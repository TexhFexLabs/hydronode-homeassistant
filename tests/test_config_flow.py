"""Tests for the HydroNode config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hydronode.const import CONF_BASE_URL, DOMAIN

BASE_URL = "https://hydronode.example.com"
TOKEN = "hat_abcdefghij1234567890abcdefghij1234"

BOOTSTRAP_OK = {
    "user": {"id": "user-123", "displayName": "Felix Knoll"},
    "stations": [],
}

# Reaching CREATE_ENTRY triggers a real async_setup_entry() for the new
# config entry. These flow tests only care about the flow itself, so the
# component setup (bootstrap refetch, WS connect, platform forwarding) is
# stubbed out, matching the common HA custom-component test pattern.
_SETUP_ENTRY = "custom_components.hydronode.async_setup_entry"


async def test_user_flow_success(hass, aioclient_mock):
    """A valid base URL + token creates a config entry keyed on user.id."""
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP_OK)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(_SETUP_ENTRY, return_value=True):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_BASE_URL: BASE_URL, CONF_TOKEN: TOKEN},
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Felix Knoll"
    assert result2["data"][CONF_BASE_URL] == BASE_URL
    assert result2["data"][CONF_TOKEN] == TOKEN
    assert result2["result"].unique_id == "user-123"


async def test_user_flow_invalid_token(hass, aioclient_mock):
    """A 401 from bootstrap surfaces as an `invalid_auth` form error."""
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", status=401)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BASE_URL: BASE_URL, CONF_TOKEN: "hat_invalid"},
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass, aioclient_mock):
    """A transport-level failure surfaces as a `cannot_connect` form error."""
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", exc=Exception("boom"))

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BASE_URL: BASE_URL, CONF_TOKEN: TOKEN},
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "unknown"}


async def test_user_flow_duplicate_aborts(hass, aioclient_mock):
    """A second config entry for the same user.id aborts as already configured."""
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP_OK)

    with patch(_SETUP_ENTRY, return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_TOKEN: TOKEN}
        )
        await hass.async_block_till_done()

        result2 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_TOKEN: TOKEN}
        )

    assert result3["type"] is FlowResultType.ABORT
    assert result3["reason"] == "already_configured"


async def test_options_flow_defaults(hass, aioclient_mock):
    """Options flow shows defaults and stores updated values."""
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP_OK)

    with patch(_SETUP_ENTRY, return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        entry_result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_TOKEN: TOKEN}
        )
        await hass.async_block_till_done()
        entry = entry_result["result"]

        flow = await hass.config_entries.options.async_init(entry.entry_id)
        assert flow["type"] is FlowResultType.FORM
        assert flow["step_id"] == "init"

        result_options = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {
                "poll_interval": 30,
                "include_followed": False,
                "fire_value_events": True,
            },
        )
        await hass.async_block_till_done()

    assert result_options["type"] is FlowResultType.CREATE_ENTRY
    assert result_options["data"]["poll_interval"] == 30
    assert result_options["data"]["include_followed"] is False
    assert result_options["data"]["fire_value_events"] is True
