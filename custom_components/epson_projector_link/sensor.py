"""Native diagnostic light-source usage."""

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .controls import EpsonControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = hass.data.get(f"{DOMAIN}_controls", {}).get(entry.entry_id)
    if hub is not None:
        async_add_entities([EpsonLightHours(hub)])


class EpsonLightHours(EpsonControlEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "h"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub):
        super().__init__(hub, "light_source_hours", "Light source hours", "LAMP")

    @property
    def native_value(self):
        return self.hub.values.get("LAMP")
