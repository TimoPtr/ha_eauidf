"""Data update coordinator for L'eau d'Ile-de-France."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyeauidf import EauIDFClient
from pyeauidf.client import AuthenticationError, EauIDFError

from .const import CONF_CONTRACTS, DOMAIN

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=6)


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
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.config_entry = entry

    async def _async_update_data(self) -> SedifData:
        """Fetch data for all contracts."""
        username = self.config_entry.data[CONF_USERNAME]
        password = self.config_entry.data[CONF_PASSWORD]
        contracts = self.config_entry.data[CONF_CONTRACTS]

        try:
            return await self.hass.async_add_executor_job(
                self._fetch_all, username, password, contracts
            )
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EauIDFError as err:
            raise UpdateFailed(
                f"Error fetching SEDIF data: {err}"
            ) from err
        except Exception as err:
            raise UpdateFailed(
                f"Unexpected error fetching SEDIF data: {err}"
            ) from err

    @staticmethod
    def _fetch_all(
        username: str,
        password: str,
        contracts: list[dict[str, str]],
    ) -> SedifData:
        """Fetch consumption data for all contracts (runs in executor)."""
        client = EauIDFClient(username, password)
        try:
            client.login()
            data: SedifData = {}
            for contract in contracts:
                cid = contract["id"]
                number = contract["number"]
                try:
                    end = date.today()
                    start = end - timedelta(days=7)
                    records = client.get_daily_consumption(
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
                    _LOGGER.exception(
                        "Failed to fetch data for contract %s", number
                    )
            if not data and contracts:
                raise EauIDFError(
                    "Failed to fetch data for any contract"
                )
            return data
        finally:
            client.close()
