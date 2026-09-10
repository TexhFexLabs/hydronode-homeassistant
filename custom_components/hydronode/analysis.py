"""Shared plumbing for the entities derived from the latest `ai_analysis` payload.

The analysis only ever arrives as a WebSocket push (`anomaly.ai_analyzed`); neither
`/bootstrap` nor `/states` carries it. A single store therefore caches the last
payload per station and every analysis entity subscribes to that store instead of
registering its own listener on the WS fan-out.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)

# Event type used by __init__._handle_ai_analyzed when it fans out to listeners.
ANALYSIS_EVENT_TYPE = "ai_analysis"

# Keys are part of the entity unique_id, so they are API: renaming one orphans the
# existing entity and creates a fresh one.
KEY_AIR_QUALITY_SCORE = "air_quality_score"
KEY_ENVIRONMENT_SCORE = "environment_score"
KEY_CONFIDENCE = "confidence"
KEY_ALERT_SEVERITY = "alert_severity"
KEY_ANOMALY_TYPE = "anomaly_type"
KEY_ANALYSIS_TIMESTAMP = "analysis_timestamp"
KEY_VENTILATION_EVENT = "ventilation_event"
KEY_ALERT = "alert"

# Every analysis entity key, in one place: __init__._async_sync_registries needs it
# to keep the registry cleanup from deleting these entities on each bootstrap
# refresh. Defined here rather than in the platforms to avoid a circular import.
ANALYSIS_ENTITY_KEYS: tuple[str, ...] = (
    KEY_AIR_QUALITY_SCORE,
    KEY_ENVIRONMENT_SCORE,
    KEY_CONFIDENCE,
    KEY_ALERT_SEVERITY,
    KEY_ANOMALY_TYPE,
    KEY_ANALYSIS_TIMESTAMP,
    KEY_VENTILATION_EVENT,
    KEY_ALERT,
)


def build_analysis_unique_id(station_id: str, key: str) -> str:
    """Unique-id format shared by the platforms and the registry cleanup."""
    return f"{station_id}_analysis_{key}"


def station_id_of(data: dict[str, Any]) -> str | None:
    """Station a payload belongs to.

    A sensor without a station is represented by a pseudo-station whose id equals
    the sensor id, so a null `stationId` falls back to `sensorId`.
    """
    value = data.get("stationId") or data.get("sensorId")
    return value if isinstance(value, str) and value else None


def score_percent(field: str) -> Callable[[dict[str, Any]], float | None]:
    """Read a 0.0-1.0 score from `analysis` and return it as a percentage."""

    def _value(data: dict[str, Any]) -> float | None:
        raw = (data.get("analysis") or {}).get(field)
        if raw is None:
            return None
        try:
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                return None
            return round(value * 100, 1)
        except (TypeError, ValueError, OverflowError):
            _LOGGER.debug("HydroNode %s is not numeric", field)
            return None

    return _value


def analysis_flag(field: str) -> Callable[[dict[str, Any]], bool | None]:
    """Read a boolean straight out of `analysis`."""

    def _value(data: dict[str, Any]) -> bool | None:
        raw = (data.get("analysis") or {}).get(field)
        return bool(raw) if isinstance(raw, bool) else None

    return _value


def enum_value(
    read: Callable[[dict[str, Any]], Any], options: list[str], label: str
) -> Callable[[dict[str, Any]], str | None]:
    """Constrain a value to `options`.

    Home Assistant rejects an enum state outside the declared options, so an
    unexpected value is logged and reported as unknown rather than raising.
    """

    def _value(data: dict[str, Any]) -> str | None:
        raw = read(data)
        if raw is None:
            return None
        text = str(raw).lower()
        if text not in options:
            _LOGGER.debug("Unknown HydroNode %s", label)
            return None
        return text

    return _value


def analysis_timestamp(data: dict[str, Any]) -> datetime | None:
    """Parse the anomaly timestamp the analysis belongs to."""
    raw = data.get("timestamp")
    if not raw:
        return None
    parsed = dt_util.parse_datetime(str(raw))
    return dt_util.as_utc(parsed) if parsed is not None else None


class HydroNodeAnalysisStore:
    """Caches the latest `ai_analysis` payload per station and notifies subscribers."""

    def __init__(self) -> None:
        self._latest: dict[str, dict[str, Any]] = {}
        self._listeners: dict[str, list[Callable[[], None]]] = {}

    @callback
    def handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Event-listener callback. Plain `anomaly` events carry no analysis."""
        if event_type != ANALYSIS_EVENT_TYPE:
            return
        if not isinstance(data.get("analysis"), dict):
            return
        station_id = station_id_of(data)
        if station_id is None:
            return
        previous = self._latest.get(station_id)
        if previous is not None:
            timestamp = analysis_timestamp(data)
            previous_timestamp = analysis_timestamp(previous)
            if (
                timestamp is not None
                and previous_timestamp is not None
                and timestamp < previous_timestamp
            ):
                return
        self._latest[station_id] = data
        for listener in list(self._listeners.get(station_id, ())):
            listener()

    def retain_stations(self, station_ids: set[str]) -> None:
        """Release cached private analysis data after a station loses access."""
        for station_id in set(self._latest) - station_ids:
            self._latest.pop(station_id)

    def latest(self, station_id: str) -> dict[str, Any] | None:
        """Last analysis payload for a station, or None if none arrived yet."""
        return self._latest.get(station_id)

    @callback
    def async_add_listener(
        self, station_id: str, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Subscribe to updates for one station; returns the unsubscribe callable."""
        self._listeners.setdefault(station_id, []).append(listener)

        @callback
        def _remove() -> None:
            listeners = self._listeners.get(station_id)
            if listeners and listener in listeners:
                listeners.remove(listener)
                if not listeners:
                    self._listeners.pop(station_id, None)

        return _remove


class HydroNodeAnalysisEntity(Entity):
    """Base for the entities that render one field of the latest analysis."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        store: HydroNodeAnalysisStore,
        station: dict[str, Any],
        key: str,
    ) -> None:
        self._store = store
        self._station_id: str = station["id"]
        self._key = key
        self._attr_unique_id = build_analysis_unique_id(self._station_id, key)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station.get("name") or "HydroNode Station",
            manufacturer=MANUFACTURER,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to the store; platforms extend this to restore a last value."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._store.async_add_listener(self._station_id, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        """A fresh analysis arrived: live data supersedes any restored value."""
        self._clear_restored()
        self.async_write_ha_state()

    @callback
    def _clear_restored(self) -> None:
        """Drop the value restored across a restart. Overridden by the platforms."""

    @property
    def analysis(self) -> dict[str, Any] | None:
        """Latest payload for this station, or None before the first analysis."""
        return self._store.latest(self._station_id)
