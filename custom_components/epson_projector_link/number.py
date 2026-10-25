"""Native picture brightness control (not laser output)."""

from homeassistant.components.number import NumberEntity
from homeassistant.components.number import NumberMode

from .const import DOMAIN
from .controls import EpsonControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = hass.data.get(f"{DOMAIN}_controls", {}).get(entry.entry_id)
    if hub is not None:
        async_add_entities([EpsonBrightness(hub)])


class EpsonBrightness(EpsonControlEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-6"

    def __init__(self, hub):
        super().__init__(hub, "brightness", "Picture brightness", "BRIGHT")

    @property
    def native_value(self):
        return self.hub.values.get("BRIGHT")

    async def async_set_native_value(self, value):
        if not 0 <= value <= 255 or value != int(value):
            raise ValueError("Picture brightness must be an integer from 0 to 255")
        await self.hub.async_set("BRIGHT", int(value), self._context)
