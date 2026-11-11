"""Async REST client for the HydroNode Home Assistant API."""

from __future__ import annotations

import json
from typing import Any

import aiohttp
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import (
    BOOTSTRAP_PATH,
    CAPABILITIES,
    CAPABILITIES_HEADER,
    FULL_TOKEN_PREFIX,
    INSIGHTS_PATH,
    POINT_VALUE_PATH,
    STATES_PATH,
)
from .validation import nonempty_string, valid_bootstrap, valid_point_state, valid_state

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class HydroNodeApiError(Exception):
    """Base error raised by the HydroNode API client."""


class HydroNodeAuthError(HydroNodeApiError):
    """The server rejected the token or its permissions."""


class HydroNodeNotFound(HydroNodeApiError):
    """The endpoint does not exist (older backend)."""


class HydroNodeConnectionError(HydroNodeApiError):
    """Network/transport failure reaching the backend."""


class HydroNodeInvalidUrl(HydroNodeApiError):
    """Invalid or unsafe base URL."""


class HydroNodeWriteError(HydroNodeApiError):
    """The server refused a write; `code` is its error code (never the body)."""

    def __init__(self, status: int, code: str | None) -> None:
        super().__init__(f"Write refused: {status} {code or ''}".strip())
        self.status = status
        self.code = code


def normalize_base_url(base_url: str) -> str:
    """Accept HTTP(S) deployments without URL credentials, queries or fragments."""
    try:
        raw = base_url.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in raw):
            raise ValueError
        url = URL(raw)
        if (
            url.scheme not in ("http", "https")
            or not url.host
            or url.user is not None
            or url.query_string
            or url.fragment
            or url.port is None
        ):
            raise ValueError
        if (
            url.host in ("hydronode.tech", "hydronode.texhfexlabs.de")
            and url.scheme != "https"
        ):
            raise ValueError
        return str(url).rstrip("/")
    except (TypeError, ValueError, AttributeError) as err:
        raise HydroNodeInvalidUrl(
            "Use an HTTP(S) base URL without credentials or query parameters"
        ) from err


def async_create_hydronode_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Share HA's connector, but prohibit redirects before sending WS auth data.

    aiohttp's ws_connect follows HTTP redirects and authenticates afterwards.
    A trace hook stops that redirect before a token can reach another endpoint.
    HA detaches this session on config-entry unload, retaining its shared pool.
    """
    trace = aiohttp.TraceConfig()

    async def reject_redirect(*_args: Any) -> None:
        raise HydroNodeConnectionError(
            "HydroNode redirects are disabled; reconfigure the base URL"
        )

    trace.on_request_redirect.append(reject_redirect)
    return async_create_clientsession(
        hass, trace_configs=[trace], timeout=REQUEST_TIMEOUT
    )


class HydroNodeApiClient:
    """One bootstrap request for discovery and one states request per poll."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, token: str
    ) -> None:
        self._session = session
        self._base_url = normalize_base_url(base_url)
        self._token = token.strip()
        if not self._token or any(
            char.isspace() or ord(char) < 32 for char in self._token
        ):
            raise HydroNodeAuthError("Invalid token format")

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def can_write(self) -> bool:
        """Only full-access tokens (hat_) switch anything; hrt_ stays read only."""
        return self._token.startswith(FULL_TOKEN_PREFIX)

    async def bootstrap(self) -> dict[str, Any]:
        result = await self._get(BOOTSTRAP_PATH)
        if not valid_bootstrap(result):
            raise HydroNodeApiError("Invalid bootstrap response")
        return result

    async def states(self) -> list[dict[str, Any]]:
        result = await self._get(STATES_PATH)
        if not isinstance(result, list):
            raise HydroNodeApiError("Invalid states response")
        # Data point rows (data platform) are checked one by one: a bad one is
        # dropped instead of failing the whole poll. Old rows stay strict.
        states = [
            state
            for state in result
            if not (isinstance(state, dict) and "dataPointId" in state)
            or valid_point_state(state)
        ]
        if not all(valid_state(state) for state in states):
            raise HydroNodeApiError("Invalid states response")
        return states

    async def write_value(self, point_id: str, value: float) -> dict[str, Any]:
        """Set a writable data point (`PUT /api/data/v1/points/{id}/value`).

        Returns the data point object of the answer. Refusals raise
        HydroNodeWriteError with the server's error code.
        """
        if not self.can_write:
            raise HydroNodeWriteError(403, "READ_TOKEN_SCOPE")
        if not nonempty_string(point_id) or "/" in point_id or "?" in point_id:
            raise HydroNodeApiError("Invalid data point id")
        path = POINT_VALUE_PATH.format(point_id=point_id)
        try:
            async with self._session.put(
                f"{self._base_url}{path}",
                headers=self._headers(),
                json={"value": value},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                body = await self._read_json(response, path)
                if response.status in (200, 202):
                    return body if isinstance(body, dict) else {}
                if response.status == 401:
                    raise HydroNodeAuthError("HydroNode token rejected")
                code = body.get("error") if isinstance(body, dict) else None
                raise HydroNodeWriteError(
                    response.status, code if isinstance(code, str) else None
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HydroNodeConnectionError(f"Connection failed for {path}") from err

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            CAPABILITIES_HEADER: CAPABILITIES,
        }

    @staticmethod
    async def _read_json(response: aiohttp.ClientResponse, path: str) -> Any:
        body = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise HydroNodeApiError("HydroNode response exceeds size limit")
        if not body:
            return None
        try:
            return json.loads(body)
        except (ValueError, UnicodeError, RecursionError) as err:
            raise HydroNodeApiError(f"Invalid JSON response for {path}") from err

    async def insights(self) -> dict[str, Any] | None:
        """Open incidents and newest analyses per station.

        Returns None when the backend predates the endpoint (404), so an older
        server just leaves the insight entities empty instead of failing setup.
        """
        try:
            result = await self._get(INSIGHTS_PATH)
        except HydroNodeNotFound:
            return None
        if not isinstance(result, dict) or not isinstance(result.get("stations"), list):
            raise HydroNodeApiError("Invalid insights response")
        return result

    async def _get(self, path: str) -> Any:
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise HydroNodeAuthError("HydroNode token or permissions rejected")
                if response.status == 404:
                    raise HydroNodeNotFound(f"Not found: {path}")
                if response.status != 200:
                    # Remote response bodies can contain credentials or personal data.
                    raise HydroNodeApiError(
                        f"Unexpected status {response.status} for {path}"
                    )
                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise HydroNodeApiError("HydroNode response exceeds size limit")
                return json.loads(body)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HydroNodeConnectionError(f"Connection failed for {path}") from err
        except (ValueError, UnicodeError, RecursionError) as err:
            raise HydroNodeApiError(f"Invalid JSON response for {path}") from err
