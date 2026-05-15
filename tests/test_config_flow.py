"""Tests for the config flow."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pyeauidf.client import AuthenticationError, EauIDFError

from custom_components.eauidf.const import CONF_CONTRACTS, DOMAIN
from tests.conftest import (
    MOCK_CONTRACT_ID,
    MOCK_CONTRACT_NUMBER,
    MOCK_CONTRACTS,
    MOCK_PASSWORD,
    MOCK_USERNAME,
)

PATCH_CLIENT = "custom_components.eauidf.config_flow.EauIDFClient"


def _make_client(contract_ids: list | None = None) -> MagicMock:
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.get_contracts = AsyncMock(
        return_value=contract_ids if contract_ids is not None else [MOCK_CONTRACT_ID]
    )
    client.get_contract_details = AsyncMock(
        return_value={"contrat": {"Name": MOCK_CONTRACT_NUMBER}}
    )
    return client


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert not result["errors"]


async def test_user_step_success(hass: HomeAssistant) -> None:
    with patch(PATCH_CLIENT, return_value=_make_client()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: MOCK_USERNAME, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == "create_entry"
    assert result["title"] == MOCK_USERNAME
    assert result["data"][CONF_USERNAME] == MOCK_USERNAME
    assert result["data"][CONF_PASSWORD] == MOCK_PASSWORD
    assert result["data"][CONF_CONTRACTS] == MOCK_CONTRACTS


async def test_user_step_invalid_auth(hass: HomeAssistant) -> None:
    client = MagicMock()
    client.login = AsyncMock(side_effect=AuthenticationError("bad credentials"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: MOCK_USERNAME, CONF_PASSWORD: "wrong"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


async def test_user_step_cannot_connect(hass: HomeAssistant) -> None:
    client = MagicMock()
    client.login = AsyncMock(side_effect=EauIDFError("connection failed"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: MOCK_USERNAME, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


async def test_user_step_unexpected_error(hass: HomeAssistant) -> None:
    client = MagicMock()
    client.login = AsyncMock(side_effect=RuntimeError("unexpected"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: MOCK_USERNAME, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


async def test_user_step_no_contracts(hass: HomeAssistant) -> None:
    with patch(PATCH_CLIENT, return_value=_make_client(contract_ids=[])):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: MOCK_USERNAME, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "no_contracts"


async def test_user_step_already_configured(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)

    with patch(PATCH_CLIENT, return_value=_make_client()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: MOCK_USERNAME, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_reauth_shows_form(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)

    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"


async def test_reauth_success(
    hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    mock_config_entry.add_to_hass(hass)

    coord_client = MagicMock()
    coord_client.login = AsyncMock()
    coord_client.close = AsyncMock()
    coord_client.get_daily_consumption = AsyncMock(return_value=[mock_record])

    with (
        patch(PATCH_CLIENT, return_value=_make_client()),
        patch(
            "custom_components.eauidf.coordinator.EauIDFClient",
            return_value=coord_client,
        ),
    ):
        result = await mock_config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: "new_password"},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new_password"


async def test_reauth_invalid_auth(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)

    client = MagicMock()
    client.login = AsyncMock(side_effect=AuthenticationError("bad credentials"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        result = await mock_config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: "wrong"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


async def test_reauth_cannot_connect(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)

    client = MagicMock()
    client.login = AsyncMock(side_effect=EauIDFError("portal down"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        result = await mock_config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


async def test_reconfigure_shows_form(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)

    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"


async def test_reconfigure_success(
    hass: HomeAssistant, mock_config_entry, mock_record
) -> None:
    mock_config_entry.add_to_hass(hass)

    init_client = MagicMock()
    init_client.login = AsyncMock()
    init_client.get_contracts = AsyncMock(return_value=[MOCK_CONTRACT_ID])
    init_client.get_contract_details = AsyncMock(
        return_value={"contrat": {"Name": MOCK_CONTRACT_NUMBER}}
    )

    coord_client = MagicMock()
    coord_client.login = AsyncMock()
    coord_client.close = AsyncMock()
    coord_client.get_daily_consumption = AsyncMock(return_value=[mock_record])

    with (
        patch(PATCH_CLIENT, return_value=_make_client()),
        patch(
            "custom_components.eauidf.EauIDFClient",
            return_value=init_client,
        ),
        patch(
            "custom_components.eauidf.coordinator.EauIDFClient",
            return_value=coord_client,
        ),
    ):
        result = await mock_config_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: "new_password"},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new_password"
    assert mock_config_entry.data[CONF_CONTRACTS] == MOCK_CONTRACTS


async def test_reconfigure_invalid_auth(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)

    client = MagicMock()
    client.login = AsyncMock(side_effect=AuthenticationError("bad credentials"))
    client.close = AsyncMock()

    with patch(PATCH_CLIENT, return_value=client):
        result = await mock_config_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: "wrong"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


async def test_reconfigure_no_contracts(
    hass: HomeAssistant, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)

    with patch(PATCH_CLIENT, return_value=_make_client(contract_ids=[])):
        result = await mock_config_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "no_contracts"
