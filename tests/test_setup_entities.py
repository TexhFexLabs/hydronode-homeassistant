"""End-to-end test: async_setup_entry wires devices, sensor and event entities."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import CONF_TOKEN
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronode.const import CONF_BASE_URL, DOMAIN

BASE_URL = "https://hydronode.example.com"
TOKEN = "hat_test1234567890"

BOOTSTRAP = {
    "user": {"id": "u1", "displayName": "Felix"},
    "stations": [
        {
            "id": "st1",
            "name": "Gewaechshaus",
            "role": "OWNER",
            "isPublic": False,
            "sensors": [
                {
                    "id": "sensor1",
                    "name": "Wanne 1",
                    "active": True,
                    "types": [
                        {"type": "WATER_TEMPERATURE", "channelName": None},
                        {"type": "WATER_PH", "channelName": None},
                    ],
                }
            ],
        }
    ],
}


async def test_setup_entry_creates_device_and_entities(hass, aioclient_mock):
    """Bootstrap + states drive device registry, sensor entities and one event entity."""
    now = dt_util.utcnow().isoformat()
    states = [
        {
            "sensorId": "sensor1",
            "type": "WATER_TEMPERATURE",
            "channelName": None,
            "value": 21.5,
            "timestamp": now,
        },
        {
            "sensorId": "sensor1",
            "type": "WATER_PH",
            "channelName": None,
            "value": 6.8,
            "timestamp": now,
        },
    ]
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP)
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/states", json=states)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="u1",
        data={CONF_BASE_URL: BASE_URL, CONF_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hydronode.ws.HydroNodeWebSocketClient.start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, "st1")})
    assert device is not None
    assert device.name == "Gewaechshaus"
    assert device.manufacturer == "TexhFex Labs"

    entity_registry = er.async_get(hass)
    temp_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "sensor1_WATER_TEMPERATURE_"
    )
    ph_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "sensor1_WATER_PH_"
    )
    event_entity_id = entity_registry.async_get_entity_id("event", DOMAIN, "st1_events")

    assert temp_entity_id is not None
    assert ph_entity_id is not None
    assert event_entity_id is not None

    temp_state = hass.states.get(temp_entity_id)
    assert temp_state is not None
    assert temp_state.state == "21.5"
    assert temp_state.attributes.get("device_class") == "temperature"
    assert temp_state.attributes.get("unit_of_measurement") == "°C"
    # Entity name is only the measurement title; the device supplies the station prefix.
    assert temp_state.attributes.get("friendly_name") == "Gewaechshaus Water Temperature"

    ph_state = hass.states.get(ph_entity_id)
    assert ph_state.attributes.get("device_class") == "ph"

    # Unload should stop cleanly (WS task was never started, .stop() is a no-op then).
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state.value == "not_loaded"


FOLLOWED_STATION = {
    "id": "st2",
    "name": "Wetterstation",
    "role": "FOLLOWED",
    "isPublic": True,
    "sensors": [
        {
            "id": "sensor2",
            "name": "Aussensensor",
            "active": True,
            "types": [
                {"type": "NC_0_5", "channelName": "nc_0_5", "scale": 0.1},
                {"type": "TEMPERATURE", "channelName": None},
            ],
        }
    ],
}


async def test_unfollowed_station_is_removed_on_bootstrap_refresh(
    hass, aioclient_mock
):
    """A station that drops out of the bootstrap loses its device and entities."""
    now = dt_util.utcnow().isoformat()
    two_stations = {
        "user": BOOTSTRAP["user"],
        "stations": BOOTSTRAP["stations"] + [FOLLOWED_STATION],
    }
    states = [
        {
            "sensorId": "sensor2",
            "type": "NC_0_5",
            "channelName": "nc_0_5",
            "value": 2.20000004768372,
            "timestamp": now,
        }
    ]
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=two_stations)
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/states", json=states)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="u1",
        data={CONF_BASE_URL: BASE_URL, CONF_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hydronode.ws.HydroNodeWebSocketClient.start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    assert device_registry.async_get_device(identifiers={(DOMAIN, "st2")}) is not None
    nc_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "sensor2_NC_0_5_nc_0_5"
    )
    assert nc_entity_id is not None

    # Friendly name comes from the type display-name map, not the raw channelName.
    nc_state = hass.states.get(nc_entity_id)
    assert nc_state.attributes.get("friendly_name") == (
        "Wetterstation Particle Count >0.5µm"
    )
    # fPort scale 0.1 → one displayed decimal, stored in the registry options.
    nc_options = entity_registry.async_get(nc_entity_id).options.get("sensor", {})
    assert nc_options.get("suggested_display_precision") == 1

    # Unfollow: next bootstrap only contains st1.
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP)
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/states", json=[])

    coordinator = entry.runtime_data.coordinator
    await coordinator.async_refresh_bootstrap()
    await hass.async_block_till_done()

    assert device_registry.async_get_device(identifiers={(DOMAIN, "st2")}) is None
    assert (
        entity_registry.async_get_entity_id("sensor", DOMAIN, "sensor2_NC_0_5_nc_0_5")
        is None
    )
    assert entity_registry.async_get_entity_id("event", DOMAIN, "st2_events") is None
    # The owned station survives untouched.
    assert device_registry.async_get_device(identifiers={(DOMAIN, "st1")}) is not None
    assert (
        entity_registry.async_get_entity_id(
            "sensor", DOMAIN, "sensor1_WATER_TEMPERATURE_"
        )
        is not None
    )
