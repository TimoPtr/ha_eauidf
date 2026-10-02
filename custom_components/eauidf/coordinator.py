# Copyright (c) 2026 Timothy (TimoPtr)
"""Data update coordinator for L'eau d'Ile-de-France."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    StatisticsRow,
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfVolume
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter
from pyeauidf import Contract, EauIDFClient
from pyeauidf.client import (
    AuthenticationError,
    ConsumptionData,
    ConsumptionRecord,
    EauIDFError,
)

from .api import contracts_to_data, entry_contracts
from .const import CONF_CONTRACTS, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=6)
CONSECUTIVE_FAILURE_THRESHOLD = 3
ISSUE_ID_PERSISTENT_FAILURE = "persistent_update_failure"
HISTORY_DAYS_FIRST_IMPORT = 90
HISTORY_DAYS_INCREMENTAL = 7


def is_current_contract_device(entry: ConfigEntry, device: dr.DeviceEntry) -> bool:
    """Return whether the device belongs to a contract currently on the account."""
    return any(
        (DOMAIN, contract.number) in device.identifiers
        for contract in entry_contracts(entry)
    )


def async_remove_stale_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Remove devices that no longer match a contract on the account.

    This covers contracts closed on the SEDIF side and devices left over from
    versions that identified contracts by their opaque API id, which changes
    over time, instead of the contract number.
    """
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not is_current_contract_device(entry, device):
            dev_reg.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


@dataclass
class ContractData:
    """Consumption data for a single contract."""

    meter_reading_m3: float
    daily_consumption_l: float
    last_date: date
    is_estimated: bool


type FetchResult = tuple[SedifData, dict[str, ConsumptionData]]
type SedifData = dict[str, ContractData]


class SedifCoordinator(DataUpdateCoordinator[SedifData]):
    """Coordinator to fetch water consumption from SEDIF."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the SEDIF coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.config_entry = entry
        self._consecutive_failures = 0
        # A dedicated session keeps the SEDIF login cookies out of the shared one.
        # Created during entry setup, HA detaches it when the entry is unloaded.
        self._session = async_create_clientsession(hass)

    async def _async_update_data(self) -> SedifData:
        """Fetch data for all contracts."""
        username = self.config_entry.data[CONF_USERNAME]
        password = self.config_entry.data[CONF_PASSWORD]

        client = EauIDFClient(username, password, session=self._session)
        try:
            await client.login()
            contracts = await self._async_refresh_contracts(client)
            start_date = await self._compute_start_date(contracts)
            sensor_data, all_data = await self._fetch_all(client, contracts, start_date)
        except AuthenticationError as err:
            self._on_failure()
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="authentication_failed",
            ) from err
        except EauIDFError as err:
            self._on_failure()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except Exception as err:
            self._on_failure()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unexpected_error",
                translation_placeholders={"error": str(err)},
            ) from err
        else:
            self._on_success()
            await self._insert_statistics(all_data)
            return sensor_data
        finally:
            await client.close()

    async def _async_refresh_contracts(self, client: EauIDFClient) -> list[Contract]:
        """Update the stored contracts from the account and drop removed ones."""
        contracts = await client.get_active_contracts()
        if not contracts:
            # Most likely a portal glitch: keep the known contracts and devices.
            msg = "No active contracts returned"
            raise EauIDFError(msg)
        if contracts != entry_contracts(self.config_entry):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_CONTRACTS: contracts_to_data(contracts),
                },
            )
            async_remove_stale_devices(self.hass, self.config_entry)
        return contracts

    async def _compute_start_date(
        self,
        contracts: list[Contract],
    ) -> date:
        """Determine how far back to fetch based on existing statistics."""
        today = datetime.now(UTC).date()
        earliest = today - timedelta(days=HISTORY_DAYS_INCREMENTAL)

        for contract in contracts:
            statistic_id = f"{DOMAIN}:{contract.number}_water_consumption"
            last_row = await self._async_get_last_statistic(statistic_id, set())
            if last_row is None:
                _LOGGER.debug(
                    "No existing statistics for %s, importing %d days",
                    statistic_id,
                    HISTORY_DAYS_FIRST_IMPORT,
                )
                return today - timedelta(days=HISTORY_DAYS_FIRST_IMPORT)

            contract_date = datetime.fromtimestamp(last_row["start"], tz=UTC).date()
            earliest = min(earliest, contract_date)

        _LOGGER.debug("Fetching consumption data from %s", earliest)
        return earliest

    async def _async_get_last_statistic(
        self,
        statistic_id: str,
        types: set[Literal["last_reset", "max", "mean", "min", "state", "sum"]],
    ) -> StatisticsRow | None:
        """Return the most recent row of a statistic, or None if there is none."""
        try:
            last_stat = await get_instance(self.hass).async_add_executor_job(
                get_last_statistics,
                self.hass,
                1,
                statistic_id,
                True,  # noqa: FBT003
                types,
            )
        except HomeAssistantError:
            _LOGGER.debug("Could not read statistics for %s", statistic_id)
            return None
        rows = last_stat.get(statistic_id)
        return rows[0] if rows else None

    def _on_success(self) -> None:
        """Reset failure counter and dismiss any repair issue."""
        if self._consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:
            async_delete_issue(self.hass, DOMAIN, ISSUE_ID_PERSISTENT_FAILURE)
        self._consecutive_failures = 0

    def _on_failure(self) -> None:
        """Track failures and create a repair issue after threshold."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:
            async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_ID_PERSISTENT_FAILURE,
                is_fixable=False,
                severity=IssueSeverity.WARNING,
                translation_key="persistent_update_failure",
                translation_placeholders={
                    "portal_url": "https://connexion.leaudiledefrance.fr"
                },
            )

    async def _insert_statistics(
        self,
        data_by_contract: dict[str, ConsumptionData],
    ) -> None:
        """Import consumption records as external statistics."""
        for contract_number, data in data_by_contract.items():
            try:
                await self._insert_contract_statistics(contract_number, data.records)
            except Exception:
                _LOGGER.exception(
                    "Failed to insert statistics for contract %s",
                    contract_number,
                )
            try:
                await self._insert_cost_statistics(contract_number, data)
            except Exception:
                _LOGGER.exception(
                    "Failed to insert cost statistics for contract %s",
                    contract_number,
                )

    async def _insert_contract_statistics(
        self,
        contract_number: str,
        records: list[ConsumptionRecord],
    ) -> None:
        """Import statistics for a single contract."""
        statistic_id = f"{DOMAIN}:{contract_number}_water_consumption"
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"SEDIF {contract_number} water consumption",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_class=VolumeConverter.UNIT_CLASS,
            unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        )

        last_row = await self._async_get_last_statistic(statistic_id, set())
        last_stats_time = last_row["start"] if last_row else None

        local_tz = dt_util.get_default_time_zone()
        statistics: list[StatisticData] = []
        for record in sorted(records, key=lambda r: r.date):
            if record.is_estimated:
                continue
            d = record.date.date()
            start = datetime(d.year, d.month, d.day, tzinfo=local_tz)
            if last_stats_time is not None and start.timestamp() <= last_stats_time:
                continue
            statistics.append(
                StatisticData(
                    start=start,
                    state=record.consumption_liters / 1000,
                    sum=record.meter_reading,
                )
            )

        async_add_external_statistics(self.hass, metadata, statistics)
        if statistics:
            _LOGGER.debug(
                "Inserted %d statistics for %s",
                len(statistics),
                statistic_id,
            )
        else:
            _LOGGER.debug("No new statistics to insert for %s", statistic_id)

    async def _insert_cost_statistics(
        self,
        contract_number: str,
        data: ConsumptionData,
    ) -> None:
        """Import cost statistics for a single contract."""
        statistic_id = f"{DOMAIN}:{contract_number}_water_cost"
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"SEDIF {contract_number} water cost",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_class=None,
            unit_of_measurement="EUR",
        )

        last_row = await self._async_get_last_statistic(statistic_id, {"sum"})
        last_stats_time = last_row["start"] if last_row else None
        running_sum: float = (last_row.get("sum") or 0.0) if last_row else 0.0

        local_tz = dt_util.get_default_time_zone()
        statistics: list[StatisticData] = []
        for record in sorted(data.records, key=lambda r: r.date):
            if record.is_estimated:
                continue
            d = record.date.date()
            start = datetime(d.year, d.month, d.day, tzinfo=local_tz)
            if last_stats_time is not None and start.timestamp() <= last_stats_time:
                continue
            daily_cost = data.daily_cost(record)
            running_sum += daily_cost
            statistics.append(
                StatisticData(
                    start=start,
                    state=daily_cost,
                    sum=running_sum,
                )
            )

        async_add_external_statistics(self.hass, metadata, statistics)
        if statistics:
            _LOGGER.debug(
                "Inserted %d cost statistics for %s",
                len(statistics),
                statistic_id,
            )
        else:
            _LOGGER.debug("No new cost statistics to insert for %s", statistic_id)

    @staticmethod
    async def _fetch_all(
        client: EauIDFClient,
        contracts: list[Contract],
        start_date: date,
    ) -> FetchResult:
        """Fetch consumption data for all contracts."""
        sensor_data: SedifData = {}
        all_data: dict[str, ConsumptionData] = {}
        end = datetime.now(UTC).date()
        for contract in contracts:
            number = contract.number
            try:
                data = await client.get_daily_consumption(
                    contract=contract,
                    start_date=start_date,
                    end_date=end,
                )
                if data.records:
                    all_data[number] = data
                    # SEDIF revises estimated readings, sometimes downward to a
                    # lower real value. The meter_reading sensor is
                    # total_increasing, so back it with the latest *confirmed*
                    # reading to stay monotonic (matching the statistics import,
                    # which also skips estimated records). Fall back to the
                    # latest record overall when nothing is confirmed yet.
                    # See TimoPtr/ha_eauidf#26.
                    confirmed = [r for r in data.records if not r.is_estimated]
                    latest = max(
                        confirmed or data.records,
                        key=lambda r: r.date,
                    )
                    sensor_data[number] = ContractData(
                        meter_reading_m3=latest.meter_reading,
                        daily_consumption_l=latest.consumption_liters,
                        last_date=latest.date.date(),
                        is_estimated=latest.is_estimated,
                    )
                else:
                    _LOGGER.warning(
                        "No consumption data returned for contract %s",
                        number,
                    )
            except AuthenticationError:
                raise
            except Exception:
                _LOGGER.exception("Failed to fetch data for contract %s", number)
        if not sensor_data and contracts:
            msg = "Failed to fetch data for any contract"
            raise EauIDFError(msg)
        return sensor_data, all_data
