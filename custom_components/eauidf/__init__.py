"""L'eau d'Ile-de-France integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import async_get as async_get_dev_reg
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

    coordinator = SedifCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EauIDFConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _refresh_contracts(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Refresh contract list from the API and remove stale devices."""
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

    old_contracts = entry.data.get(CONF_CONTRACTS, [])
    if contracts == old_contracts:
        return

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CONTRACTS: contracts}
    )

    old_numbers = {c["number"] for c in old_contracts}
    new_numbers = {c["number"] for c in contracts}
    removed = old_numbers - new_numbers
    if removed:
        dev_reg = async_get_dev_reg(hass)
        for number in removed:
            device = dev_reg.async_get_device(identifiers={(DOMAIN, number)})
            if device:
                dev_reg.async_remove_device(device.id)
