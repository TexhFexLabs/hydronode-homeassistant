"""Regression tests for transport, malformed responses and privacy boundaries."""

from copy import deepcopy
from unittest.mock import Mock, patch

import pytest
from homeassistant.const import CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronode.api import (
    HydroNodeApiClient,
    HydroNodeApiError,
    HydroNodeAuthError,
    HydroNodeConnectionError,
    HydroNodeInvalidUrl,
    async_create_hydronode_session,
    normalize_base_url,
)
from custom_components.hydronode.const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN
from custom_components.hydronode.diagnostics import async_get_config_entry_diagnostics
from tests.test_setup_entities import BOOTSTRAP

BASE_URL = "https://hydronode.tech"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://",
        "https://user:password@example.com",
        "https://example.com?token=secret",
        "https://example.com/#secret",
        "http://hydronode.tech",
        "http://hydronode.texhfexlabs.de",
        "https://example.com:invalid",
        "https://exam\nple.com",
    ],
)
def test_invalid_urls_are_rejected(url):
    with pytest.raises(HydroNodeInvalidUrl):
        normalize_base_url(url)


def test_domain_and_normalization():
    assert DEFAULT_BASE_URL == BASE_URL
    assert normalize_base_url(" HTTPS://HYDRONODE.TECH/ ") == BASE_URL
    assert normalize_base_url("http://localhost:8080/") == "http://localhost:8080"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"user": {"id": "u1"}},
        {"user": {"id": "u1"}, "stations": [None]},
        {"user": {"id": "u1"}, "stations": [{"id": "s", "sensors": None}]},
    ],
)
async def test_invalid_bootstrap_fails_closed(hass, aioclient_mock, payload):
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=payload)
    client = HydroNodeApiClient(async_get_clientsession(hass), BASE_URL, "hat_test")
    with pytest.raises(HydroNodeApiError):
        await client.bootstrap()


@pytest.mark.parametrize("payload", [{}, [None], [{"sensorId": "s1"}]])
async def test_invalid_states_are_not_empty_success(hass, aioclient_mock, payload):
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/states", json=payload)
    client = HydroNodeApiClient(async_get_clientsession(hass), BASE_URL, "hat_test")
    with pytest.raises(HydroNodeApiError):
        await client.states()


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_permissions_require_reauth(hass, aioclient_mock, status):
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", status=status)
    client = HydroNodeApiClient(async_get_clientsession(hass), BASE_URL, "hat_test")
    with pytest.raises(HydroNodeAuthError):
        await client.bootstrap()


async def test_timeout_is_transport_error(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", exc=TimeoutError())
    client = HydroNodeApiClient(async_get_clientsession(hass), BASE_URL, "hat_test")
    with pytest.raises(HydroNodeConnectionError):
        await client.bootstrap()


async def test_response_body_is_not_in_error(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/api/ha/v1/bootstrap",
        status=500,
        text="hat_secret and private data",
    )
    client = HydroNodeApiClient(async_get_clientsession(hass), BASE_URL, "hat_test")
    with pytest.raises(HydroNodeApiError) as error:
        await client.bootstrap()
    assert "private" not in str(error.value)
    assert "hat_secret" not in str(error.value)


async def test_rest_redirect_is_not_followed(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/api/ha/v1/bootstrap",
        status=302,
        headers={"Location": "https://other.example/bootstrap"},
    )
    client = HydroNodeApiClient(async_get_clientsession(hass), BASE_URL, "hat_test")
    with pytest.raises(HydroNodeApiError):
        await client.bootstrap()
    assert aioclient_mock.call_count == 1


async def test_diagnostics_hide_identity_and_coordinates(hass):
    bootstrap = deepcopy(BOOTSTRAP)
    bootstrap["stations"][0].update(latitude=12.34, longitude=56.78)
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_TOKEN: "hat_secret", CONF_BASE_URL: BASE_URL}
    )
    entry.runtime_data = Mock(coordinator=Mock(bootstrap=bootstrap, data={}))
    result = await async_get_config_entry_diagnostics(hass, entry)
    serialized = str(result)
    for private in ("hat_secret", "Felix", "Gewaechshaus", "12.34", "56.78", BASE_URL):
        assert private not in serialized
    assert bootstrap["stations"][0]["latitude"] == 12.34


async def test_bad_discovery_does_not_remove_existing_entities(hass, aioclient_mock):
    from homeassistant.helpers import device_registry as dr
    from tests.test_setup_entities import BASE_URL as old_base, TOKEN

    aioclient_mock.get(f"{old_base}/api/ha/v1/bootstrap", json=BOOTSTRAP)
    aioclient_mock.get(f"{old_base}/api/ha/v1/states", json=[])
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="u1", data={CONF_BASE_URL: old_base, CONF_TOKEN: TOKEN}
    )
    entry.add_to_hass(hass)
    with patch("custom_components.hydronode.ws.HydroNodeWebSocketClient.start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{old_base}/api/ha/v1/bootstrap", json={})
    with pytest.raises(HydroNodeApiError):
        await entry.runtime_data.coordinator.async_refresh_bootstrap()
    assert (
        dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "st1")}) is not None
    )
    await hass.config_entries.async_unload(entry.entry_id)


async def test_websocket_redirect_cannot_receive_token(
    hass, aiohttp_server, socket_enabled
):
    """Exercise real aiohttp redirect handling, before the first-message PAT auth."""
    from aiohttp import web
    from custom_components.hydronode.ws import HydroNodeWebSocketClient

    redirected_requests = []

    async def target(request):
        redirected_requests.append(request.path)
        return web.Response(text="unexpected")

    target_app = web.Application()
    target_app.router.add_get("/capture", target)
    target_server = await aiohttp_server(target_app)

    async def redirect(request):
        raise web.HTTPFound(str(target_server.make_url("/capture")))

    source_app = web.Application()
    source_app.router.add_get("/ws/ha/v1", redirect)
    source_server = await aiohttp_server(source_app)
    session = async_create_hydronode_session(hass)
    client = HydroNodeWebSocketClient(
        hass=hass,
        session=session,
        base_url=str(source_server.make_url("")),
        token="hat_secret",
        on_value_updated=Mock(),
        on_anomaly_detected=Mock(),
        on_ai_analyzed=Mock(),
        on_auth_failed=Mock(),
    )
    try:
        with pytest.raises(HydroNodeConnectionError):
            await client._connect_once()
        assert redirected_requests == []
    finally:
        session.detach()


async def test_response_size_limit(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/api/ha/v1/bootstrap", text='{"padding":"' + "x" * 200 + '"}'
    )
    client = HydroNodeApiClient(async_get_clientsession(hass), BASE_URL, "hat_test")
    with patch("custom_components.hydronode.api.MAX_RESPONSE_BYTES", 100):
        with pytest.raises(HydroNodeApiError, match="size limit"):
            await client.bootstrap()


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), True, "21.5", {}, 10**400]
)
def test_invalid_measurements(value):
    from custom_components.hydronode.validation import valid_state

    assert not valid_state(
        {
            "sensorId": "s",
            "type": "T",
            "value": value,
            "timestamp": "2026-09-10T12:00:00Z",
        }
    )
