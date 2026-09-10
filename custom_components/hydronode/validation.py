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
            for type_info in sensor["types"]:
                if not (
                    isinstance(type_info, dict)
                    and nonempty_string(type_info.get("type"))
                    and valid_channel(type_info.get("channelName"))
                ):
                    return False
    return True
