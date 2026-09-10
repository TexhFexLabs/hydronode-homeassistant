"""Binary sensor platform for HydroNode — boolean fields of the latest analysis."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import HydroNodeConfigEntry
from .analysis import (
    KEY_ALERT,
    KEY_VENTILATION_EVENT,
    HydroNodeAnalysisEntity,
    HydroNodeAnalysisStore,
    analysis_flag,
)
from .const import CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HydroNodeAnalysisBinaryDescription(BinarySensorEntityDescription):
    """Describes one boolean analysis field rendered as a binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool | None]


ANALYSIS_BINARY_SENSORS: tuple[HydroNodeAnalysisBinaryDescription, ...] = (
    HydroNodeAnalysisBinaryDescription(
        key=KEY_VENTILATION_EVENT,
        translation_key=KEY_VENTILATION_EVENT,
        value_fn=analysis_flag("is_ventilation_event"),
    ),
    HydroNodeAnalysisBinaryDescription(
        key=KEY_ALERT,
        translation_key=KEY_ALERT,
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=analysis_flag("should_alert"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydroNodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the analysis binary sensors and wire up dynamic discovery."""
    coordinator = entry.runtime_data.coordinator
    analysis_store = entry.runtime_data.analysis_store
    known_stations: set[str] = set()

    @callback
    def _add_from_bootstrap(bootstrap: dict[str, Any]) -> None:
        include_followed = entry.options.get(
            CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED
        )
        new_entities: list[HydroNodeAnalysisBinarySensor] = []
        current_stations: set[str] = set()
        for station in coordinator.filter_stations(bootstrap, include_followed):
            station_id = station["id"]
            current_stations.add(station_id)
            if station_id in known_stations:
                continue
            known_stations.add(station_id)
            new_entities.extend(
                HydroNodeAnalysisBinarySensor(analysis_store, station, description)
                for description in ANALYSIS_BINARY_SENSORS
            )
        # Stations gone from the bootstrap may come back; forget them so the
        # entities are recreated. _async_sync_registries removes the stale ones.
        known_stations.intersection_update(current_stations)
        if new_entities:
            async_add_entities(new_entities)

    _add_from_bootstrap(coordinator.bootstrap)
    entry.async_on_unload(coordinator.add_new_entity_listener(_add_from_bootstrap))


class HydroNodeAnalysisBinarySensor(
    HydroNodeAnalysisEntity, BinarySensorEntity, RestoreEntity
):
    """One boolean field of the latest `ai_analysis` payload."""

    entity_description: HydroNodeAnalysisBinaryDescription

    def __init__(
        self,
        store: HydroNodeAnalysisStore,
        station: dict[str, Any],
        description: HydroNodeAnalysisBinaryDescription,
    ) -> None:
        super().__init__(store, station, description.key)
        self.entity_description = description
        self._restored_value: bool | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last value so a restart does not blank the entity."""
        await super().async_added_to_hass()
        if self.analysis is not None:
            return
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("on", "off"):
            self._restored_value = last_state.state == STATE_ON

    @callback
    def _clear_restored(self) -> None:
        self._restored_value = None

    @property
    def is_on(self) -> bool | None:
        """Value from the latest analysis, else the one restored across a restart."""
        data = self.analysis
        if data is None:
            return self._restored_value
        return self.entity_description.value_fn(data)
