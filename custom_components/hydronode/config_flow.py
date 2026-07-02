"""Config flow for the HydroNode integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import HydroNodeApiClient, HydroNodeApiError, HydroNodeAuthError
from .const import (
    CONF_BASE_URL,
    CONF_FIRE_VALUE_EVENTS,
    CONF_INCLUDE_FOLLOWED,
    CONF_POLL_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_FIRE_VALUE_EVENTS,
    DEFAULT_INCLUDE_FOLLOWED,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MIN_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


async def _validate_credentials(hass: Any, base_url: str, token: str) -> dict[str, Any]:
    """Validate credentials via `GET /api/ha/v1/bootstrap`, return the `user` block."""
    session = async_get_clientsession(hass)
    client = HydroNodeApiClient(session, base_url, token)
    bootstrap = await client.bootstrap()
    user = bootstrap.get("user") or {}
    if not user.get("id"):
        raise HydroNodeApiError("Bootstrap response is missing user.id")
    return user


class HydroNodeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HydroNode."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step: base URL + Personal Access Token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            token = user_input[CONF_TOKEN]
            try:
                user = await _validate_credentials(self.hass, base_url, token)
            except HydroNodeAuthError:
                errors["base"] = "invalid_auth"
            except HydroNodeApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating HydroNode credentials")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user["id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user.get("displayName") or "HydroNode",
                    data={CONF_BASE_URL: base_url, CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start a reauth flow after the token was revoked/rejected (401)."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for a fresh Personal Access Token and re-validate."""
        errors: dict[str, str] = {}

        if user_input is not None and self._reauth_entry is not None:
            base_url = self._reauth_entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            token = user_input[CONF_TOKEN]
            try:
                await _validate_credentials(self.hass, base_url, token)
            except HydroNodeAuthError:
                errors["base"] = "invalid_auth"
            except HydroNodeApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating HydroNode credentials")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, CONF_TOKEN: token},
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HydroNodeOptionsFlow:
        """Return the options flow handler for this config entry."""
        return HydroNodeOptionsFlow(config_entry)


class HydroNodeOptionsFlow(config_entries.OptionsFlow):
    """Handle HydroNode options: poll interval, followed stations, value events."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_POLL_INTERVAL,
                        max=3600,
                        step=5,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(
                    CONF_INCLUDE_FOLLOWED,
                    default=options.get(
                        CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED
                    ),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_FIRE_VALUE_EVENTS,
                    default=options.get(
                        CONF_FIRE_VALUE_EVENTS, DEFAULT_FIRE_VALUE_EVENTS
                    ),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
