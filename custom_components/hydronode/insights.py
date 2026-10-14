"""Incidents (V2) and analyses for the station devices.

Both arrive twice: as a snapshot from `GET /api/ha/v1/insights` (setup, every
bootstrap refresh, WebSocket reconnect) and live as `incident.updated` /
`analysis.completed` pushes. One store holds the merged view per station and every
insight entity subscribes to it instead of registering its own WS listener.

Remote payloads are normalized once here, so entities only ever read clean,
snake_case dictionaries and a malformed field cannot reach a state attribute.
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

OPEN_LIFECYCLES = frozenset({"OPEN", "ONGOING", "RECOVERING"})
CLOSED_LIFECYCLES = frozenset({"RESOLVED", "EXPIRED", "SUPPRESSED", "INVALIDATED"})
KNOWN_LIFECYCLES = OPEN_LIFECYCLES | CLOSED_LIFECYCLES

# Lowest to highest. HA enum states and translation keys are lowercase.
SEVERITY_ORDER: tuple[str, ...] = ("info", "warning", "high", "critical")
SEVERITY_NONE = "none"
SEVERITY_OPTIONS: list[str] = [SEVERITY_NONE, *SEVERITY_ORDER]

# Event-entity event types. They are API for automations: never rename.
EVENT_INCIDENT_OPENED = "incident_opened"
EVENT_INCIDENT_ESCALATED = "incident_escalated"
EVENT_INCIDENT_RECOVERED = "incident_recovered"
EVENT_INCIDENT_CLOSED = "incident_closed"
EVENT_ANALYSIS_READY = "analysis_ready"
EVENT_TYPES: list[str] = [
    EVENT_INCIDENT_OPENED,
    EVENT_INCIDENT_ESCALATED,
    EVENT_INCIDENT_RECOVERED,
    EVENT_INCIDENT_CLOSED,
    EVENT_ANALYSIS_READY,
]

# Unique-id keys. Part of the entity unique_id, so they are API as well.
KEY_INCIDENTS_OPEN = "incidents_open"
KEY_INCIDENT_SEVERITY = "incident_severity"
KEY_INCIDENT = "incident"
KEY_EVENTS = "events"
KEY_ANALYSIS = "analysis"
KEY_AIR_QUALITY_INDEX = "air_quality_index"
KEY_ANALYSIS_TIME = "analysis_time"

STATION_ENTITY_KEYS: tuple[str, ...] = (
    KEY_INCIDENTS_OPEN,
    KEY_INCIDENT_SEVERITY,
    KEY_INCIDENT,
    KEY_EVENTS,
)
SENSOR_ENTITY_KEYS: tuple[str, ...] = (
    KEY_ANALYSIS,
    KEY_AIR_QUALITY_INDEX,
    KEY_ANALYSIS_TIME,
)

# Defensive caps for remote lists and texts that end up in state attributes.
MAX_INCIDENTS_PER_STATION = 100
MAX_LIST_ITEMS = 20
MAX_TEXT = 4000
MAX_SHORT_TEXT = 255
MAX_REMEMBERED_REVISIONS = 5000


def station_unique_id(station_id: str, key: str) -> str:
    """Unique id of a per-station insight entity."""
    return f"{station_id}_{key}"


def sensor_unique_id(station_id: str, sensor_id: str, key: str) -> str:
    """Unique id of a per-sensor analysis entity on its station device."""
    return f"{station_id}_{sensor_id}_{key}"


def station_device_info(station: dict[str, Any]) -> DeviceInfo:
    """Same device as the measurement entities, so everything shares one device."""
    return DeviceInfo(
        identifiers={(DOMAIN, station["id"])},
        name=station.get("name") or "HydroNode Station",
        manufacturer=MANUFACTURER,
    )


# --- normalization ----------------------------------------------------------------


def _text(value: Any, limit: int = MAX_SHORT_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _number(value: Any) -> float | int | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return value


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt_util.parse_datetime(value)
    except (ValueError, OverflowError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return dt_util.as_utc(parsed)


def _iso(value: Any) -> str | None:
    parsed = _timestamp(value)
    return parsed.isoformat() if parsed is not None else None


def severity_of(value: Any) -> str | None:
    """Wire severity (INFO|WARNING|HIGH|CRITICAL) as lowercase HA state."""
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    return lowered if lowered in SEVERITY_ORDER else None


def normalize_incident(data: Any) -> dict[str, Any] | None:
    """HaIncident -> internal dict, or None if it cannot be identified."""
    if not isinstance(data, dict):
        return None
    incident_id = _text(data.get("incidentId"))
    sensor_id = _text(data.get("sensorId"))
    lifecycle = data.get("lifecycle")
    if (
        incident_id is None
        or sensor_id is None
        or not isinstance(lifecycle, str)
        or lifecycle.upper() not in KNOWN_LIFECYCLES
    ):
        return None
    channel = data.get("channel")
    channel_name = channel_type = None
    if isinstance(channel, dict):
        channel_type = _text(channel.get("canonicalType"))
        channel_name = _text(channel.get("displayName")) or channel_type
    count = data.get("occurrenceCount")
    return {
        "incident_id": incident_id,
        "sensor_id": sensor_id,
        "sensor_name": _text(data.get("sensorName")),
        "channel": channel_name,
        "channel_type": channel_type,
        "category": _text(data.get("category")),
        "severity": severity_of(data.get("severity")),
        "lifecycle": lifecycle.upper(),
        "direction": _text(data.get("direction")),
        "trigger_value": _number(data.get("triggerValue")),
        "occurrence_count": count if type(count) is int else None,
        "action_required": data.get("actionRequired") is True,
        "first_seen_at": _iso(data.get("firstSeenAt")),
        "last_seen_at": _iso(data.get("lastSeenAt")),
        "recovered_at": _iso(data.get("recoveredAt")),
    }


def _findings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    findings = []
    for item in value[:MAX_LIST_ITEMS]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"))
        detail = _text(item.get("detail"), MAX_TEXT)
        if title is None and detail is None:
            continue
        findings.append(
            {
                "title": title,
                "detail": detail,
                "certainty": _text(item.get("certainty")),
            }
        )
    return findings


def _recommendations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    recommendations = []
    for item in value[:MAX_LIST_ITEMS]:
        if not isinstance(item, dict):
            continue
        text = _text(item.get("text"), MAX_TEXT)
        if text is None:
            continue
        recommendations.append(
            {"action_code": _text(item.get("actionCode")), "text": text}
        )
    return recommendations


def _air_quality(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    score = _number(value.get("value"))
    if score is None or not 0 <= score <= 100:
        return None
    return {
        "value": score,
        "band": _text(value.get("band")),
        "limiting_pollutant": _text(value.get("limitingPollutant")),
    }


def normalize_analysis(data: Any) -> dict[str, Any] | None:
    """HaAnalysis -> internal dict, or None without id, sensor or generatedAt."""
    if not isinstance(data, dict):
        return None
    analysis_id = _text(data.get("analysisId"))
    sensor_id = _text(data.get("sensorId"))
    generated_at = _timestamp(data.get("generatedAt"))
    if analysis_id is None or sensor_id is None or generated_at is None:
        return None
    should_alert = data.get("shouldAlert")
    return {
        "analysis_id": analysis_id,
        "sensor_id": sensor_id,
        "sensor_name": _text(data.get("sensorName")),
        "generated_at": generated_at,
        "source": _text(data.get("source")),
        "severity": severity_of(data.get("severity")),
        "should_alert": should_alert if isinstance(should_alert, bool) else None,
        "summary": _text(data.get("summary"), MAX_TEXT),
        "findings": _findings(data.get("findings")),
        "recommendations": _recommendations(data.get("recommendations")),
        "air_quality": _air_quality(data.get("airQuality")),
        "environment_score": _number(data.get("environmentScore")),
    }


def incident_event_type(lifecycle: str, transition: dict[str, Any]) -> str | None:
    """Map one lifecycle transition to an event-entity event type, if any.

    Intermediate steps (still_present, recovering, condition_returned, ...) only
    update the store and the bus event; they would flood the event entity.
    """
    if lifecycle == "RESOLVED":
        return EVENT_INCIDENT_RECOVERED
    if lifecycle in CLOSED_LIFECYCLES:
        return EVENT_INCIDENT_CLOSED
    reason = transition.get("reason")
    if reason == "first_detection" or (
        lifecycle == "OPEN" and transition.get("fromLifecycle") in (None, "CANDIDATE")
    ):
        return EVENT_INCIDENT_OPENED
    if reason == "severity_raised":
        return EVENT_INCIDENT_ESCALATED
    return None


def incident_summary(incident: dict[str, Any]) -> dict[str, Any]:
    """Compact form for the `incidents` attribute of the Open incidents sensor."""
    return {
        "sensor_name": incident["sensor_name"],
        "channel": incident["channel"],
        "category": incident["category"],
        "severity": incident["severity"],
        "lifecycle": incident["lifecycle"].lower(),
        "since": incident["first_seen_at"],
        "last_seen": incident["last_seen_at"],
        "action_required": incident["action_required"],
    }


def incident_event_attributes(
    incident: dict[str, Any], transition: dict[str, Any]
) -> dict[str, Any]:
    """Attributes of an incident_* event on the event entity."""
    return {
        "incident_id": incident["incident_id"],
        "sensor_id": incident["sensor_id"],
        "sensor_name": incident["sensor_name"],
        "channel": incident["channel"],
        "category": incident["category"],
        "severity": incident["severity"],
        "lifecycle": incident["lifecycle"].lower(),
        "reason": _text(transition.get("reason")),
        "from_lifecycle": (_text(transition.get("fromLifecycle")) or "").lower()
        or None,
        "trigger_value": incident["trigger_value"],
        "action_required": incident["action_required"],
    }


def analysis_event_attributes(analysis: dict[str, Any]) -> dict[str, Any]:
    """Attributes of an analysis_ready event on the event entity."""
    air_quality = analysis["air_quality"]
    return {
        "analysis_id": analysis["analysis_id"],
        "sensor_id": analysis["sensor_id"],
        "sensor_name": analysis["sensor_name"],
        "severity": analysis["severity"] or SEVERITY_NONE,
        "should_alert": analysis["should_alert"],
        "summary": analysis["summary"],
        "source": analysis["source"],
        "generated_at": analysis["generated_at"].isoformat(),
        "air_quality_index": air_quality["value"] if air_quality else None,
    }


def _last_seen_key(incident: dict[str, Any]) -> str:
    return incident["last_seen_at"] or ""


# --- store --------------------------------------------------------------------------


class HydroNodeInsightsStore:
    """Open incidents and the newest analysis per sensor, grouped by station."""

    def __init__(self) -> None:
        self._incidents: dict[str, dict[str, dict[str, Any]]] = {}
        self._analyses: dict[str, dict[str, dict[str, Any]]] = {}
        self._revisions: dict[str, int] = {}
        # Pushes applied while a snapshot request is in flight must win over it.
        self._ws_seq = 0
        self._incident_pushes: dict[str, tuple[int, str, dict[str, Any] | None]] = {}
        self._analysis_pushes: dict[tuple[str, str], int] = {}
        self._listeners: dict[str, list[Callable[[], None]]] = {}
        self._analysis_listeners: list[Callable[[], None]] = []

    # -- reads

    def open_incidents(self, station_id: str) -> list[dict[str, Any]]:
        """Open incidents of a station, most recently seen first."""
        incidents = self._incidents.get(station_id, {}).values()
        return sorted(incidents, key=_last_seen_key, reverse=True)

    def highest_severity(self, station_id: str) -> str:
        """Highest severity among the open incidents, `none` without any."""
        best = -1
        for incident in self._incidents.get(station_id, {}).values():
            severity = incident["severity"]
            if severity is not None:
                best = max(best, SEVERITY_ORDER.index(severity))
        return SEVERITY_ORDER[best] if best >= 0 else SEVERITY_NONE

    def analysis(self, station_id: str, sensor_id: str) -> dict[str, Any] | None:
        return self._analyses.get(station_id, {}).get(sensor_id)

    def analyses(self, station_id: str) -> dict[str, dict[str, Any]]:
        return dict(self._analyses.get(station_id, {}))

    def diagnostics(self) -> dict[str, int]:
        """Counts only: incident and analysis texts can contain private data."""
        return {
            "stations_with_incidents": sum(1 for v in self._incidents.values() if v),
            "open_incidents": sum(len(v) for v in self._incidents.values()),
            "analyses": sum(len(v) for v in self._analyses.values()),
        }

    # -- live pushes

    @callback
    def apply_incident(
        self, station_id: str, data: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], str | None] | None:
        """Apply `incident.updated`; returns (incident, transition, event type).

        Returns None for malformed payloads and for a revision older than one
        already applied (the WS may deliver out of order across reconnects).
        """
        incident = normalize_incident(data)
        if incident is None:
            return None
        transition = data.get("transition")
        if not isinstance(transition, dict):
            transition = {}
        incident_id = incident["incident_id"]
        revision = transition.get("revision")
        if type(revision) is int:
            known = self._revisions.get(incident_id)
            if known is not None and revision <= known:
                return None
            self._revisions.pop(incident_id, None)
            self._revisions[incident_id] = revision
            while len(self._revisions) > MAX_REMEMBERED_REVISIONS:
                self._revisions.pop(next(iter(self._revisions)))

        changed = self._remove_incident(incident_id)
        is_open = incident["lifecycle"] in OPEN_LIFECYCLES
        if is_open:
            self._incidents.setdefault(station_id, {})[incident_id] = incident
            changed.add(station_id)
        self._ws_seq += 1
        self._incident_pushes[incident_id] = (
            self._ws_seq,
            station_id,
            incident if is_open else None,
        )
        self._notify(changed)
        return (
            incident,
            transition,
            incident_event_type(incident["lifecycle"], transition),
        )

    @callback
    def apply_analysis(
        self, station_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply `analysis.completed` if it is newer than the known one."""
        analysis = normalize_analysis(data)
        if analysis is None:
            return None
        sensor_id = analysis["sensor_id"]
        current = self.analysis(station_id, sensor_id)
        if current is not None and analysis["generated_at"] <= current["generated_at"]:
            return None
        self._analyses.setdefault(station_id, {})[sensor_id] = analysis
        self._ws_seq += 1
        self._analysis_pushes[(station_id, sensor_id)] = self._ws_seq
        self._notify({station_id})
        self._notify_analyses()
        return analysis

    # -- snapshots

    def begin_snapshot(self) -> int:
        """Mark the start of an /insights request; pass the result to apply_snapshot."""
        return self._ws_seq

    @callback
    def apply_snapshot(
        self,
        started: int,
        incidents: dict[str, list[dict[str, Any]]],
        analyses: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Replace the state with a REST snapshot (normalized, already filtered)."""
        new_incidents: dict[str, dict[str, dict[str, Any]]] = {}
        for station_id, items in incidents.items():
            for incident in items[:MAX_INCIDENTS_PER_STATION]:
                if incident["lifecycle"] in OPEN_LIFECYCLES:
                    new_incidents.setdefault(station_id, {})[
                        incident["incident_id"]
                    ] = incident
        for incident_id, (seq, station_id, incident) in self._incident_pushes.items():
            if seq <= started:
                continue
            for station_incidents in new_incidents.values():
                station_incidents.pop(incident_id, None)
            if incident is not None:
                new_incidents.setdefault(station_id, {})[incident_id] = incident
        self._incident_pushes = {
            key: value
            for key, value in self._incident_pushes.items()
            if value[0] > started
        }

        new_analyses: dict[str, dict[str, dict[str, Any]]] = {}
        for station_id, items in analyses.items():
            for analysis in items:
                current = new_analyses.setdefault(station_id, {}).get(
                    analysis["sensor_id"]
                )
                if (
                    current is None
                    or analysis["generated_at"] > current["generated_at"]
                ):
                    new_analyses[station_id][analysis["sensor_id"]] = analysis
        for station_id, by_sensor in self._analyses.items():
            for sensor_id, analysis in by_sensor.items():
                snapshot = new_analyses.get(station_id, {}).get(sensor_id)
                pushed_late = (
                    self._analysis_pushes.get((station_id, sensor_id), 0) > started
                )
                if (snapshot is None and pushed_late) or (
                    snapshot is not None
                    and analysis["generated_at"] > snapshot["generated_at"]
                ):
                    new_analyses.setdefault(station_id, {})[sensor_id] = analysis
        self._analysis_pushes = {
            key: seq for key, seq in self._analysis_pushes.items() if seq > started
        }

        new_incidents = {k: v for k, v in new_incidents.items() if v}
        new_analyses = {k: v for k, v in new_analyses.items() if v}
        changed = {
            station_id
            for station_id in set(self._incidents) | set(new_incidents)
            if self._incidents.get(station_id) != new_incidents.get(station_id)
        } | {
            station_id
            for station_id in set(self._analyses) | set(new_analyses)
            if self._analyses.get(station_id) != new_analyses.get(station_id)
        }
        added_analysis = any(
            sensor_id not in self._analyses.get(station_id, {})
            or (
                self._analyses[station_id][sensor_id]["air_quality"] is None
                and analysis["air_quality"] is not None
            )
            for station_id, by_sensor in new_analyses.items()
            for sensor_id, analysis in by_sensor.items()
        )
        self._incidents = new_incidents
        self._analyses = new_analyses
        self._notify(changed)
        if added_analysis:
            self._notify_analyses()

    @callback
    def retain(self, sensor_station: dict[str, str]) -> None:
        """Drop data of sensors that left the bootstrap (revoked share, unfollow)."""
        changed: set[str] = set()
        for store in (self._incidents, self._analyses):
            for station_id in list(store):
                items = store[station_id]
                for key, item in list(items.items()):
                    if sensor_station.get(item["sensor_id"]) != station_id:
                        items.pop(key)
                        changed.add(station_id)
                if not items:
                    store.pop(station_id)
        self._notify(changed)

    def _remove_incident(self, incident_id: str) -> set[str]:
        changed: set[str] = set()
        for station_id in list(self._incidents):
            items = self._incidents[station_id]
            if items.pop(incident_id, None) is not None:
                changed.add(station_id)
                if not items:
                    self._incidents.pop(station_id)
        return changed

    # -- listeners

    @callback
    def async_add_listener(
        self, station_id: str, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Subscribe to changes of one station; returns the unsubscribe callable."""
        self._listeners.setdefault(station_id, []).append(listener)

        @callback
        def _remove() -> None:
            listeners = self._listeners.get(station_id)
            if listeners and listener in listeners:
                listeners.remove(listener)
                if not listeners:
                    self._listeners.pop(station_id, None)

        return _remove

    @callback
    def async_add_analysis_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Called when a sensor gains its first analysis or air quality index."""
        self._analysis_listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._analysis_listeners:
                self._analysis_listeners.remove(listener)

        return _remove

    def _notify(self, station_ids: set[str]) -> None:
        for station_id in station_ids:
            for listener in list(self._listeners.get(station_id, ())):
                listener()

    def _notify_analyses(self) -> None:
        for listener in list(self._analysis_listeners):
            listener()


# --- entity bases -------------------------------------------------------------------


class HydroNodeInsightsEntity(Entity):
    """Base for entities rendered from the insights store of one station."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, store: HydroNodeInsightsStore, station: dict[str, Any], unique_id: str
    ) -> None:
        self._store = store
        self._station_id: str = station["id"]
        self._attr_unique_id = unique_id
        self._attr_device_info = station_device_info(station)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._store.async_add_listener(self._station_id, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self._clear_restored()
        self.async_write_ha_state()

    @callback
    def _clear_restored(self) -> None:
        """Drop a value restored across a restart. Overridden where restored."""
