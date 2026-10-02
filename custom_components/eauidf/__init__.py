# Copyright (c) 2026 Timothy (TimoPtr)
"""L'eau d'Ile-de-France integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .coordinator import (
    SedifCoordinator,
    async_remove_stale_devices,
    is_current_contract_device,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry as dr

type EauIDFConfigEntry = ConfigEntry[SedifCoordinator]

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: EauIDFConfigEntry) -> bool:
    """Set up L'eau d'Ile-de-France from a config entry."""
    async_remove_stale_devices(hass, entry)

    coordinator = SedifCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EauIDFConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: EauIDFConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow the user to delete a device that is not a current contract."""
    return not is_current_contract_device(config_entry, device_entry)
