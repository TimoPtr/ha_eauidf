# Copyright (c) 2026 Timothy (TimoPtr)
"""Helpers shared by the config flow, the setup and the coordinator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.aiohttp_client import async_create_clientsession
from pyeauidf import Contract, EauIDFClient

from .const import CONF_CONTRACTS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


# Config entry data must be JSON, so contracts are stored as plain dicts and
# converted back to Contract wherever they are read.


def contracts_to_data(contracts: list[Contract]) -> list[dict[str, str]]:
    """Convert contracts to the form stored in the config entry."""
    return [{"id": c.contract_id, "number": c.number} for c in contracts]


def entry_contracts(entry: ConfigEntry) -> list[Contract]:
    """Return the contracts stored in the config entry."""
    return [
        Contract(contract_id=c["id"], number=c["number"])
        for c in entry.data.get(CONF_CONTRACTS, [])
    ]


async def async_fetch_contracts(
    hass: HomeAssistant, username: str, password: str
) -> list[Contract]:
    """
    Log in and return the account's active contracts.

    Raises the client errors (authentication, connection) to the caller.
    """
    # A dedicated session keeps the SEDIF login cookies out of the shared one.
    # It only lives for this call, so it is detached instead of left to HA.
    session = async_create_clientsession(hass, auto_cleanup=False)
    client = EauIDFClient(username, password, session=session)
    try:
        await client.login()
        return await client.get_active_contracts()
    finally:
        await client.close()
        session.detach()
