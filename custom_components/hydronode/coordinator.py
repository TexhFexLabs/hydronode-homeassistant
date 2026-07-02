"""DataUpdateCoordinator for HydroNode value polling and bootstrap discovery."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import HydroNodeApiClient, HydroNodeApiError, HydroNodeAuthError
from .const import BOOTSTRAP_REFRESH_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# (sensor_id, type, channel_name) -> latest state payload
StateKey = tuple[str, str, str | None]

NewEntityListener = Callable[[dict[str, Any]], None]


class HydroNodeCoordinator(DataUpdateCoordinator[dict[StateKey, dict[str, Any]]]):
    """Polls `/api/ha/v1/states` and periodically refreshes the bootstrap discovery data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: HydroNodeApiClient,
        poll_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self.client = client
        self.bootstrap: dict[str, Any] = {"user": {}, "stations": []}
        self._last_bootstrap_at: float | None = None
        self.new_entity_listeners: list[NewEntityListener] = []

    def add_new_entity_listener(self, listener: NewEntityListener) -> None:
        """Register a callback invoked with the fresh bootstrap payload after every refresh."""
        self.new_entity_listeners.append(listener)

    async def async_refresh_bootstrap(self) -> dict[str, Any]:
        """Force a bootstrap refresh (initial setup, WS reconnect). Propagates API errors."""
        await self._refresh_bootstrap()
        return self.bootstrap

    async def _refresh_bootstrap(self) -> None:
        self.bootstrap = await self.client.bootstrap()
        self._last_bootstrap_at = dt_util.utcnow().timestamp()
        for listener in self.new_entity_listeners:
            listener(self.bootstrap)

    def _bootstrap_is_stale(self) -> bool:
        if self._last_bootstrap_at is None:
            return True
        age = dt_util.utcnow().timestamp() - self._last_bootstrap_at
        return age >= BOOTSTRAP_REFRESH_INTERVAL

    async def _async_update_data(self) -> dict[StateKey, dict[str, Any]]:
        if self._bootstrap_is_stale():
            try:
                await self._refresh_bootstrap()
            except HydroNodeAuthError as err:
                raise ConfigEntryAuthFailed("HydroNode token rejected") from err
            except HydroNodeApiError as err:
                # Bootstrap refresh is best-effort here; keep using stale
                # discovery data and still try to poll states below.
                _LOGGER.warning("HydroNode bootstrap refresh failed: %s", err)

        try:
            states = await self.client.states()
        except HydroNodeAuthError as err:
            raise ConfigEntryAuthFailed("HydroNode token rejected") from err
        except HydroNodeApiError as err:
            raise UpdateFailed(f"Error fetching HydroNode states: {err}") from err

        return self._index_states(states)

    @staticmethod
    def filter_stations(
        bootstrap: dict[str, Any], include_followed: bool
    ) -> list[dict[str, Any]]:
        """Return the station list, optionally dropping `FOLLOWED` (public) stations."""
        stations = bootstrap.get("stations", [])
        if include_followed:
            return stations
        return [station for station in stations if station.get("role") != "FOLLOWED"]

    @staticmethod
    def _index_states(states: list[dict[str, Any]]) -> dict[StateKey, dict[str, Any]]:
        indexed: dict[StateKey, dict[str, Any]] = {}
        for state in states:
            key: StateKey = (state["sensorId"], state["type"], state.get("channelName"))
            indexed[key] = state
        return indexed

    def apply_ws_value_update(self, data: dict[str, Any]) -> None:
        """Patch coordinator data in-place from a WS `value.updated` push."""
        key: StateKey = (data["sensorId"], data["type"], data.get("channelName"))
        current = dict(self.data or {})
        current[key] = {
            "sensorId": data["sensorId"],
            "type": data["type"],
            "channelName": data.get("channelName"),
            "value": data["value"],
            "timestamp": data["timestamp"],
        }
        self.async_set_updated_data(current)
