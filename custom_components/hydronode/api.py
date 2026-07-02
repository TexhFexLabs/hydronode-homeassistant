"""Async REST client for the HydroNode Home Assistant API."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import BOOTSTRAP_PATH, STATES_PATH

_LOGGER = logging.getLogger(__name__)


class HydroNodeApiError(Exception):
    """Base error raised by the HydroNode API client."""


class HydroNodeAuthError(HydroNodeApiError):
    """Raised when the server rejects the Personal Access Token (HTTP 401)."""


class HydroNodeConnectionError(HydroNodeApiError):
    """Raised on network/transport failures reaching the HydroNode backend."""


class HydroNodeApiClient:
    """Thin async wrapper around `/api/ha/v1/*`.

    Only the two endpoints the HA integration needs for v1 (bootstrap discovery
    and states polling) are implemented. Token management and follow/unfollow
    are handled outside of Home Assistant (web/app UI) for now.
    """

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = token

    @property
    def base_url(self) -> str:
        """Return the configured base URL (no trailing slash)."""
        return self._base_url

    @property
    def token(self) -> str:
        """Return the configured Personal Access Token."""
        return self._token

    async def bootstrap(self) -> dict[str, Any]:
        """Fetch `GET /api/ha/v1/bootstrap` — all discoverable stations/sensors."""
        result = await self._get(BOOTSTRAP_PATH)
        return result if isinstance(result, dict) else {}

    async def states(self) -> list[dict[str, Any]]:
        """Fetch `GET /api/ha/v1/states` — latest value per (sensor, type, channel)."""
        result = await self._get(STATES_PATH)
        return result if isinstance(result, list) else []

    async def _get(self, path: str) -> Any:
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 401:
                    raise HydroNodeAuthError(f"Authentication rejected for {path}")
                if response.status >= 400:
                    body = await response.text()
                    raise HydroNodeApiError(
                        f"Unexpected status {response.status} for {path}: {body}"
                    )
                return await response.json(content_type=None)
        except aiohttp.ClientError as err:
            raise HydroNodeConnectionError(f"Connection error for {path}: {err}") from err
