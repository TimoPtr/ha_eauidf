"""Tests for the diagnostics platform."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.eauidf.diagnostics import async_get_config_entry_diagnostics
from tests.conftest import MOCK_CONTRACT_ID, MOCK_PASSWORD, MOCK_USERNAME

PATCH_INIT_CLIENT = "custom_components.eauidf.EauIDFClient"
PATCH_COORD_CLIENT = "custom_components.eauidf.coordinator.EauIDFClient"


async def test_diagnostics_redacts_credentials(
    hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    mock_config_entry.add_to_hass(hass)

    init_client = MagicMock()
    init_client.login = AsyncMock()
    init_client.get_contracts = AsyncMock(return_value=[MOCK_CONTRACT_ID])
    init_client.get_contract_details = AsyncMock(
        return_value={"contrat": {"Name": "9235380"}}
    )

    coord_client = MagicMock()
    coord_client.login = AsyncMock()
    coord_client.close = AsyncMock()
    coord_client.get_daily_consumption = AsyncMock(return_value=[mock_record])

    with (
        patch(PATCH_INIT_CLIENT, return_value=init_client),
        patch(PATCH_COORD_CLIENT, return_value=coord_client),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["config_entry_data"]["username"] == "**REDACTED**"
    assert result["config_entry_data"]["password"] == "**REDACTED**"
    assert MOCK_USERNAME not in str(result)
    assert MOCK_PASSWORD not in str(result)

    assert MOCK_CONTRACT_ID in result["coordinator_data"]
    contract_data = result["coordinator_data"][MOCK_CONTRACT_ID]
    assert contract_data["meter_reading_m3"] == mock_record.meter_reading
    assert contract_data["daily_consumption_l"] == mock_record.consumption_liters
    assert contract_data["is_estimated"] == mock_record.is_estimated
