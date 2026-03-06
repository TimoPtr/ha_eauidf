"""Sensor platform for L'eau d'Ile-de-France."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CONTRACTS, DOMAIN
from .coordinator import ContractData, SedifCoordinator


@dataclass(frozen=True, kw_only=True)
class SedifSensorDescription(SensorEntityDescription):
    """Describe a SEDIF sensor."""

    value_fn: Callable[[ContractData], Any]
    has_extra_attributes: bool = False


SENSOR_TYPES: tuple[SedifSensorDescription, ...] = (
    SedifSensorDescription(
        key="meter_reading",
        translation_key="meter_reading",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        icon="mdi:counter",
        suggested_display_precision=0,
        value_fn=lambda d: d.meter_reading_m3,
        has_extra_attributes=True,
    ),
    SedifSensorDescription(
        key="daily_consumption",
        translation_key="daily_consumption",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        icon="mdi:water",
        suggested_display_precision=0,
        value_fn=lambda d: d.daily_consumption_l,
        has_extra_attributes=True,
    ),
    SedifSensorDescription(
        key="last_reading_date",
        translation_key="last_reading_date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.last_date,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: SedifCoordinator = hass.data[DOMAIN][entry.entry_id]
    contracts = entry.data[CONF_CONTRACTS]

    entities: list[SedifSensor] = []
    for contract in contracts:
        for description in SENSOR_TYPES:
            entities.append(
                SedifSensor(
                    coordinator=coordinator,
                    description=description,
                    contract_id=contract["id"],
                    contract_number=contract["number"],
                    entry_id=entry.entry_id,
                )
            )

    async_add_entities(entities)


class SedifSensor(CoordinatorEntity[SedifCoordinator], SensorEntity):
    """Representation of a SEDIF water sensor."""

    entity_description: SedifSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SedifCoordinator,
        description: SedifSensorDescription,
        contract_id: str,
        contract_number: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._contract_id = contract_id
        self._attr_unique_id = f"{entry_id}_{contract_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_id)},
            name=f"SEDIF Contract {contract_number}",
            manufacturer="SEDIF",
            model="Water Meter",
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if not self.coordinator.data:
            return None
        contract_data = self.coordinator.data.get(self._contract_id)
        if contract_data is None:
            return None
        return self.entity_description.value_fn(contract_data)

    @property
    def extra_state_attributes(self) -> dict[str, str | bool] | None:
        """Return extra state attributes."""
        if not self.entity_description.has_extra_attributes:
            return None
        if not self.coordinator.data:
            return None
        contract_data = self.coordinator.data.get(self._contract_id)
        if contract_data is None:
            return None
        return {
            "last_reading_date": contract_data.last_date.isoformat(),
            "is_estimated": contract_data.is_estimated,
        }
