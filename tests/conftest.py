"""Shared fixtures for eauidf tests."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eauidf.const import CONF_CONTRACTS, DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Enable custom integrations for all tests."""


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Set up the recorder database before hass."""


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


def make_consumption_record(
    d: date, consumption_liters: float, meter_reading: float
) -> MagicMock:
    record = MagicMock()
    dt = datetime(d.year, d.month, d.day, tzinfo=UTC)
    mock_date = MagicMock(wraps=dt)
    mock_date.date = MagicMock(return_value=d)
    mock_date.__lt__ = lambda _self, other: dt < other
    mock_date.__le__ = lambda _self, other: dt <= other
    mock_date.__gt__ = lambda _self, other: dt > other
    mock_date.__ge__ = lambda _self, other: dt >= other
    mock_date.__eq__ = lambda _self, other: dt == other
    record.date = mock_date
    record.consumption_liters = consumption_liters
    record.meter_reading = meter_reading
    record.is_estimated = False
    return record


@pytest.fixture
def mock_records_list() -> list[MagicMock]:
    base = date(2026, 5, 11)
    return [
        make_consumption_record(base, 100.0, 1234.0),
        make_consumption_record(base + timedelta(days=1), 120.0, 1234.12),
        make_consumption_record(base + timedelta(days=2), 150.0, 1234.27),
    ]
