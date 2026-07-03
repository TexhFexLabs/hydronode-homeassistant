"""Sensor platform for HydroNode — one entity per (sensor, type, channel)."""

from __future__ import annotations

import logging
import math
from collections import Counter
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
    DEFAULT_SCALE,
    DOMAIN,
    GENERIC_SENSOR_TYPE,
    MANUFACTURER,
    SENSOR_TYPE_MAP,
    SENSOR_TYPE_NAMES,
    STALE_TIMEOUT_SECONDS,
)
from .coordinator import HydroNodeCoordinator, StateKey, build_unique_id

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
        current_keys: set[StateKey] = set()
        for station in coordinator.filter_stations(bootstrap, include_followed):
            for sensor in station.get("sensors", []):
                # Two channels of the same type on one sensor would collide on the
                # pretty type name, so only then the channel name disambiguates.
                type_counts = Counter(
                    type_info["type"] for type_info in sensor.get("types", [])
                )
                for type_info in sensor.get("types", []):
                    key: StateKey = (
                        sensor["id"],
                        type_info["type"],
                        type_info.get("channelName"),
                    )
                    current_keys.add(key)
                    if key in known_keys:
                        continue
                    known_keys.add(key)
                    new_entities.append(
                        HydroNodeSensorEntity(
                            coordinator,
                            station,
                            sensor,
                            type_info,
                            disambiguate=type_counts[type_info["type"]] > 1,
                        )
                    )
        # Forget keys that dropped out of the bootstrap (unfollow, revoked share,
        # renamed channel) so a later re-follow re-creates the entity. The stale
        # entity itself is removed from the registry by _async_sync_registries.
        known_keys.intersection_update(current_keys)
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
        disambiguate: bool = False,
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

        self._attr_unique_id = build_unique_id(self._sensor_id, self._type, self._channel)

        # Entity name mirrors the webapp: pretty display name per type
        # ("Particle Count >0.5µm" instead of "nc_0_5"). The channel name is only
        # appended when one sensor reports the same type on several channels.
        display_name = SENSOR_TYPE_NAMES.get(
            self._type, self._type.replace("_", " ").title()
        )
        if disambiguate and self._channel:
            display_name = f"{display_name} ({self._channel})"
        self._attr_name = display_name

        # fPort channel scale drives the displayed decimals (history keeps the raw
        # value); a channel without fPort config falls back to 0.01 → 2 decimals.
        scale = type_info.get("scale")
        if not scale or scale <= 0:
            scale = DEFAULT_SCALE
        self._attr_suggested_display_precision = (
            0 if scale >= 1 else min(6, math.ceil(-math.log10(scale) - 1e-9))
        )

        if self._channel:
            self._attr_extra_state_attributes = {"channel_name": self._channel}

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

        max_age = timedelta(seconds=STALE_TIMEOUT_SECONDS)
        return dt_util.utcnow() - dt_util.as_utc(value_time) <= max_age
