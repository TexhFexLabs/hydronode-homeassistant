"""The HydroNode integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import (
    HydroNodeApiClient,
    HydroNodeApiError,
    HydroNodeAuthError,
    async_create_hydronode_session,
)
from .const import (
    CONF_BASE_URL,
    CONF_FIRE_VALUE_EVENTS,
    CONF_INCLUDE_FOLLOWED,
    CONF_POLL_INTERVAL,
    DEFAULT_INCLUDE_FOLLOWED,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EVENT_ANALYSIS,
    EVENT_INCIDENT,
    EVENT_VALUE_UPDATED,
    MANUFACTURER,
)
from .coordinator import HydroNodeCoordinator, build_unique_id
from .insights import (
    EVENT_ANALYSIS_READY,
    MAX_INCIDENTS_PER_STATION,
    SENSOR_ENTITY_KEYS,
    STATION_ENTITY_KEYS,
    HydroNodeInsightsStore,
    analysis_event_attributes,
    incident_event_attributes,
    normalize_analysis,
    normalize_incident,
    sensor_unique_id,
    station_unique_id,
)
from .ws import HydroNodeWebSocketClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT]

# Callback used by event.py entities: (station_id, event_type, attributes) -> None
EventListener = Callable[[str, str, dict[str, Any]], None]


@dataclass
class HydroNodeData:
    """Runtime data attached to the config entry."""

    client: HydroNodeApiClient
    coordinator: HydroNodeCoordinator
    insights: HydroNodeInsightsStore = field(default_factory=HydroNodeInsightsStore)
    ws_client: HydroNodeWebSocketClient | None = None
    event_listeners: list[EventListener] = field(default_factory=list)


type HydroNodeConfigEntry = ConfigEntry[HydroNodeData]


async def async_setup_entry(hass: HomeAssistant, entry: HydroNodeConfigEntry) -> bool:
    """Set up HydroNode from a config entry."""
    base_url: str = entry.data[CONF_BASE_URL]
    token: str = entry.data[CONF_TOKEN].strip()
    poll_interval: int = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    session = async_create_hydronode_session(hass)
    try:
        client = HydroNodeApiClient(session, base_url, token)
        coordinator = HydroNodeCoordinator(hass, client, poll_interval)
        coordinator.expected_user_id = entry.unique_id
        await coordinator.async_refresh_bootstrap()
    except HydroNodeAuthError as err:
        raise ConfigEntryAuthFailed("HydroNode token rejected") from err
    except HydroNodeApiError as err:
        raise ConfigEntryNotReady(f"Could not reach HydroNode: {err}") from err

    await coordinator.async_config_entry_first_refresh()

    _async_sync_registries(hass, entry, coordinator.bootstrap)
    entry.async_on_unload(
        coordinator.add_new_entity_listener(
            lambda bootstrap: _async_sync_registries(hass, entry, bootstrap)
        )
    )

    runtime_data = HydroNodeData(client=client, coordinator=coordinator)
    insights = runtime_data.insights

    # sensor id -> station id of every sensor this entry may show. Pushes and
    # snapshots are filed under the bootstrap station, never the payload's own.
    sensor_station: dict[str, str] = {}

    def _sync_allowed(bootstrap: dict[str, Any]) -> None:
        stations = coordinator.filter_stations(
            bootstrap,
            entry.options.get(CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED),
        )
        sensor_station.clear()
        sensor_station.update(
            (sensor["id"], station["id"])
            for station in stations
            for sensor in station["sensors"]
        )
        insights.retain(sensor_station)

    _sync_allowed(coordinator.bootstrap)
    entry.async_on_unload(coordinator.add_new_entity_listener(_sync_allowed))

    insights_lock = asyncio.Lock()

    async def _async_load_insights() -> None:
        """Replace incidents/analyses with the REST snapshot. Never fails setup."""
        async with insights_lock:
            started = insights.begin_snapshot()
            try:
                payload = await client.insights()
            except HydroNodeApiError as err:
                # Auth failures are handled by the coordinator; keep the last data.
                _LOGGER.debug("HydroNode insights refresh failed: %s", err)
                return
            if coordinator.auth_failed:
                return
            incidents, analyses = _split_insights(payload, sensor_station)
            insights.apply_snapshot(started, incidents, analyses)

    @callback
    def _schedule_insights(_bootstrap: dict[str, Any] | None = None) -> None:
        entry.async_create_background_task(
            hass, _async_load_insights(), "HydroNode insights refresh"
        )

    await _async_load_insights()
    # Every bootstrap refresh (5 min, WS reconnect) also reloads the insights.
    entry.async_on_unload(coordinator.add_new_entity_listener(_schedule_insights))

    def _accept_event(data: dict[str, Any]) -> str | None:
        """Station of an accepted push, None when it must be dropped."""
        sensor_id = data.get("sensorId")
        if coordinator.auth_failed or not isinstance(sensor_id, str):
            return None
        return sensor_station.get(sensor_id)

    def _fan_out(station_id: str, event_type: str, attributes: dict[str, Any]) -> None:
        for listener in list(runtime_data.event_listeners):
            listener(station_id, event_type, attributes)

    def _handle_value_updated(data: dict[str, Any]) -> None:
        if _accept_event(data) is None or not coordinator.apply_ws_value_update(data):
            return
        if entry.options.get(CONF_FIRE_VALUE_EVENTS, False):
            hass.bus.async_fire(EVENT_VALUE_UPDATED, data)

    def _handle_incident_updated(data: dict[str, Any]) -> None:
        station_id = _accept_event(data)
        if station_id is None:
            return
        result = insights.apply_incident(station_id, data)
        if result is None:
            return
        incident, transition, event_type = result
        hass.bus.async_fire(EVENT_INCIDENT, data)
        if event_type is not None:
            _fan_out(
                station_id, event_type, incident_event_attributes(incident, transition)
            )

    def _handle_analysis_completed(data: dict[str, Any]) -> None:
        station_id = _accept_event(data)
        if station_id is None:
            return
        analysis = insights.apply_analysis(station_id, data)
        if analysis is None:
            return
        hass.bus.async_fire(EVENT_ANALYSIS, data)
        _fan_out(station_id, EVENT_ANALYSIS_READY, analysis_event_attributes(analysis))

    def _handle_auth_failed() -> None:
        coordinator.auth_failed = True
        coordinator.async_set_update_error(
            ConfigEntryAuthFailed("HydroNode token rejected")
        )
        entry.async_start_reauth(hass)

    async def _on_ws_reconnect() -> None:
        try:
            await coordinator.async_refresh_bootstrap()
            await coordinator.async_request_refresh()
        except HydroNodeAuthError:
            raise
        except HydroNodeApiError as err:
            _LOGGER.debug("Bootstrap refresh on WS reconnect failed: %s", err)
            # A successful refresh reloads the insights via its listener.
            _schedule_insights()

    ws_client = HydroNodeWebSocketClient(
        hass=hass,
        session=session,
        base_url=base_url,
        token=token,
        on_value_updated=_handle_value_updated,
        on_incident_updated=_handle_incident_updated,
        on_analysis_completed=_handle_analysis_completed,
        on_auth_failed=_handle_auth_failed,
        on_reconnect=_on_ws_reconnect,
        expected_user_id=entry.unique_id,
    )
    runtime_data.ws_client = ws_client
    entry.runtime_data = runtime_data

    def _check_auth() -> None:
        if coordinator.auth_failed:
            hass.async_create_task(ws_client.stop())

    # Keep polling even if every measurement entity is disabled: discovery and
    # token revalidation must still run for event/analysis-only installations.
    entry.async_on_unload(coordinator.async_add_listener(_check_auth))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    ws_client.start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


def _async_sync_registries(
    hass: HomeAssistant, entry: HydroNodeConfigEntry, bootstrap: dict[str, Any]
) -> None:
    """Mirror the bootstrap into the HA registries.

    Creates/updates one device per station (including pseudo-stations) and removes
    devices and entities whose station/sensor/channel dropped out of the bootstrap
    (unfollowed public station, revoked share, renamed channel, 30-day inactivity).
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    include_followed = entry.options.get(
        CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED
    )

    valid_station_ids: set[str] = set()
    valid_unique_ids: set[str] = set()
    for station in HydroNodeCoordinator.filter_stations(bootstrap, include_followed):
        station_id = station["id"]
        valid_station_ids.add(station_id)
        # Old V1 analysis entities ({station}_analysis_{key}) are not listed
        # here on purpose, so this cleanup removes them.
        valid_unique_ids.update(
            station_unique_id(station_id, key) for key in STATION_ENTITY_KEYS
        )
        for sensor in station.get("sensors", []):
            valid_unique_ids.update(
                sensor_unique_id(station_id, sensor["id"], key)
                for key in SENSOR_ENTITY_KEYS
            )
            for type_info in sensor.get("types", []):
                valid_unique_ids.add(
                    build_unique_id(
                        sensor["id"], type_info["type"], type_info.get("channelName")
                    )
                )
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, station_id)},
            name=station.get("name") or "HydroNode Station",
            manufacturer=MANUFACTURER,
        )

    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if entity_entry.unique_id not in valid_unique_ids:
            _LOGGER.debug("Removing stale entity %s", entity_entry.entity_id)
            entity_registry.async_remove(entity_entry.entity_id)

    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if not any(
            domain == DOMAIN and identifier in valid_station_ids
            for domain, identifier in device.identifiers
        ):
            _LOGGER.debug("Removing stale device %s", device.name)
            device_registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


def _split_insights(
    payload: dict[str, Any] | None, sensor_station: dict[str, str]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Normalize an /insights payload and file each item under its bootstrap station.

    Items of sensors outside the bootstrap (filtered followed stations, revoked
    shares) and malformed items are dropped one by one.
    """
    incidents: dict[str, list[dict[str, Any]]] = {}
    analyses: dict[str, list[dict[str, Any]]] = {}
    for station in (payload or {}).get("stations", []):
        if not isinstance(station, dict):
            continue
        raw_incidents = station.get("incidents")
        if isinstance(raw_incidents, list):
            for raw in raw_incidents[:MAX_INCIDENTS_PER_STATION]:
                incident = normalize_incident(raw)
                if incident is None:
                    continue
                station_id = sensor_station.get(incident["sensor_id"])
                if station_id is not None:
                    incidents.setdefault(station_id, []).append(incident)
        raw_analyses = station.get("analyses")
        if isinstance(raw_analyses, list):
            for raw in raw_analyses:
                analysis = normalize_analysis(raw)
                if analysis is None:
                    continue
                station_id = sensor_station.get(analysis["sensor_id"])
                if station_id is not None:
                    analyses.setdefault(station_id, []).append(analysis)
    return incidents, analyses


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: HydroNodeConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow manual device deletion for stations no longer in the bootstrap."""
    bootstrap = entry.runtime_data.coordinator.bootstrap if entry.runtime_data else {}
    current_ids = {
        station["id"]
        for station in bootstrap.get("stations", [])
        if isinstance(station, dict)
    }
    return not any(
        domain == DOMAIN and identifier in current_ids
        for domain, identifier in device.identifiers
    )


async def _async_update_listener(
    hass: HomeAssistant, entry: HydroNodeConfigEntry
) -> None:
    """Reload the entry whenever its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HydroNodeConfigEntry) -> bool:
    """Unload a config entry: stop the WS task, then unload platforms."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if (
        unload_ok
        and entry.runtime_data is not None
        and entry.runtime_data.ws_client is not None
    ):
        await entry.runtime_data.ws_client.stop()
    return unload_ok
