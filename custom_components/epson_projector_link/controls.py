"""Shared reported state and native LS500 controls on the existing connection."""

import asyncio
import logging

from homeassistant.const import STATE_ON
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.script import Script
from homeassistant.helpers.script import async_validate_actions_config
from homeassistant.helpers.storage import Store

from .const import CONF_CONTROL_ACTION
from .const import CONF_DELEGATED_PROPERTIES
from .const import CONF_HDR_AUTO_MODES
from .const import CONF_MODEL
from .const import DOMAIN
from .projector.const import COLOR_MODE_CODE_INVERTED_MAP

_LOGGER = logging.getLogger(__name__)
LS500_COLOR_MODES = ["Cinema", "Bright Cinema", "Game", "Dynamic"]
# Epson's LS500 sheet, ESC/VP21 command list Rev.W: HDR uses 20/30, not 02/03.
HDR_MODES = {"Auto": "00", "SDR": "01", "HDR10": "20", "HLG": "30"}
LS500_PROPERTIES = ["SOURCE", "CMODE", "DYNRANGE", "BRIGHT", "MUTE", "LAMP"]


def device_info(entry):
    """Use the existing projector serial identity for every platform."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer="Epson",
        model="EH-LS500" if entry.data.get(CONF_MODEL) == "ls500" else "Projector",
        name=(entry.title if entry.title != DOMAIN else "Epson projector"),
    )


class EpsonControls:
    """Observe reports; never turn an observation into a new user request."""

    def __init__(self, hass, entry, projector):
        self.hass = hass
        self.entry = entry
        self.projector = projector
        self.values = {}
        self.connected = False
        self.auto_hdr = False
        self._signal = f"{DOMAIN}_{entry.entry_id}_controls"
        self._store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.controls")
        self._hdr_lock = asyncio.Lock()
        self._hdr_task = None
        self._closed = False
        self._unsubscribe = projector.add_callback(self.update)
        self._action = None

    async def async_load(self):
        """Restore manual HDR ownership before any projector reports arrive."""
        actions = self.entry.data.get(CONF_CONTROL_ACTION)
        if actions:
            # Config-entry selectors store JSON, unlike YAML's validated actions.
            # Compile templates before Script executes the configured sequence.
            actions = await async_validate_actions_config(
                self.hass, cv.SCRIPT_SCHEMA(actions)
            )
            self._action = Script(
                self.hass, actions, f"{self.entry.title} control", DOMAIN
            )
        data = await self._store.async_load()
        self.auto_hdr = (data or {}).get(
            "auto_hdr", bool(self.entry.data.get(CONF_HDR_AUTO_MODES))
        )

    def close(self):
        self._closed = True
        self._unsubscribe()
        if self._hdr_task is not None:
            self._hdr_task.cancel()

    def notify(self):
        async_dispatcher_send(self.hass, self._signal)

    def set_connected(self, connected):
        self.connected = connected
        if not connected:
            self.values.clear()
        self.notify()

    @property
    def available(self):
        return self.connected and self.values.get("PWR") == STATE_ON

    def update(self, prop, value):
        if self._closed:
            return
        previous = self.values.get(prop)
        if prop == "PWR":
            if value != STATE_ON or previous != STATE_ON:
                self.values.clear()
            self.connected = True
        self.values[prop] = value
        self.notify()
        if (
            prop in ("CMODE", "DYNRANGE")
            and self.auto_hdr
            and self.available
            and (self._hdr_task is None or self._hdr_task.done())
        ):
            self._hdr_task = self.hass.async_create_task(self._async_auto_hdr())

    async def _async_auto_hdr(self):
        try:
            await self.async_apply_auto_hdr()
        except Exception:
            _LOGGER.warning("Unable to apply automatic dynamic range", exc_info=True)

    async def async_apply_auto_hdr(self):
        """Derive HDR only from fresh actual picture mode, while enabled and on."""
        async with self._hdr_lock:
            mode = self.values.get("CMODE")
            if not self.auto_hdr or not self.available or mode not in LS500_COLOR_MODES:
                return
            target = (
                "Auto"
                if mode in self.entry.data.get(CONF_HDR_AUTO_MODES, [])
                else "SDR"
            )
            current = await self.projector.get_property("DYNRANGE")
            # A newer report or manual request can supersede the awaited query.
            if (
                not self.auto_hdr
                or not self.available
                or mode != self.values.get("CMODE")
            ):
                return
            if current != HDR_MODES[target]:
                await self._async_write("DYNRANGE", target)

    async def async_set_auto_hdr(self, enabled):
        async with self._hdr_lock:
            self.auto_hdr = enabled
            await self._store.async_save({"auto_hdr": enabled})
            self.notify()
        if enabled:
            await self.async_apply_auto_hdr()

    async def async_set(self, prop, value, context=None):
        """Explicit requests can be delegated; reported feedback never is."""
        if not self.available:
            raise HomeAssistantError("Projector must be on and responding")
        if prop == "DYNRANGE":
            async with self._hdr_lock:
                self.auto_hdr = False
                await self._store.async_save({"auto_hdr": False})
                self.notify()
                await self._async_write(prop, value)
        elif prop in self.entry.data.get(CONF_DELEGATED_PROPERTIES, []):
            if self._action is None:
                raise HomeAssistantError(
                    "Delegated controls require a configured action"
                )
            # Always forward, even when value equals old reported state: a newer
            # request may need to cancel the controller's in-flight opposite one.
            await self._action.async_run(
                {"property": prop, "value": value}, context=context
            )
        else:
            await self._async_write(prop, value)

    async def _async_write(self, prop, value):
        if prop == "CMODE":
            wire_value = COLOR_MODE_CODE_INVERTED_MAP[value]
        elif prop == "DYNRANGE":
            wire_value = HDR_MODES[value]
        elif prop == "MUTE":
            wire_value = "ON" if value else "OFF"
        else:
            wire_value = value
        await self.projector.set_property(prop, wire_value)
        # ACK is acceptance. Only the following reported value updates entities.
        return await self.projector.get_property(prop)


class EpsonControlEntity(Entity):
    """One control in the shared physical projector device."""

    _attr_has_entity_name = True

    def __init__(self, hub, key, name, prop=None):
        self.hub = hub
        self.prop = prop
        self._attr_unique_id = f"{hub.entry.unique_id}_{key}"
        self._attr_name = name
        self._attr_device_info = device_info(hub.entry)

    @property
    def available(self):
        return self.hub.available and self.hub.values.get(self.prop) is not None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.hub._signal, self.async_write_ha_state
            )
        )
