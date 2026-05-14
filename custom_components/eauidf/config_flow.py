"""Config flow for L'eau d'Ile-de-France."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pyeauidf import EauIDFClient
from pyeauidf.client import AuthenticationError, EauIDFError

from .const import CONF_CONTRACTS, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class EauIDFConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for L'eau d'Ile-de-France."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            try:
                contracts = await self._validate_and_fetch_contracts(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except EauIDFError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "cannot_connect"
            else:
                if not contracts:
                    errors["base"] = "no_contracts"
                else:
                    return self.async_create_entry(
                        title=user_input[CONF_USERNAME],
                        data={
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_CONTRACTS: contracts,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth when credentials expire."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            try:
                contracts = await self._validate_and_fetch_contracts(
                    reauth_entry.data[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except EauIDFError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_CONTRACTS: contracts,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    async def _validate_and_fetch_contracts(
        self, username: str, password: str
    ) -> list[dict[str, str]]:
        """Validate credentials and return contract list."""

        def _fetch() -> list[dict[str, str]]:
            client = EauIDFClient(username, password)
            try:
                client.login()
                contract_ids = client.get_contracts()
                contracts = []
                for cid in contract_ids:
                    details = client.get_contract_details(cid)
                    contrat = details.get("contrat", {})
                    number = contrat.get("Name", cid)
                    contracts.append({"id": cid, "number": str(number)})
                return contracts
            finally:
                client.close()

        return await self.hass.async_add_executor_job(_fetch)
