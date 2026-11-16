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
from .data_points import point_index
from .validation import valid_desired, valid_point_state, valid_state

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
        # Data platform: latest row per data point id, and which old (sensor,
        # type, channel) key a device data point shares, for WS pushes.
        self.point_states: dict[str, dict[str, Any]] = {}
        self.point_by_key: dict[StateKey, dict[str, Any]] = {}

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
            self.point_by_key = point_index(bootstrap)
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
        self.point_states = self._merge_points(
            {
                state["dataPointId"]: state
                for state in states
                if state.get("dataPointId") is not None
            }
        )
        indexed = self._index_states(
            [state for state in states if state.get("dataPointId") is None]
        )
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

    def _merge_points(
        self, fresh: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Keep a pushed or written row that is newer than the poll result."""
        for point_id, state in fresh.items():
            current = self.point_states.get(point_id)
            if current is not None and self._newer(current, state):
                fresh[point_id] = {**current, "desired": state.get("desired")}
        return fresh

    def apply_point_state(self, point_id: str, row: dict[str, Any]) -> None:
        """A write answered with the data point: show it before the next poll."""
        self.point_states[point_id] = row
        self.async_update_listeners()

    @staticmethod
    def _newer(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
        candidate_time = dt_util.parse_datetime(candidate["timestamp"])
        current_time = dt_util.parse_datetime(current["timestamp"])
        return candidate_time > current_time

    def apply_ws_value_update(self, data: dict[str, Any]) -> bool:
        """Apply a valid push without postponing REST polling or copying every state."""
        if self.auth_failed or not valid_state(data):
            return False
        if data.get("dataPointId") is not None:
            return self._apply_ws_point(data)
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
        point = self.point_by_key.get(key)
        if point is not None:
            # A device data point read as on/off or a choice shares this series.
            previous_point = self.point_states.get(point["id"], {})
            self.point_states[point["id"]] = {
                **previous_point,
                **updated,
                "dataPointId": point["id"],
                "label": dict(point["options"]).get(updated["value"])
                if updated["value"] == int(updated["value"])
                else None,
            }
        # async_set_updated_data resets the polling timer. Continuous pushes would
        # otherwise suppress discovery, stale-value checks and token revalidation.
        self.async_update_listeners()
        return True

    def apply_ws_desired(self, data: dict[str, Any]) -> bool:
        """`desired.updated`: a switch went pending, confirmed or failed.

        Only the desired part of the row changes, so `write_state` follows at once.
        A confirmed switch newer than the row's value is the real state (a relay
        without its own report), like `/states` shows it.
        """
        if self.auth_failed or not valid_desired(data):
            return False
        point_id = data["dataPointId"]
        desired = data["desired"]
        previous = self.point_states.get(point_id)
        row = (
            dict(previous)
            if previous is not None
            else {
                "sensorId": data["sensorId"],
                "dataPointId": point_id,
                "value": None,
                "timestamp": desired["at"],
            }
        )
        if row.get("desired") == desired:
            return False
        row["desired"] = desired
        confirmed_value = desired.get("value")
        if (
            desired["state"] == "CONFIRMED"
            and type(confirmed_value) in (int, float)
            and (row.get("value") is None or self._older(row, desired["at"]))
        ):
            row["value"] = confirmed_value
            row["label"] = desired.get("label")
            row["timestamp"] = desired["at"]
        self.point_states[point_id] = row
        self.async_update_listeners()
        return True

    @staticmethod
    def _older(row: dict[str, Any], timestamp: str) -> bool:
        current = dt_util.parse_datetime(row.get("timestamp") or "")
        candidate = dt_util.parse_datetime(timestamp)
        return current is None or (candidate is not None and candidate > current)

    def _apply_ws_point(self, data: dict[str, Any]) -> bool:
        """A push of a data platform server: routed by its data point id.

        Every kind arrives this way, also texts and the references of virtual
        sensors (`referenceOf`, the id is the reference). A device value also
        updates its old-style entity, so both stay in step.
        """
        if not valid_point_state(data):
            return False
        point_id = data["dataPointId"]
        previous_point = self.point_states.get(point_id)
        row = {
            field: data.get(field)
            for field in (
                "sensorId",
                "type",
                "channelName",
                "value",
                "timestamp",
                "dataPointId",
                "text",
                "label",
            )
        }
        if previous_point is not None:
            row["desired"] = previous_point.get("desired")
        changed = previous_point is None or (
            not self._newer(previous_point, row)
            and {field: previous_point.get(field) for field in row} != row
        )
        if changed:
            self.point_states[point_id] = row
        legacy = False
        if data.get("referenceOf") is None:
            key: StateKey = (data["sensorId"], data["type"], data.get("channelName"))
            current = self.data
            if current is None:
                current = self.data = {}
            previous = current.get(key)
            updated = {
                field: data.get(field)
                for field in ("sensorId", "type", "channelName", "value", "timestamp")
            }
            if previous is None or not (
                previous == updated or self._newer(previous, updated)
            ):
                current[key] = updated
                legacy = True
        if not (changed or legacy):
            return False
        self.async_update_listeners()
        return True
