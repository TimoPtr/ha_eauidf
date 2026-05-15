"""Data update coordinator for L'eau d'Ile-de-France."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyeauidf import EauIDFClient
from pyeauidf.client import AuthenticationError, EauIDFError

from .const import CONF_CONTRACTS, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=6)
CONSECUTIVE_FAILURE_THRESHOLD = 3
ISSUE_ID_PERSISTENT_FAILURE = "persistent_update_failure"


@dataclass
class ContractData:
    """Consumption data for a single contract."""

    meter_reading_m3: float
    daily_consumption_l: float
    last_date: date
    is_estimated: bool


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

        client = EauIDFClient(
            username, password, session=async_create_clientsession(self.hass)
        )
        try:
            data = await self._fetch_all(client, contracts)
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
            return data
        finally:
            await client.close()

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

    @staticmethod
    async def _fetch_all(
        client: EauIDFClient,
        contracts: list[dict[str, str]],
    ) -> SedifData:
        """Fetch consumption data for all contracts."""
        await client.login()
        data: SedifData = {}
        for contract in contracts:
            cid = contract["id"]
            number = contract["number"]
            try:
                end = datetime.now(UTC).date()
                start = end - timedelta(days=7)
                records = await client.get_daily_consumption(
                    contract_id=cid, start_date=start, end_date=end
                )
                if records:
                    latest = records[-1]
                    data[cid] = ContractData(
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
        if not data and contracts:
            msg = "Failed to fetch data for any contract"
            raise EauIDFError(msg)
        return data
