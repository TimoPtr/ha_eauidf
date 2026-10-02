# Copyright (c) 2026 Timothy (TimoPtr)
"""L'eau d'Ile-de-France integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from pyeauidf import EauIDFClient
from pyeauidf.client import EauIDFError

from .const import CONF_CONTRACTS, DOMAIN
from .coordinator import SedifCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

type EauIDFConfigEntry = ConfigEntry[SedifCoordinator]

PLATFORMS = [Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: EauIDFConfigEntry) -> bool:
    """Set up L'eau d'Ile-de-France from a config entry."""
    await _refresh_contracts(hass, entry)
    _remove_stale_devices(hass, entry)

    coordinator = SedifCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EauIDFConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _refresh_contracts(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Refresh the contract list stored in the entry from the API."""
    session = async_create_clientsession(hass)
    client = EauIDFClient(
        entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD], session=session
    )
    try:
        await client.login()
        contract_ids = await client.get_contracts()
        contracts = []
        for cid in contract_ids:
            details = await client.get_contract_details(cid)
            contrat = details.get("contrat", {})
            number = contrat.get("Name", cid)
            contracts.append({"id": cid, "number": str(number)})
    except (EauIDFError, OSError):  # fmt: skip
        _LOGGER.debug("Could not refresh contracts, using cached list")
        return

    if contracts != entry.data.get(CONF_CONTRACTS, []):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_CONTRACTS: contracts}
        )


def _is_current_contract_device(entry: ConfigEntry, device: dr.DeviceEntry) -> bool:
    """Return whether the device belongs to a contract currently on the account."""
    return any(
        (DOMAIN, contract["number"]) in device.identifiers
        for contract in entry.data.get(CONF_CONTRACTS, [])
    )


def _remove_stale_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Remove devices that no longer match a contract on the account.

    This covers contracts closed on the SEDIF side and devices left over from
    versions that identified contracts by their opaque API id, which changes
    over time, instead of the contract number.
    """
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not _is_current_contract_device(entry, device):
            dev_reg.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: EauIDFConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow the user to delete a device that is not a current contract."""
    return not _is_current_contract_device(config_entry, device_entry)
