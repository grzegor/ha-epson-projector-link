"""Native screen blanking, picture shortcut and HDR policy controls."""

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .controls import EpsonControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = hass.data.get(f"{DOMAIN}_controls", {}).get(entry.entry_id)
    if hub is not None:
        async_add_entities(
            [
                EpsonBlankSwitch(hub),
                EpsonBrightCinemaSwitch(hub),
                EpsonAutoHDRSwitch(hub),
            ]
        )


class EpsonBlankSwitch(EpsonControlEntity, SwitchEntity):
    _attr_icon = "mdi:projector-off"

    def __init__(self, hub):
        super().__init__(hub, "screen_blank", "Screen blanking", "MUTE")

    @property
    def is_on(self):
        return self.hub.values.get("MUTE")

    async def async_turn_on(self, **kwargs):
        await self.hub.async_set("MUTE", True, self._context)

    async def async_turn_off(self, **kwargs):
        await self.hub.async_set("MUTE", False, self._context)


class EpsonBrightCinemaSwitch(EpsonControlEntity, SwitchEntity):
    _attr_icon = "mdi:brightness-7"

    def __init__(self, hub):
        super().__init__(hub, "bright_cinema", "Bright Cinema", "CMODE")

    @property
    def is_on(self):
        return self.hub.values.get("CMODE") == "Bright Cinema"

    async def async_turn_on(self, **kwargs):
        await self.hub.async_set("CMODE", "Bright Cinema", self._context)

    async def async_turn_off(self, **kwargs):
        await self.hub.async_set("CMODE", "Cinema", self._context)


class EpsonAutoHDRSwitch(EpsonControlEntity, SwitchEntity):
    _attr_icon = "mdi:theme-light-dark"

    def __init__(self, hub):
        super().__init__(hub, "auto_hdr", "Automatic HDR by picture mode")

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.hub.auto_hdr

    async def async_turn_on(self, **kwargs):
        await self.hub.async_set_auto_hdr(True)

    async def async_turn_off(self, **kwargs):
        await self.hub.async_set_auto_hdr(False)
