"""Async REST client for the HydroNode Home Assistant API."""

from __future__ import annotations

import json
from typing import Any

import aiohttp
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import BOOTSTRAP_PATH, INSIGHTS_PATH, STATES_PATH
from .validation import valid_bootstrap, valid_state

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

    async def bootstrap(self) -> dict[str, Any]:
        result = await self._get(BOOTSTRAP_PATH)
        if not valid_bootstrap(result):
            raise HydroNodeApiError("Invalid bootstrap response")
        return result

    async def states(self) -> list[dict[str, Any]]:
        result = await self._get(STATES_PATH)
        if not isinstance(result, list) or not all(
            valid_state(state) for state in result
        ):
            raise HydroNodeApiError("Invalid states response")
        return result

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
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                headers=headers,
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
