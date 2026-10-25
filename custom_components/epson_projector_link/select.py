"""Native picture and HDR mode selectors."""

from homeassistant.components.select import SelectEntity

from .const import DOMAIN
from .controls import EpsonControlEntity
from .controls import HDR_MODES
from .controls import LS500_COLOR_MODES


async def async_setup_entry(hass, entry, async_add_entities):
    hub = hass.data.get(f"{DOMAIN}_controls", {}).get(entry.entry_id)
    if hub is not None:
        async_add_entities(
            [
                EpsonModeSelect(
                    hub, "color_mode", "Picture mode", "CMODE", LS500_COLOR_MODES
                ),
                EpsonModeSelect(
                    hub, "dynamic_range", "HDR mode", "DYNRANGE", list(HDR_MODES)
                ),
            ]
        )


class EpsonModeSelect(EpsonControlEntity, SelectEntity):
    def __init__(self, hub, key, name, prop, options):
        super().__init__(hub, key, name, prop)
        self._attr_options = options
        self._attr_icon = (
            "mdi:palette" if prop == "CMODE" else "mdi:video-high-definition"
        )

    @property
    def current_option(self):
        value = self.hub.values.get(self.prop)
        if self.prop == "DYNRANGE":
            return next(
                (name for name, code in HDR_MODES.items() if code == value), None
            )
        return value if value in self.options else None

    async def async_select_option(self, option):
        await self.hub.async_set(self.prop, option, self._context)
