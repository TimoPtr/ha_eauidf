# Copyright (c) 2026 Timothy
"""Tests for the sensor platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.recorder import Recorder
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.eauidf.const import DOMAIN
from tests.conftest import (
    MOCK_CONTRACT_NUMBER,
    MOCK_CONTRACTS,
    make_consumption_data,
)

PATCH_INIT_CLIENT = "custom_components.eauidf.EauIDFClient"
PATCH_COORD_CLIENT = "custom_components.eauidf.coordinator.EauIDFClient"


def _make_init_client() -> MagicMock:
    """Create a mock client for the __init__.py contract refresh."""
    client = MagicMock()
    client.login = AsyncMock()
    client.get_contracts = AsyncMock(return_value=[MOCK_CONTRACTS[0]["id"]])
    client.get_contract_details = AsyncMock(
        return_value={"contrat": {"Name": MOCK_CONTRACT_NUMBER}}
    )
    return client


def _make_coord_client(mock_record: MagicMock) -> MagicMock:
    """Create a mock client for the coordinator."""
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_daily_consumption = AsyncMock(
        return_value=make_consumption_data([mock_record])
    )
    return client


async def _setup_integration(
    hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    mock_config_entry.add_to_hass(hass)
    with (
        patch(PATCH_INIT_CLIENT, return_value=_make_init_client()),
        patch(PATCH_COORD_CLIENT, return_value=_make_coord_client(mock_record)),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()


def _get_state(hass: HomeAssistant, mock_config_entry, key: str):
    ent_reg = er.async_get(hass)
    unique_id = f"{mock_config_entry.entry_id}_{MOCK_CONTRACT_NUMBER}_{key}"
    entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None, f"Entity not found for unique_id: {unique_id}"
    return hass.states.get(entity_id)


async def test_all_sensors_created(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)

    ent_reg = er.async_get(hass)
    for key in ("meter_reading", "daily_consumption", "last_reading_date"):
        unique_id = f"{mock_config_entry.entry_id}_{MOCK_CONTRACT_NUMBER}_{key}"
        entry = ent_reg.async_get(
            ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        )
        assert entry is not None, f"Sensor {key} was not created"


async def test_meter_reading_state(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)
    state = _get_state(hass, mock_config_entry, "meter_reading")

    assert float(state.state) == mock_record.meter_reading
    assert state.attributes["unit_of_measurement"] == UnitOfVolume.CUBIC_METERS
    assert state.attributes["device_class"] == SensorDeviceClass.WATER
    assert state.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING


async def test_daily_consumption_state(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)
    state = _get_state(hass, mock_config_entry, "daily_consumption")

    assert float(state.state) == mock_record.consumption_liters
    assert state.attributes["unit_of_measurement"] == UnitOfVolume.LITERS
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert "device_class" not in state.attributes


async def test_last_reading_date_disabled_by_default(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)

    ent_reg = er.async_get(hass)
    unique_id = f"{mock_config_entry.entry_id}_{MOCK_CONTRACT_NUMBER}_last_reading_date"
    entry = ent_reg.async_get(ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id))
    assert entry is not None
    assert entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION


async def test_last_reading_date_state(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)

    ent_reg = er.async_get(hass)
    unique_id = f"{mock_config_entry.entry_id}_{MOCK_CONTRACT_NUMBER}_last_reading_date"
    ent_reg.async_update_entity(
        ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id),
        disabled_by=None,
    )
    await hass.async_block_till_done()

    with (
        patch(PATCH_INIT_CLIENT, return_value=_make_init_client()),
        patch(
            PATCH_COORD_CLIENT,
            return_value=_make_coord_client(mock_record),
        ),
    ):
        await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state = _get_state(hass, mock_config_entry, "last_reading_date")
    assert state.state == "2026-05-13"
    assert state.attributes["device_class"] == SensorDeviceClass.DATE


async def test_extra_attributes(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)
    state = _get_state(hass, mock_config_entry, "meter_reading")

    assert state.attributes["last_reading_date"] == "2026-05-13"
    assert state.attributes["is_estimated"] is False


async def test_sensor_unavailable_when_no_data(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)

    coordinator = mock_config_entry.runtime_data
    coordinator.data = {}
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = _get_state(hass, mock_config_entry, "meter_reading")
    assert state.state in ("unknown", "unavailable", "None")


async def test_unload_entry(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    await _setup_integration(hass, mock_config_entry, mock_record)
    assert mock_config_entry.runtime_data is not None

    result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
