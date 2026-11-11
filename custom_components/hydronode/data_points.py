"""Data platform: entities per data point (HydroNode sends them with `data1`).

The bootstrap of a data platform server carries per sensor its `kind` and a
`dataPoints` list. Entities are made from them by one rule, shared by every
platform and by the registry cleanup:

* Device sensors keep their old entities (`{sensor}_{type}_{channel}`) exactly
  as before. Their data points add an entity only when they are more than a
  number: read as on/off (binary_sensor) or as a choice (enum sensor), or when
  they can be switched (actuators).
* Other kinds (MQTT, push, virtual, external) get one entity per data point and
  no old-style entities: no older version could have made them.
* A data point the token may write becomes a control instead of a read entity:
  switch (on/off), select (choice) or number. Read tokens (hrt_) never write.

New entities use the data point id: `dp_{id}` (read) and `dp_{id}_write` (control).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from typing import Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_INCLUDE_FOLLOWED,
    DEFAULT_INCLUDE_FOLLOWED,
    DOMAIN,
    KIND_MODELS,
    MANUFACTURER,
    STALE_TIMEOUT_SECONDS,
)
from .validation import normalize_point

READ = "read"
CONTROL = "control"

DEVICE_KIND = "DEVICE"

# (role, value type) -> HA platform
PLATFORMS = {
    (READ, "NUMBER"): "sensor",
    (READ, "ENUM"): "sensor",
    (READ, "TEXT"): "sensor",
    (READ, "BOOL"): "binary_sensor",
    (CONTROL, "BOOL"): "switch",
    (CONTROL, "ENUM"): "select",
    (CONTROL, "NUMBER"): "number",
}

# HA states are capped at 255 characters.
MAX_STATE_LENGTH = 255

WRITE_ERRORS = {
    "TOO_SOON": "Switched a moment ago. Try again in a few seconds",
    "RATE_LIMITED": "Too many changes. Try again in a minute",
    "EDIT_REQUIRED": "Your access to this sensor only allows reading",
    "LINK_READ_ONLY": "This data point is linked and read only here",
    "NOT_WRITABLE": "This data point is no longer writable",
    "FEATURE_OFF": "Writing data points is turned off on the server",
    "INVALID_VALUE": "The server did not accept this value",
    "COMMAND_QUEUE_FULL": "The device has too many commands waiting",
    "READ_TOKEN_SCOPE": "A read only token cannot switch anything",
}


def read_unique_id(point_id: str) -> str:
    return f"dp_{point_id}"


def control_unique_id(point_id: str) -> str:
    return f"dp_{point_id}_write"


def sensor_kind(sensor: dict[str, Any]) -> str:
    kind = sensor.get("kind")
    return kind if isinstance(kind, str) else DEVICE_KIND


def has_points(sensor: dict[str, Any]) -> bool:
    return isinstance(sensor.get("dataPoints"), list)


def legacy_types(sensor: dict[str, Any]) -> list[dict[str, Any]]:
    """Old-style (sensor, type, channel) entries that still make entities."""
    if has_points(sensor) and sensor_kind(sensor) != DEVICE_KIND:
        return []
    return sensor.get("types", [])


def sensor_points(sensor: dict[str, Any]) -> list[dict[str, Any]]:
    if not has_points(sensor):
        return []
    points = [normalize_point(raw) for raw in sensor["dataPoints"]]
    return [point for point in points if point is not None]


def role_of(
    sensor: dict[str, Any], point: dict[str, Any], can_write: bool
) -> str | None:
    """READ, CONTROL or None (a device number keeps only its old entity)."""
    if (
        can_write
        and point["writable"]
        and point["can_write"]
        and (CONTROL, point["value_type"]) in PLATFORMS
    ):
        return CONTROL
    if sensor_kind(sensor) == DEVICE_KIND and point["value_type"] == "NUMBER":
        return None
    return READ


def iter_points(
    stations: list[dict[str, Any]], can_write: bool
) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str]]:
    """(station, sensor, point, platform, unique_id) for every data point entity."""
    for station in stations:
        for sensor in station.get("sensors", []):
            for point in sensor_points(sensor):
                role = role_of(sensor, point, can_write)
                if role is None:
                    continue
                platform = PLATFORMS[(role, point["value_type"])]
                unique_id = (
                    control_unique_id(point["id"])
                    if role == CONTROL
                    else read_unique_id(point["id"])
                )
                yield station, sensor, point, platform, unique_id


def point_index(bootstrap: dict[str, Any]) -> dict[tuple, dict[str, Any]]:
    """(sensor, type, channel) of device data points, to route WS pushes."""
    index: dict[tuple, dict[str, Any]] = {}
    for station in bootstrap.get("stations", []):
        for sensor in station.get("sensors", []):
            if sensor_kind(sensor) != DEVICE_KIND:
                continue
            for point in sensor_points(sensor):
                if point["type"] is not None:
                    index[(sensor["id"], point["type"], point["channel"])] = point
    return index


def station_model(station: dict[str, Any]) -> str | None:
    """Device model for stations made only of non-device sensors."""
    kinds = {sensor_kind(sensor) for sensor in station.get("sensors", [])}
    if len(kinds) == 1:
        return KIND_MODELS.get(kinds.pop())
    return None


def setup_point_platform(
    entry: Any,
    platform: str,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[..., Any],
) -> None:
    """Create the data point entities of one platform and follow the bootstrap."""
    coordinator = entry.runtime_data.coordinator
    client = entry.runtime_data.client
    known: set[str] = set()

    @callback
    def _add(bootstrap: dict[str, Any]) -> None:
        stations = coordinator.filter_stations(
            bootstrap,
            entry.options.get(CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED),
        )
        current: set[str] = set()
        new_entities = []
        for station, sensor, point, point_platform, unique_id in iter_points(
            stations, client.can_write
        ):
            if point_platform != platform:
                continue
            current.add(unique_id)
            if unique_id in known:
                continue
            known.add(unique_id)
            new_entities.append(factory(coordinator, client, station, sensor, point))
        # Gone points may come back (share restored): forget them so they are
        # recreated. The registry cleanup removes the stale entities.
        known.intersection_update(current)
        if new_entities:
            async_add_entities(new_entities)

    _add(coordinator.bootstrap)
    entry.async_on_unload(coordinator.add_new_entity_listener(_add))


class HydroNodePointEntity(CoordinatorEntity):
    """Base of every data point entity: name, device, state row, writing."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Any,
        client: Any,
        station: dict[str, Any],
        sensor: dict[str, Any],
        point: dict[str, Any],
        unique_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._point = point
        self._point_id: str = point["id"]
        self._sensor_id: str = sensor["id"]
        self._sensor_active: bool = sensor.get("active", True)
        self._last_written_state: Any = None
        self._attr_unique_id = unique_id
        name = point["name"] or (point["type"] or "Value").replace("_", " ").title()
        # On a station with several sensors the sensor name tells them apart.
        if station["id"] != sensor["id"] and sensor.get("name"):
            name = f"{sensor['name']} {name}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station["id"])},
            name=station.get("name") or "HydroNode Station",
            manufacturer=MANUFACTURER,
        )
        attributes: dict[str, Any] = {"data_point_id": self._point_id}
        if point["channel"]:
            attributes["channel_name"] = point["channel"]
        self._static_attributes = attributes

    @property
    def _row(self) -> dict[str, Any] | None:
        return self.coordinator.point_states.get(self._point_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Avoid rewriting every entity for a push affecting another one."""
        state = (self._row, self.available)
        if state != self._last_written_state:
            self._last_written_state = state
            super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if not self.coordinator.sensor_active.get(self._sensor_id, self._sensor_active):
            return False
        row = self._row
        if row is None:
            # A switch without any value yet can still be switched.
            return self._attr_unique_id == control_unique_id(self._point_id)
        if self._point["value_type"] != "NUMBER":
            # On/off, choices and texts report on change only: never stale.
            return True
        parsed = dt_util.parse_datetime(row.get("timestamp") or "")
        if parsed is None:
            return False
        age = dt_util.utcnow() - dt_util.as_utc(parsed)
        return age.total_seconds() <= STALE_TIMEOUT_SECONDS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = dict(self._static_attributes)
        desired = (self._row or {}).get("desired")
        if isinstance(desired, dict) and isinstance(desired.get("state"), str):
            attributes["write_state"] = desired["state"].lower()
        return attributes

    def _value(self) -> float | None:
        """The value to show: a pending switch shows the wanted value."""
        row = self._row
        if row is None:
            return None
        desired = row.get("desired")
        if (
            isinstance(desired, dict)
            and desired.get("state") == "PENDING"
            and type(desired.get("value")) in (int, float)
        ):
            return float(desired["value"])
        value = row.get("value")
        return float(value) if type(value) in (int, float) else None

    def _label(self) -> str | None:
        value = self._value()
        if value is None or not math.isfinite(value) or value != int(value):
            return None
        return dict(self._point["options"]).get(int(value))

    async def _async_write(self, value: float) -> None:
        from .api import HydroNodeApiError, HydroNodeWriteError

        try:
            answer = await self._client.write_value(self._point_id, value)
        except HydroNodeWriteError as err:
            raise HomeAssistantError(
                WRITE_ERRORS.get(err.code or "", "HydroNode refused the change")
            ) from err
        except HydroNodeApiError as err:
            raise HomeAssistantError("HydroNode is not reachable") from err
        row = _row_from_point(self._sensor_id, self._point, answer, value)
        self.coordinator.apply_point_state(self._point_id, row)


def _row_from_point(
    sensor_id: str, point: dict[str, Any], answer: dict[str, Any], value: float
) -> dict[str, Any]:
    """A /states row from the data point object a write answers with."""
    latest = answer.get("latest") if isinstance(answer.get("latest"), dict) else {}
    desired = answer.get("desired") if isinstance(answer.get("desired"), dict) else None
    latest_value = latest.get("value")
    return {
        "sensorId": sensor_id,
        "type": point["type"],
        "channelName": point["channel"],
        "dataPointId": point["id"],
        "value": latest_value if type(latest_value) in (int, float) else value,
        "label": latest.get("label"),
        "timestamp": latest.get("at") or dt_util.utcnow().isoformat(),
        "desired": desired,
    }
