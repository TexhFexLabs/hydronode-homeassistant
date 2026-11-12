"""Validate remote data before it reaches entities or registry reconciliation."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.util import dt as dt_util


def nonempty_string(value: Any) -> bool:
    """Identifiers must be nonempty strings, never containers or numbers."""
    return isinstance(value, str) and bool(value.strip())


def valid_channel(value: Any) -> bool:
    return value is None or isinstance(value, str)


def valid_state(data: Any) -> bool:
    """Only finite measurements with timezone-aware timestamps are usable."""
    if not isinstance(data, dict):
        return False
    value = data.get("value")
    timestamp = data.get("timestamp")
    if not (
        nonempty_string(data.get("sensorId"))
        and nonempty_string(data.get("type"))
        and valid_channel(data.get("channelName"))
        and type(value) in (int, float)
        and isinstance(timestamp, str)
    ):
        return False
    try:
        parsed = dt_util.parse_datetime(timestamp)
        return math.isfinite(value) and parsed is not None and parsed.tzinfo is not None
    except (ValueError, OverflowError):
        return False


VALUE_TYPES = ("NUMBER", "BOOL", "ENUM", "TEXT")


def valid_point_state(data: Any) -> bool:
    """A data point row of /states: an old-style row plus id, optional text/label."""
    return (
        valid_state(data)
        and nonempty_string(data.get("dataPointId"))
        and (data.get("text") is None or isinstance(data["text"], str))
        and (data.get("label") is None or isinstance(data["label"], str))
        and (data.get("desired") is None or isinstance(data["desired"], dict))
    )


DESIRED_STATES = ("PENDING", "CONFIRMED", "FAILED")


def valid_desired(data: Any) -> bool:
    """A `desired.updated` push: ids, a known state and a timezone-aware time."""
    if not isinstance(data, dict):
        return False
    desired = data.get("desired")
    if not (
        nonempty_string(data.get("sensorId"))
        and nonempty_string(data.get("dataPointId"))
        and isinstance(desired, dict)
        and desired.get("state") in DESIRED_STATES
        and isinstance(desired.get("at"), str)
    ):
        return False
    value = desired.get("value")
    if value is not None and not (type(value) in (int, float) and math.isfinite(value)):
        return False
    for key in ("label", "text", "declineReason"):
        if desired.get(key) is not None and not isinstance(desired[key], str):
            return False
    try:
        parsed = dt_util.parse_datetime(desired["at"])
    except (ValueError, OverflowError):
        return False
    return parsed is not None and parsed.tzinfo is not None


def normalize_point(raw: Any) -> dict[str, Any] | None:
    """A bootstrap data point with checked fields, None when unusable."""
    if not isinstance(raw, dict) or not nonempty_string(raw.get("id")):
        return None
    value_type = raw.get("valueType")
    if value_type not in VALUE_TYPES:
        return None
    options: list[tuple[int, str]] = []
    raw_options = raw.get("enumOptions")
    if isinstance(raw_options, list):
        for option in raw_options:
            if (
                isinstance(option, dict)
                and type(option.get("value")) is int
                and nonempty_string(option.get("label"))
            ):
                options.append((option["value"], option["label"]))
    if value_type == "BOOL" and sorted(value for value, _ in options) != [0, 1]:
        options = [(0, "Off"), (1, "On")]
    if value_type == "ENUM" and not options:
        return None
    write = raw.get("write") if isinstance(raw.get("write"), dict) else {}
    decimals = raw.get("decimals")

    def _number(key: str) -> float | None:
        value = write.get(key)
        return (
            float(value)
            if type(value) in (int, float) and math.isfinite(value)
            else None
        )

    name = raw.get("name")
    return {
        "id": raw["id"],
        "name": name.strip() if nonempty_string(name) else None,
        "type": raw.get("type") if nonempty_string(raw.get("type")) else None,
        "channel": raw.get("channelName")
        if isinstance(raw.get("channelName"), str)
        else None,
        "value_type": value_type,
        "options": options,
        "unit": raw.get("unit") if nonempty_string(raw.get("unit")) else None,
        "decimals": decimals if type(decimals) is int and 0 <= decimals <= 6 else None,
        "writable": raw.get("writable") is True,
        "can_write": raw.get("canWrite") is True,
        "write_kind": write.get("kind") if isinstance(write.get("kind"), str) else None,
        "min": _number("min"),
        "max": _number("max"),
        "step": _number("step"),
        # A virtual sensor's reference: the source data point it reads through.
        "reference_of": raw.get("referenceOf")
        if nonempty_string(raw.get("referenceOf"))
        else None,
    }


def valid_bootstrap(data: Any) -> bool:
    """Reject incomplete discovery snapshots instead of deleting existing devices."""
    if not isinstance(data, dict) or not isinstance(data.get("user"), dict):
        return False
    if not nonempty_string(data["user"].get("id")):
        return False
    stations = data.get("stations")
    if not isinstance(stations, list):
        return False
    station_ids: set[str] = set()
    sensor_ids: set[str] = set()
    for station in stations:
        if not isinstance(station, dict) or not nonempty_string(station.get("id")):
            return False
        if station["id"] in station_ids:
            return False
        station_ids.add(station["id"])
        if not isinstance(station.get("sensors"), list):
            return False
        for sensor in station["sensors"]:
            if not isinstance(sensor, dict) or not nonempty_string(sensor.get("id")):
                return False
            if sensor["id"] in sensor_ids:
                return False
            sensor_ids.add(sensor["id"])
            if not isinstance(sensor.get("types"), list):
                return False
            if "active" in sensor and not isinstance(sensor["active"], bool):
                return False
            if "kind" in sensor and not nonempty_string(sensor["kind"]):
                return False
            if "dataPoints" in sensor and not isinstance(sensor["dataPoints"], list):
                return False
            for type_info in sensor["types"]:
                if not (
                    isinstance(type_info, dict)
                    and nonempty_string(type_info.get("type"))
                    and valid_channel(type_info.get("channelName"))
                ):
                    return False
    return True
