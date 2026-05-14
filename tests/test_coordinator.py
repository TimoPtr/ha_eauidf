"""Tests for the coordinator."""

from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pyeauidf.client import AuthenticationError, EauIDFError

from custom_components.eauidf.coordinator import ContractData, SedifCoordinator
from tests.conftest import MOCK_CONTRACT_ID

PATCH_CLIENT = "custom_components.eauidf.coordinator.EauIDFClient"


async def test_fetch_success(
    hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.get_daily_consumption.return_value = [mock_record]

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert MOCK_CONTRACT_ID in coordinator.data
    data = coordinator.data[MOCK_CONTRACT_ID]
    assert isinstance(data, ContractData)
    assert data.meter_reading_m3 == mock_record.meter_reading
    assert data.daily_consumption_l == mock_record.consumption_liters
    assert data.last_date == mock_record.date.date()
    assert data.is_estimated == mock_record.is_estimated


async def test_fetch_auth_error_raises(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login.side_effect = AuthenticationError("expired")

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)


async def test_fetch_api_error_raises(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login.side_effect = EauIDFError("api down")

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)


async def test_fetch_unexpected_error_raises(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login.side_effect = RuntimeError("unexpected")

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)


async def test_fetch_empty_records_raises(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """When all contracts return no records, UpdateFailed is raised."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.get_daily_consumption.return_value = []

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)


async def test_client_closed_on_success(
    hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.get_daily_consumption.return_value = [mock_record]

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    client.close.assert_called_once()


async def test_client_closed_on_error(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login.side_effect = EauIDFError("fail")

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    client.close.assert_called_once()
