"""Tests for the coordinator."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.issue_registry import async_get as async_get_issue_reg
from homeassistant.helpers.update_coordinator import UpdateFailed
from pyeauidf.client import AuthenticationError, EauIDFError

from custom_components.eauidf.const import DOMAIN
from custom_components.eauidf.coordinator import (
    CONSECUTIVE_FAILURE_THRESHOLD,
    ISSUE_ID_PERSISTENT_FAILURE,
    ContractData,
    SedifCoordinator,
)
from tests.conftest import MOCK_CONTRACT_NUMBER

PATCH_CLIENT = "custom_components.eauidf.coordinator.EauIDFClient"


async def test_fetch_success(
    hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_daily_consumption = AsyncMock(return_value=[mock_record])

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert MOCK_CONTRACT_NUMBER in coordinator.data
    data = coordinator.data[MOCK_CONTRACT_NUMBER]
    assert isinstance(data, ContractData)
    assert data.meter_reading_m3 == mock_record.meter_reading
    assert data.daily_consumption_l == mock_record.consumption_liters
    assert data.last_date == mock_record.date.date()
    assert data.is_estimated == mock_record.is_estimated


async def test_fetch_auth_error_raises(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock(side_effect=AuthenticationError("expired"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)


async def test_fetch_api_error_raises(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock(side_effect=EauIDFError("api down"))
    client.close = AsyncMock()

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
    client.login = AsyncMock(side_effect=RuntimeError("unexpected"))
    client.close = AsyncMock()

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
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_daily_consumption = AsyncMock(return_value=[])

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
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_daily_consumption = AsyncMock(return_value=[mock_record])

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    client.close.assert_called_once()


async def test_client_closed_on_error(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock(side_effect=EauIDFError("fail"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    client.close.assert_called_once()


async def test_repair_issue_created_after_consecutive_failures(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock(side_effect=EauIDFError("down"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
            await coordinator.async_refresh()

    issue_reg = async_get_issue_reg(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ISSUE_ID_PERSISTENT_FAILURE)
    assert issue is not None


async def test_repair_issue_dismissed_on_success(
    hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    mock_config_entry.add_to_hass(hass)
    failing_client = MagicMock()
    failing_client.login = AsyncMock(side_effect=EauIDFError("down"))
    failing_client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=failing_client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
            await coordinator.async_refresh()

    success_client = MagicMock()
    success_client.login = AsyncMock()
    success_client.close = AsyncMock()
    success_client.get_daily_consumption = AsyncMock(return_value=[mock_record])

    with patch(PATCH_CLIENT, return_value=success_client):
        await coordinator.async_refresh()

    issue_reg = async_get_issue_reg(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ISSUE_ID_PERSISTENT_FAILURE)
    assert issue is None
