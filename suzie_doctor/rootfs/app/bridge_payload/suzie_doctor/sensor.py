from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BRIDGE_VERSION


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SuzieDoctorBridgeSensor()])


class SuzieDoctorBridgeSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Bridge"
    _attr_unique_id = "suzie_doctor_bridge"
    _attr_icon = "mdi:stethoscope"

    @property
    def native_value(self) -> str:
        return "ready"

    @property
    def extra_state_attributes(self):
        return {"bridge_version": BRIDGE_VERSION}
