"""Event platform for HydroNode — one event entity per station device."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydroNodeConfigEntry
from .const import CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED, DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)

EVENT_TYPES = ["anomaly", "ai_analysis"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydroNodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one HydroNode event entity per station and wire up dynamic discovery."""
    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator
    known_entities: dict[str, HydroNodeEventEntity] = {}

    @callback
    def _add_from_bootstrap(bootstrap: dict[str, Any]) -> None:
        include_followed = entry.options.get(
            CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED
        )
        new_entities: list[HydroNodeEventEntity] = []
        current_ids: set[str] = set()
        for station in coordinator.filter_stations(bootstrap, include_followed):
            station_id = station["id"]
            current_ids.add(station_id)
            if station_id in known_entities:
                continue
            entity = HydroNodeEventEntity(station)
            known_entities[station_id] = entity
            runtime_data.event_listeners.append(entity.handle_event)
            new_entities.append(entity)
        # Stations gone from the bootstrap (unfollow, revoked share): stop feeding
        # their entities; the registry entries are removed by _async_sync_registries.
        for station_id in set(known_entities) - current_ids:
            entity = known_entities.pop(station_id)
            if entity.handle_event in runtime_data.event_listeners:
                runtime_data.event_listeners.remove(entity.handle_event)
        if new_entities:
            async_add_entities(new_entities)

    _add_from_bootstrap(coordinator.bootstrap)
    coordinator.add_new_entity_listener(_add_from_bootstrap)


class HydroNodeEventEntity(EventEntity):
    """Fires `anomaly` / `ai_analysis` events for a single station device."""

    _attr_has_entity_name = True
    _attr_name = "Events"
    _attr_event_types = EVENT_TYPES
    _attr_should_poll = False

    def __init__(self, station: dict[str, Any]) -> None:
        self._station_id: str = station["id"]
        self._attr_unique_id = f"{self._station_id}_events"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station.get("name") or "HydroNode Station",
            manufacturer=MANUFACTURER,
        )

    @callback
    def handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Dispatch an incoming anomaly/ai_analysis payload if it targets this station.

        A sensor without a station is represented by a pseudo-station whose id
        equals the sensor id, so when `stationId` is null we fall back to
        `sensorId` to still match the right device.
        """
        if event_type not in EVENT_TYPES:
            return

        effective_station_id = data.get("stationId") or data.get("sensorId")
        if effective_station_id != self._station_id:
            return

        self._trigger_event(event_type, data)
        if self.hass is not None:
            self.async_write_ha_state()
