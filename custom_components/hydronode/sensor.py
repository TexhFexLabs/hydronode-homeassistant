"""Sensor platform for HydroNode — one entity per (sensor, type, channel)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import HydroNodeConfigEntry
from .const import (
    CONF_INCLUDE_FOLLOWED,
    DEFAULT_INCLUDE_FOLLOWED,
    DOMAIN,
    GENERIC_SENSOR_TYPE,
    MANUFACTURER,
    SENSOR_TYPE_MAP,
    STALE_AFTER_POLL_MULTIPLIER,
    STALE_BUFFER_SECONDS,
)
from .coordinator import HydroNodeCoordinator, StateKey

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydroNodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HydroNode sensor entities and wire up dynamic discovery."""
    coordinator = entry.runtime_data.coordinator
    known_keys: set[StateKey] = set()

    @callback
    def _add_from_bootstrap(bootstrap: dict[str, Any]) -> None:
        include_followed = entry.options.get(
            CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED
        )
        new_entities: list[HydroNodeSensorEntity] = []
        for station in coordinator.filter_stations(bootstrap, include_followed):
            for sensor in station.get("sensors", []):
                for type_info in sensor.get("types", []):
                    key: StateKey = (
                        sensor["id"],
                        type_info["type"],
                        type_info.get("channelName"),
                    )
                    if key in known_keys:
                        continue
                    known_keys.add(key)
                    new_entities.append(
                        HydroNodeSensorEntity(coordinator, station, sensor, type_info)
                    )
        if new_entities:
            async_add_entities(new_entities)

    _add_from_bootstrap(coordinator.bootstrap)
    coordinator.add_new_entity_listener(_add_from_bootstrap)


class HydroNodeSensorEntity(CoordinatorEntity[HydroNodeCoordinator], SensorEntity):
    """A single (sensor, type, channel) measurement as a HA sensor entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HydroNodeCoordinator,
        station: dict[str, Any],
        sensor: dict[str, Any],
        type_info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._sensor_id: str = sensor["id"]
        self._type: str = type_info["type"]
        self._channel: str | None = type_info.get("channelName")
        self._sensor_active: bool = sensor.get("active", True)
        self._key: StateKey = (self._sensor_id, self._type, self._channel)

        device_class, unit, state_class = SENSOR_TYPE_MAP.get(
            self._type, GENERIC_SENSOR_TYPE
        )
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class

        self._attr_unique_id = f"{self._sensor_id}_{self._type}_{self._channel or ''}"

        # Entity name is just the measurement title (channel name if configured,
        # otherwise the prettified type). The station device name provides context,
        # so "HydroNodeStation01 Battery_Voltage" instead of
        # "HydroNodeStation01 BATTERY_VOLTAGE Battery_Voltage".
        self._attr_name = self._channel or self._type.replace("_", " ").title()

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station["id"])},
            name=station.get("name") or "HydroNode Station",
            manufacturer=MANUFACTURER,
        )

    @property
    def _current_state(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._key)

    @property
    def native_value(self) -> Any:
        """Return the latest numeric value for this (sensor, type, channel)."""
        state = self._current_state
        if state is None:
            return None
        return state.get("value")

    @property
    def available(self) -> bool:
        """Unavailable if the sensor is inactive or the last value is too stale."""
        if not super().available:
            return False
        if not self._sensor_active:
            return False

        state = self._current_state
        if state is None:
            return False

        timestamp = state.get("timestamp")
        if not timestamp:
            return False

        value_time = dt_util.parse_datetime(timestamp)
        if value_time is None:
            return False

        poll_interval = self.coordinator.update_interval or timedelta(seconds=60)
        max_age = timedelta(
            seconds=poll_interval.total_seconds() * STALE_AFTER_POLL_MULTIPLIER
            + STALE_BUFFER_SECONDS
        )
        return dt_util.utcnow() - dt_util.as_utc(value_time) <= max_age
