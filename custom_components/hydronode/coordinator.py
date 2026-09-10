"""DataUpdateCoordinator for HydroNode value polling and bootstrap discovery."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import HydroNodeApiClient, HydroNodeApiError, HydroNodeAuthError
from .const import BOOTSTRAP_REFRESH_INTERVAL, DOMAIN
from .validation import valid_state

_LOGGER = logging.getLogger(__name__)

# (sensor_id, type, channel_name) -> latest state payload
StateKey = tuple[str, str, str | None]

NewEntityListener = Callable[[dict[str, Any]], None]


def build_unique_id(sensor_id: str, type_: str, channel: str | None) -> str:
    """Unique-id format shared by the sensor platform and the registry cleanup."""
    return f"{sensor_id}_{type_}_{channel or ''}"


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
        self.auth_failed = False
        self.expected_user_id: str | None = None
        self.sensor_active: dict[str, bool] = {}
        self._bootstrap_lock = asyncio.Lock()
        self.bootstrap: dict[str, Any] = {"user": {}, "stations": []}
        self._last_bootstrap_at: float | None = None
        self.new_entity_listeners: list[NewEntityListener] = []

    def add_new_entity_listener(
        self, listener: NewEntityListener
    ) -> Callable[[], None]:
        """Register a callback invoked with the fresh bootstrap payload after every refresh."""
        self.new_entity_listeners.append(listener)
        return lambda: self.new_entity_listeners.remove(listener)

    async def async_refresh_bootstrap(self) -> dict[str, Any]:
        """Force a bootstrap refresh (initial setup, WS reconnect). Propagates API errors."""
        await self._refresh_bootstrap()
        return self.bootstrap

    async def _refresh_bootstrap(self) -> None:
        async with self._bootstrap_lock:
            bootstrap = await self.client.bootstrap()
            user_id = bootstrap["user"]["id"]
            if self.expected_user_id is not None and user_id != self.expected_user_id:
                self.auth_failed = True
                raise HydroNodeAuthError("HydroNode account changed")
            self.expected_user_id = user_id
            self.bootstrap = bootstrap
            self.sensor_active = {
                sensor["id"]: sensor.get("active", True)
                for station in bootstrap.get("stations", [])
                for sensor in station.get("sensors", [])
            }
            self._last_bootstrap_at = dt_util.utcnow().timestamp()
            for listener in list(self.new_entity_listeners):
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
                self.auth_failed = True
                raise ConfigEntryAuthFailed("HydroNode token rejected") from err
            except HydroNodeApiError as err:
                # Bootstrap refresh is best-effort here; keep using stale
                # discovery data and still try to poll states below.
                _LOGGER.warning("HydroNode bootstrap refresh failed: %s", err)

        try:
            states = await self.client.states()
        except HydroNodeAuthError as err:
            self.auth_failed = True
            raise ConfigEntryAuthFailed("HydroNode token rejected") from err
        except HydroNodeApiError as err:
            raise UpdateFailed(f"Error fetching HydroNode states: {err}") from err

        self.auth_failed = False
        indexed = self._index_states(states)
        # A poll started before a push must not overwrite the newer measurement.
        # Only merge keys still in REST, so revoked/deleted sensors disappear.
        for key, state in indexed.items():
            current = (self.data or {}).get(key)
            if current is not None and self._newer(current, state):
                indexed[key] = current
        return indexed

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

    @staticmethod
    def _newer(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
        candidate_time = dt_util.parse_datetime(candidate["timestamp"])
        current_time = dt_util.parse_datetime(current["timestamp"])
        return candidate_time > current_time

    def apply_ws_value_update(self, data: dict[str, Any]) -> bool:
        """Apply a valid push without postponing REST polling or copying every state."""
        if self.auth_failed or not valid_state(data):
            return False
        key: StateKey = (data["sensorId"], data["type"], data.get("channelName"))
        current = self.data
        if current is None:
            current = self.data = {}
        previous = current.get(key)
        updated = {
            field: data.get(field)
            for field in ("sensorId", "type", "channelName", "value", "timestamp")
        }
        if previous is not None and (
            previous == updated or self._newer(previous, updated)
        ):
            return False
        current[key] = updated
        # async_set_updated_data resets the polling timer. Continuous pushes would
        # otherwise suppress discovery, stale-value checks and token revalidation.
        self.async_update_listeners()
        return True
