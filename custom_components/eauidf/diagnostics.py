# Copyright (c) 2026 Timothy
"""Diagnostics support for L'eau d'Ile-de-France."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import EauIDFConfigEntry

TO_REDACT = {"username", "password"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EauIDFConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "config_entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "coordinator_data": {
            contract_number: {
                "meter_reading_m3": data.meter_reading_m3,
                "daily_consumption_l": data.daily_consumption_l,
                "last_date": data.last_date.isoformat(),
                "is_estimated": data.is_estimated,
            }
            for contract_number, data in (coordinator.data or {}).items()
        },
    }
