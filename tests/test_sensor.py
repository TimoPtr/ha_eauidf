"""Tests for the sensor platform."""

from unittest.mock import MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.eauidf.const import DOMAIN
from tests.conftest import MOCK_CONTRACT_ID

PATCH_CLIENT = "custom_components.eauidf.coordinator.EauIDFClient"


async def _setup_integration(hass: HomeAssistant, mock_config_entry, mock_record) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.get_daily_consumption.return_value = [mock_record]
    with patch(PATCH_CLIENT, return_value=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()


def _get_state(hass: HomeAssistant, mock_config_entry, key: str):
    ent_reg = er.async_get(hass)
    unique_id = f"{mock_config_entry.entry_id}_{MOCK_CONTRACT_ID}_{key}"
    entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None, f"Entity not found for unique_id: {unique_id}"
    return hass.states.get(entity_id)


async def test_all_sensors_created(hass: HomeAssistant, mock_config_entry, mock_record) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)

    for key in ("meter_reading", "daily_consumption", "last_reading_date"):
        state = _get_state(hass, mock_config_entry, key)
        assert state is not None, f"Sensor {key} was not created"


async def test_meter_reading_state(hass: HomeAssistant, mock_config_entry, mock_record) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)
    state = _get_state(hass, mock_config_entry, "meter_reading")

    assert float(state.state) == mock_record.meter_reading
    assert state.attributes["unit_of_measurement"] == UnitOfVolume.CUBIC_METERS
    assert state.attributes["device_class"] == SensorDeviceClass.WATER
    assert state.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING


async def test_daily_consumption_state(hass: HomeAssistant, mock_config_entry, mock_record) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)
    state = _get_state(hass, mock_config_entry, "daily_consumption")

    assert float(state.state) == mock_record.consumption_liters
    assert state.attributes["unit_of_measurement"] == UnitOfVolume.LITERS
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert "device_class" not in state.attributes


async def test_last_reading_date_state(hass: HomeAssistant, mock_config_entry, mock_record) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)
    state = _get_state(hass, mock_config_entry, "last_reading_date")

    assert state.state == "2026-05-13"
    assert state.attributes["device_class"] == SensorDeviceClass.DATE


async def test_extra_attributes(hass: HomeAssistant, mock_config_entry, mock_record) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)
    state = _get_state(hass, mock_config_entry, "meter_reading")

    assert state.attributes["last_reading_date"] == "2026-05-13"
    assert state.attributes["is_estimated"] is False


async def test_sensor_unavailable_when_no_data(hass: HomeAssistant, mock_config_entry, mock_record) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    coordinator.data = {}
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = _get_state(hass, mock_config_entry, "meter_reading")
    assert state.state in ("unknown", "unavailable", "None")


async def test_unload_entry(hass: HomeAssistant, mock_config_entry, mock_record) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)
    assert mock_config_entry.entry_id in hass.data[DOMAIN]

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})
