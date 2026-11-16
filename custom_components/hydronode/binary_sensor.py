"""Binary sensor platform: incident problem sensor per station, on/off data points."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydroNodeConfigEntry
from .const import CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED
from .data_points import HydroNodePointEntity, read_unique_id, setup_point_platform
from .insights import (
    KEY_INCIDENT,
    HydroNodeInsightsEntity,
    HydroNodeInsightsStore,
    station_unique_id,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydroNodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the incident binary sensors and wire up dynamic discovery."""
    coordinator = entry.runtime_data.coordinator
    insights = entry.runtime_data.insights
    known_stations: set[str] = set()

    @callback
    def _add_from_bootstrap(bootstrap: dict[str, Any]) -> None:
        include_followed = entry.options.get(
            CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED
        )
        new_entities: list[HydroNodeIncidentBinarySensor] = []
        current_stations: set[str] = set()
        for station in coordinator.filter_stations(bootstrap, include_followed):
            station_id = station["id"]
            current_stations.add(station_id)
            if station_id in known_stations:
                continue
            known_stations.add(station_id)
            new_entities.append(HydroNodeIncidentBinarySensor(insights, station))
        # Stations gone from the bootstrap may come back; forget them so the
        # entities are recreated. _async_sync_registries removes the stale ones.
        known_stations.intersection_update(current_stations)
        if new_entities:
            async_add_entities(new_entities)

    _add_from_bootstrap(coordinator.bootstrap)
    entry.async_on_unload(coordinator.add_new_entity_listener(_add_from_bootstrap))
    setup_point_platform(
        entry, "binary_sensor", async_add_entities, HydroNodePointBinarySensor
    )


class HydroNodeIncidentBinarySensor(HydroNodeInsightsEntity, BinarySensorEntity):
    """On while the station has at least one open incident."""

    _attr_translation_key = KEY_INCIDENT
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, store: HydroNodeInsightsStore, station: dict[str, Any]) -> None:
        super().__init__(store, station, station_unique_id(station["id"], KEY_INCIDENT))

    @property
    def is_on(self) -> bool:
        return bool(self._store.open_incidents(self._station_id))


class HydroNodePointBinarySensor(HydroNodePointEntity, BinarySensorEntity):
    """A data point read as on/off. Its labels are an attribute (Open, Wet ...)."""

    def __init__(self, coordinator, client, station, sensor, point) -> None:
        super().__init__(
            coordinator, client, station, sensor, point, read_unique_id(point["id"])
        )

    @property
    def is_on(self) -> bool | None:
        value = self._value()
        if value not in (0.0, 1.0):
            return None
        return value == 1.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        label = self._label()
        if label is not None:
            attributes["label"] = label
        return attributes
