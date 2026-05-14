"""Shared fixtures for eauidf tests."""

from datetime import date
from unittest.mock import MagicMock

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eauidf.const import CONF_CONTRACTS, DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

MOCK_USERNAME = "test@example.com"
MOCK_PASSWORD = "secret"
MOCK_CONTRACT_ID = "CONTRACT_001"
MOCK_CONTRACT_NUMBER = "9235380"
MOCK_CONTRACTS = [{"id": MOCK_CONTRACT_ID, "number": MOCK_CONTRACT_NUMBER}]


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: MOCK_USERNAME,
            CONF_PASSWORD: MOCK_PASSWORD,
            CONF_CONTRACTS: MOCK_CONTRACTS,
        },
        unique_id=MOCK_USERNAME,
    )


@pytest.fixture
def mock_record() -> MagicMock:
    record = MagicMock()
    record.meter_reading = 1234.5
    record.consumption_liters = 150.0
    record.date = MagicMock()
    record.date.date.return_value = date(2026, 5, 13)
    record.is_estimated = False
    return record
