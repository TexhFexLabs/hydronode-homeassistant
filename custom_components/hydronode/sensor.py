"""Sensor platform for HydroNode — one entity per (sensor, type, channel)."""

from __future__ import annotations

import logging
import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
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
from .insights import (
    KEY_AIR_QUALITY_INDEX,
    KEY_ANALYSIS,
    KEY_ANALYSIS_TIME,
    KEY_INCIDENT_SEVERITY,
    KEY_INCIDENTS_OPEN,
    SENSOR_ENTITY_KEYS,
    SEVERITY_NONE,
    SEVERITY_OPTIONS,
    HydroNodeInsightsEntity,
    HydroNodeInsightsStore,
    incident_summary,
    sensor_unique_id,
    station_unique_id,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydroNodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HydroNode sensor entities and wire up dynamic discovery."""
    coordinator = entry.runtime_data.coordinator
    insights = entry.runtime_data.insights
    entity_registry = er.async_get(hass)
    known_keys: set[StateKey] = set()
    known_insight_stations: set[str] = set()
    known_analysis_ids: set[str] = set()

    def _included_stations() -> list[dict[str, Any]]:
        return coordinator.filter_stations(
            coordinator.bootstrap,
            entry.options.get(CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED),
        )

    @callback
    def _add_analysis_entities() -> None:
        """Create per-sensor analysis entities once a sensor has an analysis.

        Entities already in the registry are recreated at startup even before
        their analysis is known again, so they restore their last value.
        """
        new_entities: list[SensorEntity] = []
        valid_ids: set[str] = set()
        for station in _included_stations():
            for sensor in station.get("sensors", []):
                analysis = insights.analysis(station["id"], sensor["id"])
                for key in SENSOR_ENTITY_KEYS:
                    unique_id = sensor_unique_id(station["id"], sensor["id"], key)
                    valid_ids.add(unique_id)
                    if unique_id in known_analysis_ids:
                        continue
                    wanted = (
                        analysis is not None
                        and (key != KEY_AIR_QUALITY_INDEX or analysis["air_quality"])
                    ) or entity_registry.async_get_entity_id(
                        "sensor", DOMAIN, unique_id
                    )
                    if not wanted:
                        continue
                    known_analysis_ids.add(unique_id)
                    new_entities.append(
                        HydroNodeAnalysisSensor(insights, station, sensor, key)
                    )
        known_analysis_ids.intersection_update(valid_ids)
        if new_entities:
            async_add_entities(new_entities)

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
            if station["id"] not in known_insight_stations:
                known_insight_stations.add(station["id"])
                new_entities.extend(
                    (
                        HydroNodeOpenIncidentsSensor(insights, station),
                        HydroNodeIncidentSeveritySensor(insights, station),
                    )
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
        known_insight_stations.intersection_update(current_stations)
        if new_entities:
            async_add_entities(new_entities)
        _add_analysis_entities()

    _add_from_bootstrap(coordinator.bootstrap)
    entry.async_on_unload(coordinator.add_new_entity_listener(_add_from_bootstrap))
    entry.async_on_unload(insights.async_add_analysis_listener(_add_analysis_entities))


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


class HydroNodeOpenIncidentsSensor(HydroNodeInsightsEntity, SensorEntity):
    """Number of open incidents of a station, with a compact list as attribute."""

    _attr_translation_key = KEY_INCIDENTS_OPEN
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:alert-circle-outline"
    # The list can be long; history only needs the count.
    _unrecorded_attributes = frozenset({"incidents"})

    def __init__(self, store: HydroNodeInsightsStore, station: dict[str, Any]) -> None:
        super().__init__(
            store, station, station_unique_id(station["id"], KEY_INCIDENTS_OPEN)
        )

    @property
    def native_value(self) -> int:
        return len(self._store.open_incidents(self._station_id))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "incidents": [
                incident_summary(incident)
                for incident in self._store.open_incidents(self._station_id)
            ]
        }


class HydroNodeIncidentSeveritySensor(HydroNodeInsightsEntity, SensorEntity):
    """Highest severity among the open incidents of a station."""

    _attr_translation_key = KEY_INCIDENT_SEVERITY
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SEVERITY_OPTIONS

    def __init__(self, store: HydroNodeInsightsStore, station: dict[str, Any]) -> None:
        super().__init__(
            store, station, station_unique_id(station["id"], KEY_INCIDENT_SEVERITY)
        )

    @property
    def native_value(self) -> str:
        return self._store.highest_severity(self._station_id)


# Attributes of the analysis sensor; restored across restarts with the state.
ANALYSIS_ATTRIBUTES = (
    "summary",
    "findings",
    "recommendations",
    "generated_at",
    "source",
    "should_alert",
    "environment_score",
)


class HydroNodeAnalysisSensor(HydroNodeInsightsEntity, RestoreSensor):
    """One view of the newest analysis of a sensor, on its station device.

    `analysis` shows the severity (texts only as attributes: HA states are capped
    at 255 characters), `air_quality_index` the 0-100 index and `analysis_time`
    when the analysis was generated.
    """

    _unrecorded_attributes = frozenset({"summary", "findings", "recommendations"})

    def __init__(
        self,
        store: HydroNodeInsightsStore,
        station: dict[str, Any],
        sensor: dict[str, Any],
        key: str,
    ) -> None:
        super().__init__(
            store, station, sensor_unique_id(station["id"], sensor["id"], key)
        )
        self._sensor_id: str = sensor["id"]
        self._key = key
        self._restored_value: Any = None
        self._restored_attributes: dict[str, Any] = {}
        # A pseudo-station is the sensor itself: no need to repeat its name.
        if station["id"] == sensor["id"]:
            self._attr_translation_key = key
        else:
            self._attr_translation_key = f"sensor_{key}"
            self._attr_translation_placeholders = {
                "sensor_name": sensor.get("name") or "Sensor"
            }
        if key == KEY_ANALYSIS:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = SEVERITY_OPTIONS
            self._attr_icon = "mdi:text-box-search-outline"
        elif key == KEY_AIR_QUALITY_INDEX:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 0
            self._attr_icon = "mdi:air-filter"
        elif key == KEY_ANALYSIS_TIME:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def _analysis(self) -> dict[str, Any] | None:
        return self._store.analysis(self._station_id, self._sensor_id)

    async def async_added_to_hass(self) -> None:
        """Restore the last value until the store knows the analysis again."""
        await super().async_added_to_hass()
        if self._analysis is not None:
            return
        last_data = await self.async_get_last_sensor_data()
        if last_data is not None:
            value = last_data.native_value
            if self._key == KEY_ANALYSIS:
                value = str(value).lower() if value is not None else None
                if value not in SEVERITY_OPTIONS:
                    value = None
            elif self._key == KEY_ANALYSIS_TIME and not isinstance(value, datetime):
                value = None
            self._restored_value = value
        if self._key in (KEY_ANALYSIS, KEY_AIR_QUALITY_INDEX):
            last_state = await self.async_get_last_state()
            if last_state is not None:
                self._restored_attributes = {
                    name: last_state.attributes[name]
                    for name in (*ANALYSIS_ATTRIBUTES, "band", "limiting_pollutant")
                    if name in last_state.attributes
                }

    @callback
    def _clear_restored(self) -> None:
        # Station updates also come from other sensors: keep the restored value
        # until this sensor's own analysis is known.
        if self._analysis is not None:
            self._restored_value = None
            self._restored_attributes = {}

    @property
    def native_value(self) -> Any:
        analysis = self._analysis
        if analysis is None:
            return self._restored_value
        if self._key == KEY_ANALYSIS:
            return analysis["severity"] or SEVERITY_NONE
        if self._key == KEY_AIR_QUALITY_INDEX:
            air_quality = analysis["air_quality"]
            return air_quality["value"] if air_quality else None
        return analysis["generated_at"]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        analysis = self._analysis
        if analysis is None:
            return self._restored_attributes or None
        if self._key == KEY_ANALYSIS:
            return {
                "summary": analysis["summary"],
                "findings": analysis["findings"],
                "recommendations": analysis["recommendations"],
                "generated_at": analysis["generated_at"].isoformat(),
                "source": analysis["source"],
                "should_alert": analysis["should_alert"],
                "environment_score": analysis["environment_score"],
            }
        if self._key == KEY_AIR_QUALITY_INDEX and analysis["air_quality"]:
            return {
                "band": analysis["air_quality"]["band"],
                "limiting_pollutant": analysis["air_quality"]["limiting_pollutant"],
            }
        return None
