"""The HydroNode integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HydroNodeApiClient, HydroNodeApiError, HydroNodeAuthError
from .const import (
    CONF_BASE_URL,
    CONF_FIRE_VALUE_EVENTS,
    CONF_INCLUDE_FOLLOWED,
    CONF_POLL_INTERVAL,
    DEFAULT_INCLUDE_FOLLOWED,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EVENT_AI_ANALYSIS,
    EVENT_ANOMALY,
    EVENT_VALUE_UPDATED,
    MANUFACTURER,
)
from .coordinator import HydroNodeCoordinator, build_unique_id
from .ws import HydroNodeWebSocketClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.EVENT]

# Callback signature used by event.py entities: (event_type, data) -> None
EventListener = Any


@dataclass
class HydroNodeData:
    """Runtime data attached to the config entry."""

    client: HydroNodeApiClient
    coordinator: HydroNodeCoordinator
    ws_client: HydroNodeWebSocketClient | None = None
    event_listeners: list[EventListener] = field(default_factory=list)


type HydroNodeConfigEntry = ConfigEntry[HydroNodeData]


async def async_setup_entry(hass: HomeAssistant, entry: HydroNodeConfigEntry) -> bool:
    """Set up HydroNode from a config entry."""
    base_url: str = entry.data[CONF_BASE_URL]
    token: str = entry.data[CONF_TOKEN]
    poll_interval: int = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    session = async_get_clientsession(hass)
    client = HydroNodeApiClient(session, base_url, token)
    coordinator = HydroNodeCoordinator(hass, client, poll_interval)

    try:
        await coordinator.async_refresh_bootstrap()
    except HydroNodeAuthError as err:
        raise ConfigEntryAuthFailed("HydroNode token rejected") from err
    except HydroNodeApiError as err:
        raise ConfigEntryNotReady(f"Could not reach HydroNode: {err}") from err

    await coordinator.async_config_entry_first_refresh()

    _async_sync_registries(hass, entry, coordinator.bootstrap)
    coordinator.add_new_entity_listener(
        lambda bootstrap: _async_sync_registries(hass, entry, bootstrap)
    )

    runtime_data = HydroNodeData(client=client, coordinator=coordinator)

    def _handle_value_updated(data: dict[str, Any]) -> None:
        coordinator.apply_ws_value_update(data)
        if entry.options.get(CONF_FIRE_VALUE_EVENTS, False):
            hass.bus.async_fire(EVENT_VALUE_UPDATED, data)

    def _handle_anomaly_detected(data: dict[str, Any]) -> None:
        hass.bus.async_fire(EVENT_ANOMALY, data)
        for listener in list(runtime_data.event_listeners):
            listener("anomaly", data)

    def _handle_ai_analyzed(data: dict[str, Any]) -> None:
        hass.bus.async_fire(EVENT_AI_ANALYSIS, data)
        for listener in list(runtime_data.event_listeners):
            listener("ai_analysis", data)

    def _handle_auth_failed() -> None:
        hass.async_create_task(_trigger_reauth(hass, entry))

    async def _on_ws_reconnect() -> None:
        try:
            await coordinator.async_refresh_bootstrap()
        except HydroNodeApiError as err:
            _LOGGER.debug("Bootstrap refresh on WS reconnect failed: %s", err)

    ws_client = HydroNodeWebSocketClient(
        hass=hass,
        session=session,
        base_url=base_url,
        token=token,
        on_value_updated=_handle_value_updated,
        on_anomaly_detected=_handle_anomaly_detected,
        on_ai_analyzed=_handle_ai_analyzed,
        on_auth_failed=_handle_auth_failed,
        on_reconnect=_on_ws_reconnect,
    )
    runtime_data.ws_client = ws_client
    entry.runtime_data = runtime_data

    ws_client.start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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
    include_followed = entry.options.get(CONF_INCLUDE_FOLLOWED, DEFAULT_INCLUDE_FOLLOWED)

    valid_station_ids: set[str] = set()
    valid_unique_ids: set[str] = set()
    for station in HydroNodeCoordinator.filter_stations(bootstrap, include_followed):
        station_id = station["id"]
        valid_station_ids.add(station_id)
        valid_unique_ids.add(f"{station_id}_events")
        for sensor in station.get("sensors", []):
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


async def _trigger_reauth(hass: HomeAssistant, entry: HydroNodeConfigEntry) -> None:
    """Kick off a reauth flow, e.g. after a WS close code 4401."""
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )


async def _async_update_listener(hass: HomeAssistant, entry: HydroNodeConfigEntry) -> None:
    """Reload the entry whenever its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HydroNodeConfigEntry) -> bool:
    """Unload a config entry: stop the WS task, then unload platforms."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if entry.runtime_data is not None and entry.runtime_data.ws_client is not None:
        await entry.runtime_data.ws_client.stop()
    return unload_ok
