# Copyright (c) 2026 Timothy (TimoPtr)
"""Tests for the sensor platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.recorder import Recorder
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pyeauidf import Contract

from custom_components.eauidf.const import DOMAIN
from tests.conftest import (
    MOCK_ACTIVE_CONTRACTS,
    MOCK_CONTRACT_NUMBER,
    make_consumption_data,
)

PATCH_COORD_CLIENT = "custom_components.eauidf.coordinator.EauIDFClient"


def _make_coord_client(mock_record: MagicMock) -> MagicMock:
    """Create a mock client for the coordinator."""
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(
        return_value=make_consumption_data([mock_record])
    )
    return client


async def _setup_integration(
    hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    mock_config_entry.add_to_hass(hass)
    with (
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


async def test_sensor_without_data(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    """Sensors have no value and no attributes when the coordinator has no data."""
    await _setup_integration(hass, mock_config_entry, mock_record)

    mock_config_entry.runtime_data.async_set_updated_data({})
    await hass.async_block_till_done()

    state = _get_state(hass, mock_config_entry, "meter_reading")
    assert state.state == "unknown"
    assert "is_estimated" not in state.attributes


async def test_sensor_contract_missing_from_data(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    """Sensors have no value when their contract is absent from the update."""
    await _setup_integration(hass, mock_config_entry, mock_record)
    contract_data = mock_config_entry.runtime_data.data[MOCK_CONTRACT_NUMBER]

    mock_config_entry.runtime_data.async_set_updated_data({"7654321": contract_data})
    await hass.async_block_till_done()

    state = _get_state(hass, mock_config_entry, "meter_reading")
    assert state.state == "unknown"
    assert "is_estimated" not in state.attributes


SECOND_CONTRACT = Contract(contract_id="CONTRACT_002", number="7654321")


def _meter_entity_id(hass: HomeAssistant, entry, number: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{number}_meter_reading"
    )


async def test_new_contract_added_without_reload(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    """A contract added to the account gets a device and sensors on the next update."""
    mock_config_entry.add_to_hass(hass)
    coord_client = _make_coord_client(mock_record)
    with (
        patch(PATCH_COORD_CLIENT, return_value=coord_client),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert _meter_entity_id(hass, mock_config_entry, SECOND_CONTRACT.number) is None

        coord_client.get_active_contracts.return_value = [
            *MOCK_ACTIVE_CONTRACTS,
            SECOND_CONTRACT,
        ]
        await mock_config_entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    entity_id = _meter_entity_id(hass, mock_config_entry, SECOND_CONTRACT.number)
    assert entity_id is not None
    assert hass.states.get(entity_id).state == str(mock_record.meter_reading)
    assert any(
        (DOMAIN, SECOND_CONTRACT.number) in device.identifiers
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), mock_config_entry.entry_id
        )
    )


async def test_removed_contract_cleaned_up_without_reload(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    """A contract removed from the account loses its device and sensors."""
    mock_config_entry.add_to_hass(hass)
    coord_client = _make_coord_client(mock_record)
    coord_client.get_active_contracts.return_value = [
        *MOCK_ACTIVE_CONTRACTS,
        SECOND_CONTRACT,
    ]
    with (
        patch(PATCH_COORD_CLIENT, return_value=coord_client),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        entity_id = _meter_entity_id(hass, mock_config_entry, SECOND_CONTRACT.number)
        assert entity_id is not None

        coord_client.get_active_contracts.return_value = MOCK_ACTIVE_CONTRACTS
        await mock_config_entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert _meter_entity_id(hass, mock_config_entry, SECOND_CONTRACT.number) is None
    assert hass.states.get(entity_id) is None
    assert _meter_entity_id(hass, mock_config_entry, MOCK_CONTRACT_NUMBER)
