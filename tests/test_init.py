# Copyright (c) 2026 Timothy (TimoPtr)
"""Tests for the integration setup."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.recorder import Recorder
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pyeauidf.client import EauIDFError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import eauidf
from custom_components.eauidf.const import CONF_CONTRACTS, DOMAIN
from tests.conftest import (
    MOCK_ACTIVE_CONTRACTS,
    MOCK_CONTRACT_NUMBER,
    MOCK_CONTRACTS,
    make_consumption_data,
)

PATCH_COORD_CLIENT = "custom_components.eauidf.coordinator.EauIDFClient"

# Identifier used by pre-release versions (before v1.0.0), keyed on the SEDIF
# opaque contract id instead of the contract number.
LEGACY_IDENTIFIER = "iMvSQcY23KUv%2F"


def _make_coord_client(mock_record: MagicMock, *, fail: bool = False) -> MagicMock:
    client = MagicMock()
    client.login = AsyncMock(side_effect=EauIDFError("down") if fail else None)
    client.close = AsyncMock()
    client.get_active_contracts = AsyncMock(return_value=MOCK_ACTIVE_CONTRACTS)
    client.get_daily_consumption = AsyncMock(
        return_value=make_consumption_data([mock_record])
    )
    return client


def _add_device(
    hass: HomeAssistant, entry: MockConfigEntry, identifier: str
) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, identifier)}
    )


def _contract_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    return next(
        device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        if (DOMAIN, MOCK_CONTRACT_NUMBER) in device.identifiers
    )


async def _setup_integration(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_record: MagicMock,
    *,
    api_down: bool = False,
) -> None:
    with patch(
        PATCH_COORD_CLIENT,
        return_value=_make_coord_client(mock_record, fail=api_down),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_legacy_device_removed_on_setup(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_record: MagicMock,
) -> None:
    """A device not matching any current contract is removed with its entities."""
    mock_config_entry.add_to_hass(hass)
    legacy = _add_device(hass, mock_config_entry, LEGACY_IDENTIFIER)
    ent_reg = er.async_get(hass)
    legacy_entity = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mock_config_entry.entry_id}_{LEGACY_IDENTIFIER}_meter_reading",
        config_entry=mock_config_entry,
        device_id=legacy.id,
    )

    await _setup_integration(hass, mock_config_entry, mock_record)

    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get(legacy.id) is None
    assert ent_reg.async_get(legacy_entity.entity_id) is None
    assert _contract_device(hass, mock_config_entry)


async def test_legacy_device_removed_when_api_unavailable(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_record: MagicMock,
) -> None:
    """Cleanup falls back to the stored contracts when they cannot be refreshed."""
    mock_config_entry.add_to_hass(hass)
    legacy = _add_device(hass, mock_config_entry, LEGACY_IDENTIFIER)

    await _setup_integration(hass, mock_config_entry, mock_record, api_down=True)

    assert dr.async_get(hass).async_get(legacy.id) is None


async def test_remove_config_entry_device(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_record: MagicMock,
) -> None:
    """Only devices that are not a current contract can be deleted by the user."""
    mock_config_entry.add_to_hass(hass)
    await _setup_integration(hass, mock_config_entry, mock_record)
    current = _contract_device(hass, mock_config_entry)
    stale = _add_device(hass, mock_config_entry, LEGACY_IDENTIFIER)

    assert not await eauidf.async_remove_config_entry_device(
        hass, mock_config_entry, current
    )
    assert await eauidf.async_remove_config_entry_device(hass, mock_config_entry, stale)


async def test_closed_contract_device_removed(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_record: MagicMock,
) -> None:
    """A contract no longer returned by the API is dropped with its device."""
    closed = {"id": "CONTRACT_OLD", "number": "7654321"}
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_CONTRACTS: [*MOCK_CONTRACTS, closed]},
    )
    closed_device = _add_device(hass, mock_config_entry, closed["number"])

    await _setup_integration(hass, mock_config_entry, mock_record)

    assert mock_config_entry.data[CONF_CONTRACTS] == MOCK_CONTRACTS
    assert dr.async_get(hass).async_get(closed_device.id) is None
    assert _contract_device(hass, mock_config_entry)
