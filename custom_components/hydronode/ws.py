"""Persistent WebSocket client for /ws/ha/v1 push events."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Awaitable, Callable

import aiohttp

from homeassistant.core import HomeAssistant

from .api import HydroNodeAuthError, normalize_base_url
from .const import (
    EVENT_TYPE_ANALYSIS_COMPLETED,
    EVENT_TYPE_INCIDENT_UPDATED,
    EVENT_TYPE_VALUE_UPDATED,
    MSG_TYPE_AUTH,
    MSG_TYPE_AUTH_OK,
    WS_AUTH_TIMEOUT,
    WS_CLOSE_AUTH_FAILED,
    WS_CLOSE_SESSION_LIMIT,
    WS_MAX_BACKOFF,
    WS_MIN_BACKOFF,
    WS_PATH,
)

_LOGGER = logging.getLogger(__name__)

DataCallback = Callable[[dict[str, Any]], None]
VoidCallback = Callable[[], None]
ReconnectCallback = Callable[[], "Awaitable[None] | None"]


class HydroNodeWsError(Exception):
    """Generic WS error that should trigger a reconnect."""


class _AuthFailed(HydroNodeWsError):
    """Raised on close code 4401 or an explicitly rejected handshake."""


class _SessionLimit(HydroNodeWsError):
    """Raised on close code 4429 (too many concurrent sessions)."""


def build_ws_url(base_url: str) -> str:
    """Translate an https(s)/http(s) base URL into the wss/ws endpoint URL."""
    url = normalize_base_url(base_url)
    if url.startswith("https://"):
        url = "wss://" + url[len("https://") :]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://") :]
    return f"{url}{WS_PATH}"


class HydroNodeWebSocketClient:
    """Maintains a persistent WS connection with first-message auth and backoff reconnect."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str,
        on_value_updated: DataCallback,
        on_incident_updated: DataCallback,
        on_analysis_completed: DataCallback,
        on_auth_failed: VoidCallback,
        on_reconnect: ReconnectCallback | None = None,
        expected_user_id: str | None = None,
    ) -> None:
        self._hass = hass
        self._session = session
        self._ws_url = build_ws_url(base_url)
        self._token = token
        self._on_value_updated = on_value_updated
        self._on_incident_updated = on_incident_updated
        self._on_analysis_completed = on_analysis_completed
        self._on_auth_failed = on_auth_failed
        self._on_reconnect = on_reconnect
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._expected_user_id = expected_user_id
        self._has_connected = False
        self._connected_at: float | None = None

    def start(self) -> None:
        """Start the background reconnect-loop task."""
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = self._hass.async_create_background_task(
            self._run(), "HydroNode websocket"
        )

    async def stop(self) -> None:
        """Cancel the background task and wait for it to finish."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        backoff = WS_MIN_BACKOFF
        while not self._stopped:
            try:
                await self._connect_once()
            except (_AuthFailed, HydroNodeAuthError):
                self._on_auth_failed()
                return
            except asyncio.CancelledError:
                raise
            except HydroNodeWsError as err:
                _LOGGER.debug("HydroNode WS closed, reconnecting: %s", err)
            except Exception:  # noqa: BLE001 - transport failure must reconnect
                _LOGGER.debug("HydroNode WS transport error, reconnecting")

            if self._stopped:
                return

            if self._connected_at is not None:
                if self._hass.loop.time() - self._connected_at >= 30:
                    backoff = WS_MIN_BACKOFF
                self._connected_at = None
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
            backoff = min(backoff * 2, WS_MAX_BACKOFF)

    async def _connect_once(self) -> None:
        async with self._session.ws_connect(
            self._ws_url, heartbeat=30, max_msg_size=1024 * 1024
        ) as ws:
            await ws.send_json({"type": MSG_TYPE_AUTH, "token": self._token})

            try:
                auth_msg = await asyncio.wait_for(ws.receive(), timeout=WS_AUTH_TIMEOUT)
            except asyncio.TimeoutError as err:
                await ws.close()
                raise HydroNodeWsError("auth response timeout") from err

            if auth_msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
            ):
                self._raise_for_close_code(ws.close_code)
                return

            payload = self._safe_json(auth_msg)
            if not payload or payload.get("type") != MSG_TYPE_AUTH_OK:
                await ws.close()
                raise HydroNodeWsError("invalid auth response")

            if (
                self._expected_user_id is not None
                and payload.get("userId") != self._expected_user_id
            ):
                raise _AuthFailed("WebSocket account mismatch")
            self._connected_at = self._hass.loop.time()
            _LOGGER.debug("HydroNode WS authenticated")

            reconnecting = self._has_connected
            self._has_connected = True
            if reconnecting and self._on_reconnect is not None:
                result = self._on_reconnect()
                if asyncio.iscoroutine(result):
                    await result

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._dispatch(msg)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    raise HydroNodeWsError("WS transport error")

            self._raise_for_close_code(ws.close_code)

    def _raise_for_close_code(self, close_code: int | None) -> None:
        if close_code == WS_CLOSE_AUTH_FAILED:
            raise _AuthFailed("server closed: auth failed")
        if close_code == WS_CLOSE_SESSION_LIMIT:
            raise _SessionLimit("server closed: too many sessions")
        raise HydroNodeWsError(f"WS closed with code {close_code}")

    def _dispatch(self, msg: aiohttp.WSMessage) -> None:
        payload = self._safe_json(msg)
        if payload is None:
            return
        msg_type = payload.get("type")
        data = payload.get("data")
        if not isinstance(data, dict):
            return

        handler = (
            {
                EVENT_TYPE_VALUE_UPDATED: self._on_value_updated,
                EVENT_TYPE_INCIDENT_UPDATED: self._on_incident_updated,
                EVENT_TYPE_ANALYSIS_COMPLETED: self._on_analysis_completed,
            }.get(msg_type)
            if isinstance(msg_type, str)
            else None
        )
        if handler is not None:
            try:
                handler(data)
            except Exception:  # noqa: BLE001 - one bad event must not drop the socket
                _LOGGER.debug("Ignoring malformed HydroNode event or failed listener")

    @staticmethod
    def _safe_json(msg: aiohttp.WSMessage) -> dict[str, Any] | None:
        try:
            result = json.loads(msg.data)
            return result if isinstance(result, dict) else None
        except (TypeError, ValueError, RecursionError):
            _LOGGER.debug("Ignoring invalid HydroNode WS message")
            return None
