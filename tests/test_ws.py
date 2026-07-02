"""Tests for the HydroNode WebSocket client: message dispatch and reconnect wiring."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.hydronode.const import EVENT_ANOMALY
from custom_components.hydronode.coordinator import HydroNodeCoordinator
from custom_components.hydronode.ws import (
    HydroNodeWebSocketClient,
    HydroNodeWsError,
    build_ws_url,
)


class FakeWSMessage:
    """Mimics aiohttp.WSMessage well enough for our dispatch logic."""

    def __init__(self, type_: aiohttp.WSMsgType, data: str) -> None:
        self.type = type_
        self.data = data


class FakeWebSocket:
    """Mimics aiohttp's ClientWebSocketResponse for one connection lifecycle."""

    def __init__(self, messages: list[FakeWSMessage], close_code: int | None = 1000) -> None:
        self._auth_response = messages[0] if messages else None
        self._messages = messages[1:]
        self.close_code = close_code
        self.sent: list[dict] = []
        self.closed = False

    async def __aenter__(self) -> "FakeWebSocket":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def receive(self) -> FakeWSMessage:
        return self._auth_response

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for msg in self._messages:
            yield msg


class FakeSession:
    """Mimics aiohttp.ClientSession.ws_connect() returning a FakeWebSocket."""

    def __init__(self, ws: FakeWebSocket) -> None:
        self._ws = ws

    def ws_connect(self, url: str, **kwargs: object) -> FakeWebSocket:
        return self._ws


def _auth_ok(user_id: str = "u1") -> FakeWSMessage:
    return FakeWSMessage(
        aiohttp.WSMsgType.TEXT, json.dumps({"type": "auth_ok", "userId": user_id})
    )


def _envelope(msg_type: str, data: dict) -> FakeWSMessage:
    return FakeWSMessage(
        aiohttp.WSMsgType.TEXT,
        json.dumps({"type": msg_type, "ts": "2026-07-02T14:30:22.456Z", "data": data}),
    )


def test_build_ws_url_translates_scheme():
    assert build_ws_url("https://hydronode.com") == "wss://hydronode.com/ws/ha/v1"
    assert build_ws_url("http://localhost:8080/") == "ws://localhost:8080/ws/ha/v1"


def _make_client(session, **overrides):
    kwargs = dict(
        hass=overrides.pop("hass"),
        session=session,
        base_url="https://hydronode.example.com",
        token="hat_test",
        on_value_updated=lambda data: None,
        on_anomaly_detected=lambda data: None,
        on_ai_analyzed=lambda data: None,
        on_auth_failed=lambda: None,
    )
    kwargs.update(overrides)
    return HydroNodeWebSocketClient(**kwargs)


async def test_connect_once_sends_auth_and_dispatches_value_update(hass):
    """First message is the auth envelope; subsequent TEXT messages are dispatched."""
    value_data = {
        "sensorId": "s1",
        "stationId": "st1",
        "type": "WATER_TEMPERATURE",
        "channelName": None,
        "value": 21.5,
        "timestamp": "2026-07-02T14:30:22Z",
    }
    ws = FakeWebSocket([_auth_ok(), _envelope("value.updated", value_data)])
    session = FakeSession(ws)

    received: dict[str, dict | None] = {"value": None}
    client = _make_client(
        session,
        hass=hass,
        on_value_updated=lambda data: received.__setitem__("value", data),
    )

    with pytest.raises(HydroNodeWsError):
        await client._connect_once()

    assert ws.sent == [{"type": "auth", "token": "hat_test"}]
    assert received["value"] == value_data


async def test_dispatch_routes_all_message_types(hass):
    """_dispatch() routes each envelope type to its matching callback."""
    seen: dict[str, dict | None] = {"value": None, "anomaly": None, "ai": None}
    client = _make_client(
        FakeSession(FakeWebSocket([])),
        hass=hass,
        on_value_updated=lambda data: seen.__setitem__("value", data),
        on_anomaly_detected=lambda data: seen.__setitem__("anomaly", data),
        on_ai_analyzed=lambda data: seen.__setitem__("ai", data),
    )

    client._dispatch(_envelope("value.updated", {"sensorId": "s1"}))
    client._dispatch(_envelope("anomaly.detected", {"anomalyId": "a1"}))
    client._dispatch(_envelope("anomaly.ai_analyzed", {"anomalyId": "a1", "provider": "x"}))
    client._dispatch(_envelope("some.unknown.type", {"foo": "bar"}))

    assert seen["value"] == {"sensorId": "s1"}
    assert seen["anomaly"] == {"anomalyId": "a1"}
    assert seen["ai"] == {"anomalyId": "a1", "provider": "x"}


async def test_dispatch_ignores_non_json_payload(hass):
    client = _make_client(FakeSession(FakeWebSocket([])), hass=hass)
    # Must not raise.
    client._dispatch(FakeWSMessage(aiohttp.WSMsgType.TEXT, "not json"))


async def test_auth_timeout_raises_ws_error(hass):
    """No auth response within WS_AUTH_TIMEOUT -> _connect_once() raises (auth failed)."""
    import asyncio

    ws = FakeWebSocket([])

    async def _hang_receive():
        await asyncio.sleep(999)

    ws.receive = _hang_receive  # type: ignore[assignment]
    session = FakeSession(ws)
    client = _make_client(session, hass=hass)

    # Patch the timeout to something tiny for the test instead of waiting 10s.
    import custom_components.hydronode.ws as ws_module

    original_timeout = ws_module.WS_AUTH_TIMEOUT
    ws_module.WS_AUTH_TIMEOUT = 0.01
    try:
        with pytest.raises(HydroNodeWsError):
            await client._connect_once()
    finally:
        ws_module.WS_AUTH_TIMEOUT = original_timeout


async def test_run_loop_calls_on_auth_failed_and_stops(hass):
    """The reconnect loop invokes on_auth_failed() and exits (no retry) on 4401-style errors."""
    ws = FakeWebSocket([FakeWSMessage(aiohttp.WSMsgType.TEXT, json.dumps({"type": "nope"}))])
    session = FakeSession(ws)

    auth_failed_calls: list[bool] = []
    client = _make_client(
        session, hass=hass, on_auth_failed=lambda: auth_failed_calls.append(True)
    )

    await client._run()

    assert auth_failed_calls == [True]


async def test_value_updated_dispatch_updates_coordinator_and_anomaly_fires_bus_event(hass):
    """End-to-end wiring: value.updated patches the coordinator; anomaly fires the HA bus."""
    mock_client = AsyncMock()
    mock_client.states.return_value = [
        {
            "sensorId": "s1",
            "type": "WATER_TEMPERATURE",
            "channelName": None,
            "value": 21.5,
            "timestamp": "2026-07-02T14:30:22Z",
        }
    ]
    mock_client.bootstrap.return_value = {"user": {"id": "u1"}, "stations": []}
    coordinator = HydroNodeCoordinator(hass, mock_client, poll_interval=60)
    await coordinator.async_refresh()

    events = async_capture_events(hass, EVENT_ANOMALY)

    def on_value_updated(data: dict) -> None:
        coordinator.apply_ws_value_update(data)

    def on_anomaly_detected(data: dict) -> None:
        hass.bus.async_fire(EVENT_ANOMALY, data)

    ws_client = _make_client(
        FakeSession(FakeWebSocket([])),
        hass=hass,
        on_value_updated=on_value_updated,
        on_anomaly_detected=on_anomaly_detected,
    )

    ws_client._dispatch(
        _envelope(
            "value.updated",
            {
                "sensorId": "s1",
                "type": "WATER_TEMPERATURE",
                "channelName": None,
                "value": 42.0,
                "timestamp": "2026-07-02T15:00:00Z",
            },
        )
    )
    assert coordinator.data[("s1", "WATER_TEMPERATURE", None)]["value"] == 42.0

    ws_client._dispatch(
        _envelope(
            "anomaly.detected",
            {"anomalyId": "a1", "sensorId": "s1", "stationId": "st1"},
        )
    )
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["anomalyId"] == "a1"
