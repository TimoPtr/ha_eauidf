# Copyright (c) 2026 Timothy (TimoPtr)
"""Tests for the coordinator."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import get_last_statistics
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.issue_registry import async_get as async_get_issue_reg
from homeassistant.helpers.update_coordinator import UpdateFailed
from pyeauidf.client import AuthenticationError, EauIDFError

from custom_components.eauidf.const import CONF_CONTRACTS, DOMAIN
from custom_components.eauidf.coordinator import (
    CONSECUTIVE_FAILURE_THRESHOLD,
    HISTORY_DAYS_FIRST_IMPORT,
    HISTORY_DAYS_INCREMENTAL,
    ISSUE_ID_PERSISTENT_FAILURE,
    ContractData,
    SedifCoordinator,
    is_history_start_error,
)
from tests.conftest import (
    MOCK_ACTIVE_CONTRACTS,
    MOCK_CONTRACT_NUMBER,
    MOCK_CONTRACTS,
    MOCK_PRICE_PER_M3,
    make_consumption_data,
    make_consumption_record,
)

PATCH_CLIENT = "custom_components.eauidf.coordinator.EauIDFClient"


async def test_fetch_success(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data,
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert MOCK_CONTRACT_NUMBER in coordinator.data
    data = coordinator.data[MOCK_CONTRACT_NUMBER]
    assert isinstance(data, ContractData)
    record = mock_consumption_data.records[0]
    assert data.meter_reading_m3 == record.meter_reading
    assert data.daily_consumption_l == record.consumption_liters
    assert data.last_date == record.date.date()
    assert data.is_estimated == record.is_estimated


async def test_fetch_uses_latest_confirmed_reading(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Sensor state should reflect the latest non-estimated reading.

    SEDIF can revise an estimated reading downward to a lower real value.
    Because meter_reading is total_increasing, tracking the estimated tail
    makes the sensor decrease. See TimoPtr/ha_eauidf#26.
    """
    confirmed_old = make_consumption_record(date(2026, 6, 28), 250.0, 105.800)
    confirmed = make_consumption_record(date(2026, 6, 29), 265.0, 105.900)
    estimated = make_consumption_record(date(2026, 6, 30), 240.0, 105.927)
    estimated.is_estimated = True
    # Chronological order, latest record estimated: the realistic SEDIF case.
    # The earlier confirmed record proves selection picks the latest by date,
    # not merely the first non-estimated record.
    data = make_consumption_data([confirmed_old, confirmed, estimated])

    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=data)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    result = coordinator.data[MOCK_CONTRACT_NUMBER]
    assert result.meter_reading_m3 == confirmed.meter_reading
    assert result.daily_consumption_l == confirmed.consumption_liters
    assert result.last_date == confirmed.date.date()
    assert result.is_estimated is False


async def test_fetch_falls_back_to_latest_when_all_estimated(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """When every record is estimated, fall back to the latest one by date."""
    older = make_consumption_record(date(2026, 6, 29), 265.0, 105.900)
    older.is_estimated = True
    newer = make_consumption_record(date(2026, 6, 30), 240.0, 105.927)
    newer.is_estimated = True
    data = make_consumption_data([older, newer])

    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=data)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    result = coordinator.data[MOCK_CONTRACT_NUMBER]
    assert result.meter_reading_m3 == newer.meter_reading
    assert result.last_date == newer.date.date()
    assert result.is_estimated is True


async def test_fetch_auth_error_raises(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock(side_effect=AuthenticationError("expired"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)
    assert coordinator.last_exception.translation_domain == DOMAIN
    assert coordinator.last_exception.translation_key == "authentication_failed"


async def test_contract_auth_error_raises(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry
) -> None:
    """An auth error while fetching a contract triggers reauth, not a skip."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(side_effect=AuthenticationError("expired"))

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)
    assert coordinator.last_exception.translation_domain == DOMAIN
    assert coordinator.last_exception.translation_key == "authentication_failed"


async def test_fetch_api_error_raises(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock(side_effect=EauIDFError("api down"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.last_exception.translation_domain == DOMAIN
    assert coordinator.last_exception.translation_key == "update_failed"


async def test_fetch_unexpected_error_raises(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry
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
    assert coordinator.last_exception.translation_domain == DOMAIN
    assert coordinator.last_exception.translation_key == "unexpected_error"


async def test_fetch_empty_records_raises(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry
) -> None:
    """When all contracts return no records, UpdateFailed is raised."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=make_consumption_data([]))

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.last_exception.translation_domain == DOMAIN
    assert coordinator.last_exception.translation_key == "update_failed"


async def test_client_closed_on_success(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data,
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    client.close.assert_called_once()


async def test_client_closed_on_error(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock(side_effect=EauIDFError("fail"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    client.close.assert_called_once()


async def test_repair_issue_created_after_consecutive_failures(
    recorder_mock: Recorder, hass: HomeAssistant, mock_config_entry
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
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data,
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
    success_client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    success_client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data)

    with patch(PATCH_CLIENT, return_value=success_client):
        await coordinator.async_refresh()

    issue_reg = async_get_issue_reg(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ISSUE_ID_PERSISTENT_FAILURE)
    assert issue is None


# ---------------------------------------------------------------------------
# Statistics import
# ---------------------------------------------------------------------------

STAT_ID = f"{DOMAIN}:{MOCK_CONTRACT_NUMBER}_water_consumption"
COST_STAT_ID = f"{DOMAIN}:{MOCK_CONTRACT_NUMBER}_water_cost"


async def test_first_import_fetches_90_days(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data_list,
) -> None:
    """First import (no existing stats) should request 90 days of history."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data_list)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    call_kwargs = client.get_daily_consumption.call_args.kwargs
    today = datetime.now(UTC).date()
    assert call_kwargs["start_date"] == today - timedelta(
        days=HISTORY_DAYS_FIRST_IMPORT
    )


async def test_incremental_import_fetches_7_days(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data_list,
) -> None:
    """After first import, subsequent calls should use 7 days."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data_list)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        client.get_daily_consumption.reset_mock()
        await coordinator.async_refresh()

    call_kwargs = client.get_daily_consumption.call_args.kwargs
    today = datetime.now(UTC).date()
    expected = today - timedelta(days=HISTORY_DAYS_INCREMENTAL)
    assert call_kwargs["start_date"] <= expected


async def test_statistics_values_correct(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data_list,
) -> None:
    """Verify sum=meter_reading and state=consumption_liters/1000."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data_list)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    await hass.async_block_till_done()
    await recorder_mock.async_block_till_done()

    stats = await hass.async_add_executor_job(
        get_last_statistics,
        hass,
        1,
        STAT_ID,
        True,  # noqa: FBT003
        {"sum", "state"},
    )
    assert STAT_ID in stats
    last = stats[STAT_ID][0]
    last_record = mock_consumption_data_list.records[-1]
    assert last["sum"] == pytest.approx(last_record.meter_reading)
    assert last["state"] == pytest.approx(last_record.consumption_liters / 1000)


async def test_cost_statistics_inserted(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data_list,
) -> None:
    """Verify cost statistics are inserted with cumulative sum."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data_list)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    await hass.async_block_till_done()
    await recorder_mock.async_block_till_done()

    stats = await hass.async_add_executor_job(
        get_last_statistics,
        hass,
        1,
        COST_STAT_ID,
        True,  # noqa: FBT003
        {"sum", "state"},
    )
    assert COST_STAT_ID in stats
    last = stats[COST_STAT_ID][0]
    records = mock_consumption_data_list.records
    expected_sum = sum(
        (r.consumption_liters / 1000) * MOCK_PRICE_PER_M3 for r in records
    )
    assert last["sum"] == pytest.approx(expected_sum)
    last_record = records[-1]
    expected_state = (last_record.consumption_liters / 1000) * MOCK_PRICE_PER_M3
    assert last["state"] == pytest.approx(expected_state)


async def test_statistics_failure_does_not_break_sensors(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data_list,
) -> None:
    """If statistics insertion fails, sensor data should still be available."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data_list)

    with (
        patch(PATCH_CLIENT, return_value=client),
        patch(
            "custom_components.eauidf.coordinator.async_add_external_statistics",
            side_effect=RuntimeError("recorder broken"),
        ),
    ):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert MOCK_CONTRACT_NUMBER in coordinator.data


async def test_statistics_read_error_falls_back_to_full_import(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data_list,
) -> None:
    """If existing statistics can't be read, the full history is fetched again."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data_list)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        client.get_daily_consumption.reset_mock()
        with patch(
            "custom_components.eauidf.coordinator.get_last_statistics",
            side_effect=HomeAssistantError("recorder unavailable"),
        ):
            await coordinator.async_refresh()

    assert coordinator.last_update_success
    call_kwargs = client.get_daily_consumption.call_args.kwargs
    today = datetime.now(UTC).date()
    assert call_kwargs["start_date"] == today - timedelta(
        days=HISTORY_DAYS_FIRST_IMPORT
    )


async def test_one_contract_failure_keeps_other_contracts(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data,
) -> None:
    """A contract that fails to fetch does not prevent the others from updating."""
    broken = {"id": "CONTRACT_BROKEN", "number": "7654321"}
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_CONTRACTS: [*MOCK_CONTRACTS, broken]},
    )

    async def get_daily_consumption(**kwargs):
        if kwargs["contract"].contract_id == broken["id"]:
            msg = "contract unavailable"
            raise EauIDFError(msg)
        return mock_consumption_data

    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(side_effect=get_daily_consumption)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert MOCK_CONTRACT_NUMBER in coordinator.data
    assert broken["number"] not in coordinator.data


async def test_session_reused_across_updates(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data,
) -> None:
    """One dedicated session is created per coordinator and left to HA to clean up."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data)
    session = MagicMock()
    session.close = AsyncMock()

    with (
        patch(PATCH_CLIENT, return_value=client),
        patch(
            "custom_components.eauidf.coordinator.async_create_clientsession",
            return_value=session,
        ) as create_session,
    ):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    create_session.assert_called_once()
    session.close.assert_not_awaited()


async def test_empty_contract_list_keeps_stored_contracts(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data,
) -> None:
    """An empty contract list is treated as a failed update, not as all closed."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=[])
    client.get_daily_consumption = AsyncMock(return_value=mock_consumption_data)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert coordinator.last_exception.translation_key == "update_failed"
    assert mock_config_entry.data[CONF_CONTRACTS] == MOCK_CONTRACTS


# Server-side NullPointerException raised by SEDIF's getHistoriqueIndex2 when
# the requested start date is at or before the contract's first reading.
# See TimoPtr/ha_eauidf#44.
HISTORY_NULL_ERROR = (
    "LTN015_ICL_ContratConsoHisto.getData failed: "
    "[{'exceptionType': 'System.NullPointerException', "
    "'isUserDefinedException': False, "
    "'message': 'Attempt to de-reference a null object', "
    "'stackTrace': 'Class.WS020_SE_GestionIndex.getHistoriqueIndex2: "
    "line 579, column 1\\n"
    "Class.LTN015_ICL_ContratConsoHisto.getData: line 188, column 1'}]"
)


def test_is_history_start_error_matches_null_pointer() -> None:
    """The SEDIF getHistoriqueIndex2 NullPointerException is recognised."""
    assert is_history_start_error(EauIDFError(HISTORY_NULL_ERROR))
    assert is_history_start_error(EauIDFError("System.NullPointerException"))
    assert not is_history_start_error(EauIDFError("contract unavailable"))


async def test_history_start_error_clamps_to_first_reading(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
    mock_consumption_data,
) -> None:
    """A meter with less history than requested still imports its data.

    SEDIF rejects a start date at or before the first reading with a
    server-side NullPointerException. The coordinator must clamp the start to
    the earliest accepted date instead of failing. See TimoPtr/ha_eauidf#44.
    """
    mock_config_entry.add_to_hass(hass)
    today = datetime.now(UTC).date()
    # Any start on or before the first reading is rejected; later ones succeed.
    first_reading = today - timedelta(days=15)
    accepted_starts: list[date] = []

    async def get_daily_consumption(**kwargs):
        start_date = kwargs["start_date"]
        if start_date <= first_reading:
            raise EauIDFError(HISTORY_NULL_ERROR)
        accepted_starts.append(start_date)
        return mock_consumption_data

    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(side_effect=get_daily_consumption)

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert MOCK_CONTRACT_NUMBER in coordinator.data
    # The import is clamped to the first reading, keeping all available history.
    assert min(accepted_starts) == first_reading + timedelta(days=1)


async def test_history_start_error_fails_when_no_window_works(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """When even the narrowest window is rejected, the contract fetch fails."""
    mock_config_entry.add_to_hass(hass)

    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(
        side_effect=EauIDFError(HISTORY_NULL_ERROR)
    )

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    # Initial request plus the single-day probe, both rejected.
    assert client.get_daily_consumption.await_count == 2


async def test_other_errors_are_not_retried(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry,
) -> None:
    """Non history-start errors fail the contract without retrying."""
    mock_config_entry.add_to_hass(hass)

    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(
        side_effect=EauIDFError("contract unavailable")
    )

    with patch(PATCH_CLIENT, return_value=client):
        coordinator = SedifCoordinator(hass, mock_config_entry)
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert client.get_daily_consumption.await_count == 1


async def test_fetch_contract_returns_earliest_accepted_data() -> None:
    """The binary search returns the data from the earliest accepted start."""
    end = date(2026, 10, 29)
    start = end - timedelta(days=HISTORY_DAYS_FIRST_IMPORT)
    first_reading = date(2026, 10, 14)
    accepted_starts: list[date] = []

    def data_for(start_date: date):
        return make_consumption_data([make_consumption_record(start_date, 100.0, 10.0)])

    async def get_daily_consumption(**kwargs):
        start_date = kwargs["start_date"]
        if start_date <= first_reading:
            raise EauIDFError(HISTORY_NULL_ERROR)
        accepted_starts.append(start_date)
        return data_for(start_date)

    client = MagicMock()
    client.get_daily_consumption = AsyncMock(side_effect=get_daily_consumption)

    result = await SedifCoordinator._fetch_contract(
        client, MOCK_ACTIVE_CONTRACTS[0], start, end
    )

    earliest = first_reading + timedelta(days=1)
    assert result.records[0].date.date() == earliest
    assert min(accepted_starts) == earliest


async def test_fetch_contract_probe_other_error_propagates() -> None:
    """A non history-start error while probing propagates immediately."""
    end = date(2026, 10, 29)
    start = end - timedelta(days=HISTORY_DAYS_FIRST_IMPORT)

    async def get_daily_consumption(**kwargs):
        if kwargs["start_date"] == start:
            raise EauIDFError(HISTORY_NULL_ERROR)
        msg = "backend exploded"
        raise EauIDFError(msg)

    client = MagicMock()
    client.get_daily_consumption = AsyncMock(side_effect=get_daily_consumption)

    with pytest.raises(EauIDFError, match="backend exploded"):
        await SedifCoordinator._fetch_contract(
            client, MOCK_ACTIVE_CONTRACTS[0], start, end
        )

    # Initial attempt plus the probe, which failed with a different error.
    assert client.get_daily_consumption.await_count == 2


async def test_fetch_contract_retry_other_error_propagates() -> None:
    """A non history-start error during the binary search propagates."""
    end = date(2026, 10, 29)
    start = end - timedelta(days=HISTORY_DAYS_FIRST_IMPORT)
    data = make_consumption_data([make_consumption_record(end, 100.0, 10.0)])

    async def get_daily_consumption(**kwargs):
        start_date = kwargs["start_date"]
        if start_date == start:
            raise EauIDFError(HISTORY_NULL_ERROR)
        if start_date == end:
            return data
        msg = "backend exploded"
        raise EauIDFError(msg)

    client = MagicMock()
    client.get_daily_consumption = AsyncMock(side_effect=get_daily_consumption)

    with pytest.raises(EauIDFError, match="backend exploded"):
        await SedifCoordinator._fetch_contract(
            client, MOCK_ACTIVE_CONTRACTS[0], start, end
        )
