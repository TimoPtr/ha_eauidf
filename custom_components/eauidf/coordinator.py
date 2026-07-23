"""Data update coordinator for L'eau d'Ile-de-France."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance  # type: ignore[attr-defined]
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfVolume
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter
from pyeauidf import EauIDFClient
from pyeauidf.client import (
    AuthenticationError,
    ConsumptionData,
    ConsumptionRecord,
    EauIDFError,
)

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

    async def _async_update_data(self) -> SedifData:
        """Fetch data for all contracts."""
        username = self.config_entry.data[CONF_USERNAME]
        password = self.config_entry.data[CONF_PASSWORD]
        contracts = self.config_entry.data[CONF_CONTRACTS]

        start_date = await self._compute_start_date(contracts)

        client = EauIDFClient(
            username, password, session=async_create_clientsession(self.hass)
        )
        try:
            sensor_data, all_data = await self._fetch_all(client, contracts, start_date)
        except AuthenticationError as err:
            self._on_failure()
            raise ConfigEntryAuthFailed(str(err)) from err
        except EauIDFError as err:
            self._on_failure()
            msg = f"Error fetching SEDIF data: {err}"
            raise UpdateFailed(msg) from err
        except Exception as err:
            self._on_failure()
            msg = f"Unexpected error fetching SEDIF data: {err}"
            raise UpdateFailed(msg) from err
        else:
            self._on_success()
            await self._insert_statistics(all_data)
            return sensor_data
        finally:
            await client.close()

    async def _compute_start_date(
        self,
        contracts: list[dict[str, str]],
    ) -> date:
        """Determine how far back to fetch based on existing statistics."""
        today = datetime.now(UTC).date()
        earliest = today - timedelta(days=HISTORY_DAYS_INCREMENTAL)

        for contract in contracts:
            statistic_id = f"{DOMAIN}:{contract['number']}_water_consumption"
            try:
                last_stat: dict[str, list[dict[str, Any]]] = await get_instance(
                    self.hass
                ).async_add_executor_job(
                    get_last_statistics,  # type: ignore[arg-type]
                    self.hass,
                    1,
                    statistic_id,
                    True,  # noqa: FBT003
                    set(),
                )
            except HomeAssistantError:
                last_stat = {}
            if not last_stat:
                _LOGGER.debug(
                    "No existing statistics for %s, importing %d days",
                    statistic_id,
                    HISTORY_DAYS_FIRST_IMPORT,
                )
                return today - timedelta(days=HISTORY_DAYS_FIRST_IMPORT)

            last_start = last_stat[statistic_id][0]["start"]
            if isinstance(last_start, (int, float)):
                contract_date = datetime.fromtimestamp(last_start, tz=UTC).date()
            else:
                contract_date = last_start.date()
            earliest = min(earliest, contract_date)

        _LOGGER.debug("Fetching consumption data from %s", earliest)
        return earliest

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

        try:
            last_stat: dict[str, list[dict[str, Any]]] = await get_instance(
                self.hass
            ).async_add_executor_job(
                get_last_statistics,  # type: ignore[arg-type]
                self.hass,
                1,
                statistic_id,
                True,  # noqa: FBT003
                set(),
            )
        except HomeAssistantError:
            _LOGGER.debug("No existing statistics for %s", statistic_id)
            last_stat = {}
        last_stats_time: float | None = None
        if last_stat:
            raw_start = last_stat[statistic_id][0]["start"]
            if isinstance(raw_start, (int, float)):
                last_stats_time = raw_start
            else:
                last_stats_time = raw_start.timestamp()

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

        last_stats_time: float | None = None
        running_sum: float = 0.0
        try:
            last_stat: dict[str, list[dict[str, Any]]] = await get_instance(
                self.hass
            ).async_add_executor_job(
                get_last_statistics,  # type: ignore[arg-type]
                self.hass,
                1,
                statistic_id,
                True,  # noqa: FBT003
                {"sum"},
            )
        except HomeAssistantError:
            _LOGGER.debug("No existing cost statistics for %s", statistic_id)
            last_stat = {}
        if last_stat:
            raw_start = last_stat[statistic_id][0]["start"]
            if isinstance(raw_start, (int, float)):
                last_stats_time = raw_start
            else:
                last_stats_time = raw_start.timestamp()
            running_sum = last_stat[statistic_id][0].get("sum", 0.0) or 0.0

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
        contracts: list[dict[str, str]],
        start_date: date,
    ) -> FetchResult:
        """Fetch consumption data for all contracts."""
        await client.login()
        sensor_data: SedifData = {}
        all_data: dict[str, ConsumptionData] = {}
        end = datetime.now(UTC).date()
        for contract in contracts:
            cid = contract["id"]
            number = contract["number"]
            try:
                data = await client.get_daily_consumption(
                    contract_id=cid, start_date=start_date, end_date=end
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
