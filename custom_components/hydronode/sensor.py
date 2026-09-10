"""Sensor platform for HydroNode — one entity per (sensor, type, channel)."""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import HydroNodeConfigEntry
from .analysis import (
    KEY_AIR_QUALITY_SCORE,
    KEY_ALERT_SEVERITY,
    KEY_ANALYSIS_TIMESTAMP,
    KEY_ANOMALY_TYPE,
    KEY_CONFIDENCE,
    KEY_ENVIRONMENT_SCORE,
    HydroNodeAnalysisEntity,
    HydroNodeAnalysisStore,
    analysis_timestamp,
    enum_value,
    score_percent,
)
from .const import (
    ALERT_SEVERITY_OPTIONS,
    ANOMALY_TYPE_OPTIONS,
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


@dataclass(frozen=True, kw_only=True)
class HydroNodeAnalysisSensorDescription(SensorEntityDescription):
    """Describes one analysis field rendered as a sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


ANALYSIS_SENSORS: tuple[HydroNodeAnalysisSensorDescription, ...] = (
    HydroNodeAnalysisSensorDescription(
        key=KEY_AIR_QUALITY_SCORE,
        translation_key=KEY_AIR_QUALITY_SCORE,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=score_percent("air_quality_score"),
    ),
    HydroNodeAnalysisSensorDescription(
        key=KEY_ENVIRONMENT_SCORE,
        translation_key=KEY_ENVIRONMENT_SCORE,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=score_percent("environment_score"),
    ),
    HydroNodeAnalysisSensorDescription(
        key=KEY_CONFIDENCE,
        translation_key=KEY_CONFIDENCE,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=score_percent("confidence"),
    ),
    HydroNodeAnalysisSensorDescription(
        key=KEY_ALERT_SEVERITY,
        translation_key=KEY_ALERT_SEVERITY,
        device_class=SensorDeviceClass.ENUM,
        options=ALERT_SEVERITY_OPTIONS,
        value_fn=enum_value(
            lambda data: (data.get("analysis") or {}).get("alert_severity"),
            ALERT_SEVERITY_OPTIONS,
            "alert severity",
        ),
    ),
    HydroNodeAnalysisSensorDescription(
        key=KEY_ANOMALY_TYPE,
        translation_key=KEY_ANOMALY_TYPE,
        device_class=SensorDeviceClass.ENUM,
        options=ANOMALY_TYPE_OPTIONS,
        value_fn=enum_value(
            lambda data: data.get("anomalyType"),
            ANOMALY_TYPE_OPTIONS,
            "anomaly type",
        ),
    ),
    HydroNodeAnalysisSensorDescription(
        key=KEY_ANALYSIS_TIMESTAMP,
        translation_key=KEY_ANALYSIS_TIMESTAMP,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=analysis_timestamp,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydroNodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HydroNode sensor entities and wire up dynamic discovery."""
    coordinator = entry.runtime_data.coordinator
    analysis_store = entry.runtime_data.analysis_store
    known_keys: set[StateKey] = set()
    known_analysis_stations: set[str] = set()

    @callback
    def _add_from_bootstrap(bootstrap: dict[str, Any]) -> None:
        include_followed = entry.options.get(
            CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED
        )
        new_entities: list[SensorEntity] = []
        current_keys: set[StateKey] = set()
        current_stations: set[str] = set()
        for station in coordinator.filter_stations(bootstrap, include_followed):
            current_stations.add(station["id"])
            if station["id"] not in known_analysis_stations:
                known_analysis_stations.add(station["id"])
                new_entities.extend(
                    HydroNodeAnalysisSensor(analysis_store, station, description)
                    for description in ANALYSIS_SENSORS
                )
            for sensor in station.get("sensors", []):
                # Two channels of the same type on one sensor would collide on the
                # pretty type name, so only then the channel name disambiguates.
                type_counts = Counter(
                    type_info["type"].upper() for type_info in sensor.get("types", [])
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
                            disambiguate=type_counts[type_info["type"].upper()] > 1,
                        )
                    )
        # Forget keys that dropped out of the bootstrap (unfollow, revoked share,
        # renamed channel) so a later re-follow re-creates the entity. The stale
        # entity itself is removed from the registry by _async_sync_registries.
        known_keys.intersection_update(current_keys)
        known_analysis_stations.intersection_update(current_stations)
        if new_entities:
            async_add_entities(new_entities)

    _add_from_bootstrap(coordinator.bootstrap)
    entry.async_on_unload(coordinator.add_new_entity_listener(_add_from_bootstrap))


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
        self._last_written_state: tuple[Any, bool] | None = None
        self._cached_timestamp: str | None = None
        self._cached_value_time = None

        # Types are matched case-insensitively, like the webapp: user-defined fPort
        # configs may register them as e.g. "pm2_5" instead of "PM2_5".
        type_key = self._type.upper()
        device_class, unit, state_class = SENSOR_TYPE_MAP.get(
            type_key, GENERIC_SENSOR_TYPE
        )
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class

        self._attr_unique_id = build_unique_id(
            self._sensor_id, self._type, self._channel
        )

        # Entity name mirrors the webapp: pretty display name per type
        # ("Particle Count >0.5µm" instead of "nc_0_5"). The channel name is only
        # appended when one sensor reports the same type on several channels.
        display_name = SENSOR_TYPE_NAMES.get(
            type_key, self._type.replace("_", " ").title()
        )
        if disambiguate and self._channel:
            display_name = f"{display_name} ({self._channel})"
        self._attr_name = display_name

        # fPort channel scale drives the displayed decimals (history keeps the raw
        # value); a channel without fPort config falls back to 0.01 → 2 decimals.
        raw_scale = type_info.get("scale")
        try:
            scale = (
                float(raw_scale) if type(raw_scale) in (int, float) else DEFAULT_SCALE
            )
        except (ValueError, OverflowError):
            scale = DEFAULT_SCALE
        if not math.isfinite(scale) or scale <= 0:
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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Avoid rewriting every sensor for a push affecting a single channel."""
        state = (self._current_state, self.available)
        if state != self._last_written_state:
            self._last_written_state = state
            super()._handle_coordinator_update()

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
        if not self.coordinator.sensor_active.get(self._sensor_id, self._sensor_active):
            return False

        state = self._current_state
        if state is None:
            return False

        timestamp = state.get("timestamp")
        if not timestamp:
            return False

        if timestamp != self._cached_timestamp:
            self._cached_timestamp = timestamp
            parsed = dt_util.parse_datetime(timestamp)
            self._cached_value_time = (
                dt_util.as_utc(parsed) if parsed is not None else None
            )
        value_time = self._cached_value_time
        if value_time is None:
            return False

        max_age = timedelta(seconds=STALE_TIMEOUT_SECONDS)
        return dt_util.utcnow() - dt_util.as_utc(value_time) <= max_age


class HydroNodeAnalysisSensor(HydroNodeAnalysisEntity, RestoreSensor):
    """One field of the latest `ai_analysis` payload as a sensor."""

    entity_description: HydroNodeAnalysisSensorDescription

    def __init__(
        self,
        store: HydroNodeAnalysisStore,
        station: dict[str, Any],
        description: HydroNodeAnalysisSensorDescription,
    ) -> None:
        super().__init__(store, station, description.key)
        self.entity_description = description
        self._restored_value: Any = None

    async def async_added_to_hass(self) -> None:
        """Restore the last value so a restart does not blank the entity.

        The analysis only arrives by WebSocket push and the backend exposes no
        endpoint to seed it, so without this the entity would stay unknown until
        the next analysis — which can be hours away.
        """
        await super().async_added_to_hass()
        if self.analysis is not None:
            return
        last_data = await self.async_get_last_sensor_data()
        if last_data is not None:
            value = last_data.native_value
            if self.entity_description.device_class == SensorDeviceClass.ENUM:
                value = str(value).lower() if value is not None else None
                if value not in (self.entity_description.options or []):
                    value = None
            self._restored_value = value

    @callback
    def _clear_restored(self) -> None:
        self._restored_value = None

    @property
    def native_value(self) -> Any:
        """Value from the latest analysis, else the one restored across a restart."""
        data = self.analysis
        if data is None:
            return self._restored_value
        return self.entity_description.value_fn(data)
