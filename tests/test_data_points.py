"""Data platform (`data1`): kinds, value types, writable data points, stable IDs."""

from __future__ import annotations

import copy
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_TOKEN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronode.const import CONF_BASE_URL, DOMAIN

BASE_URL = "https://hydronode.example.com"
TOKEN = "hat_test1234567890"
READ_TOKEN = "hrt_test1234567890"

# An account as an older integration saw it: devices only, numbers only.
LEGACY_BOOTSTRAP = {
    "user": {"id": "u1", "displayName": "Felix"},
    "stations": [
        {
            "id": "st1",
            "name": "Greenhouse",
            "role": "OWNER",
            "isPublic": False,
            "sensors": [
                {
                    "id": "dev1",
                    "name": "Tank",
                    "active": True,
                    "types": [
                        {"type": "WATER_TEMPERATURE", "channelName": None},
                        {"type": "HEATER_MODE", "channelName": None},
                    ],
                }
            ],
        },
        {
            "id": "dev2",
            "name": "Field node",
            "role": "OWNER",
            "isPublic": False,
            "sensors": [
                {
                    "id": "dev2",
                    "name": "Field node",
                    "active": True,
                    "types": [{"type": "SOIL_MOISTURE", "channelName": "soil"}],
                }
            ],
        },
    ],
}


def point(point_id, name, type_, value_type, **extra):
    base = {
        "id": point_id,
        "name": name,
        "type": type_,
        "channelName": extra.pop("channelName", None),
        "valueType": value_type,
        "enumOptions": None,
        "unit": None,
        "decimals": 1,
        "origin": "DEVICE",
        "writable": False,
        "canWrite": False,
        "write": None,
    }
    base.update(extra)
    return base


HEATER_OPTIONS = [
    {"value": 0, "label": "Off"},
    {"value": 1, "label": "Eco"},
    {"value": 2, "label": "Boost"},
]


def data_bootstrap() -> dict:
    """The same account with the capability: kinds, data points, an MQTT plug."""
    bootstrap = copy.deepcopy(LEGACY_BOOTSTRAP)
    tank = bootstrap["stations"][0]["sensors"][0]
    tank["kind"] = "DEVICE"
    tank["dataPoints"] = [
        point("p-temp", "Water temperature", "WATER_TEMPERATURE", "NUMBER", unit="°C"),
        point(
            "p-heater",
            "Heater mode",
            "HEATER_MODE",
            "ENUM",
            enumOptions=HEATER_OPTIONS,
            writable=True,
            canWrite=True,
            write={"kind": "COMMAND", "min": None, "max": None, "step": None},
        ),
        point(
            "p-pump",
            "Pump relay",
            "CUSTOM",
            "BOOL",
            channelName="pump",
            enumOptions=[{"value": 0, "label": "Off"}, {"value": 1, "label": "On"}],
            writable=True,
            canWrite=True,
            write={"kind": "COMMAND", "min": None, "max": None, "step": None},
        ),
        point(
            "p-setpoint",
            "Setpoint",
            "CUSTOM",
            "NUMBER",
            channelName="setpoint",
            unit="°C",
            writable=True,
            canWrite=True,
            write={"kind": "COMMAND", "min": 10.0, "max": 30.0, "step": 0.5},
        ),
    ]
    field = bootstrap["stations"][1]["sensors"][0]
    field["kind"] = "DEVICE"
    field["dataPoints"] = [
        point(
            "p-soil",
            "soil",
            "SOIL_MOISTURE",
            "BOOL",
            channelName="soil",
            enumOptions=[{"value": 0, "label": "Dry"}, {"value": 1, "label": "Wet"}],
            origin="LORAWAN",
        )
    ]
    bootstrap["stations"].append(
        {
            "id": "plug",
            "name": "Shelly plug",
            "role": "OWNER",
            "isPublic": False,
            "sensors": [
                {
                    "id": "plug",
                    "name": "Shelly plug",
                    "active": True,
                    # Series the plug stored; an MQTT sensor makes no old entities.
                    "types": [{"type": "POWER", "channelName": "power"}],
                    "kind": "MQTT",
                    "dataPoints": [
                        point(
                            "p-power",
                            "power",
                            "POWER",
                            "NUMBER",
                            channelName="power",
                            unit="W",
                            origin="MQTT",
                        ),
                        point(
                            "p-on",
                            "on",
                            "CUSTOM",
                            "BOOL",
                            channelName="on",
                            origin="MQTT",
                        ),
                        point(
                            "p-status",
                            "status",
                            "CUSTOM",
                            "TEXT",
                            channelName="status",
                            origin="MQTT",
                        ),
                    ],
                }
            ],
        }
    )
    return bootstrap


def legacy_states(now: str) -> list[dict]:
    return [
        {
            "sensorId": "dev1",
            "type": "WATER_TEMPERATURE",
            "channelName": None,
            "value": 21.5,
            "timestamp": now,
        },
        {
            "sensorId": "dev1",
            "type": "HEATER_MODE",
            "channelName": None,
            "value": 1.0,
            "timestamp": now,
        },
        {
            "sensorId": "dev2",
            "type": "SOIL_MOISTURE",
            "channelName": "soil",
            "value": 1.0,
            "timestamp": now,
        },
    ]


def data_states(now: str) -> list[dict]:
    rows = legacy_states(now) + [
        {
            "sensorId": "plug",
            "type": "POWER",
            "channelName": "power",
            "value": 41.5,
            "timestamp": now,
        }
    ]

    def row(sensor, type_, channel, point_id, value, **extra):
        return {
            "sensorId": sensor,
            "type": type_,
            "channelName": channel,
            "value": value,
            "timestamp": now,
            "dataPointId": point_id,
            **extra,
        }

    return rows + [
        # Same series key as an old row, but a different value: must not leak
        # into the old entity.
        row("dev1", "WATER_TEMPERATURE", None, "p-temp", 99.0),
        row("dev1", "HEATER_MODE", None, "p-heater", 1.0, label="Eco"),
        row(
            "dev1",
            "CUSTOM",
            "pump",
            "p-pump",
            0.0,
            label="Off",
            desired={"value": 0.0, "label": "Off", "state": "CONFIRMED"},
        ),
        row("dev2", "SOIL_MOISTURE", "soil", "p-soil", 1.0, label="Wet"),
        row("plug", "POWER", "power", "p-power", 41.5),
        row("plug", "CUSTOM", "on", "p-on", 1.0, label="On"),
        row("plug", "CUSTOM", "status", "p-status", 0, text="Heating"),
        # Broken data row: dropped alone, the poll still succeeds.
        {"sensorId": "plug", "type": "CUSTOM", "value": 1, "dataPointId": "x"},
    ]


def mock_api(aioclient_mock, bootstrap, states):
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=bootstrap)
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/states", json=states)
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/insights", json={"stations": []})


async def setup_entry(hass, token=TOKEN) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="u1",
        data={CONF_BASE_URL: BASE_URL, CONF_TOKEN: token},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.hydronode.ws.HydroNodeWebSocketClient.start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def registry_snapshot(hass, entry) -> dict[tuple[str, str], str]:
    registry = er.async_get(hass)
    return {
        (entity.domain, entity.unique_id): entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


def entity_id(hass, domain, unique_id):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


async def test_existing_entities_keep_their_ids(hass, aioclient_mock):
    """An account set up by 0.5.x keeps every entity, ID and entity_id with data1."""
    now = dt_util.utcnow().isoformat()
    mock_api(aioclient_mock, LEGACY_BOOTSTRAP, legacy_states(now))
    entry = await setup_entry(hass)
    before = registry_snapshot(hass, entry)
    assert ("sensor", "dev1_WATER_TEMPERATURE_") in before
    assert ("sensor", "dev1_HEATER_MODE_") in before
    assert ("sensor", "dev2_SOIL_MOISTURE_soil") in before
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    # Same account, now answered as DATA.
    mock_api(aioclient_mock, data_bootstrap(), data_states(now))
    with patch("custom_components.hydronode.ws.HydroNodeWebSocketClient.start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    after = registry_snapshot(hass, entry)

    for key, old_entity_id in before.items():
        assert after.get(key) == old_entity_id, key
    # New: the plug's station entities, and one entity per data point by its id.
    new = {key for key in set(after) - set(before) if key[1].startswith("dp_")}
    assert {
        key for key in set(after) - set(before) if not key[1].startswith("dp_")
    } == {
        ("binary_sensor", "plug_incident"),
        ("sensor", "plug_incidents_open"),
        ("sensor", "plug_incident_severity"),
        ("event", "plug_events"),
    }
    assert new == {
        ("select", "dp_p-heater_write"),
        ("switch", "dp_p-pump_write"),
        ("number", "dp_p-setpoint_write"),
        ("binary_sensor", "dp_p-soil"),
        ("sensor", "dp_p-power"),
        ("binary_sensor", "dp_p-on"),
        ("sensor", "dp_p-status"),
    }
    # The device number p-temp keeps only its old entity, with the old value.
    temp = hass.states.get(before[("sensor", "dev1_WATER_TEMPERATURE_")])
    assert temp.state == "21.5"


async def test_every_request_announces_the_capability(hass, aioclient_mock):
    now = dt_util.utcnow().isoformat()
    mock_api(aioclient_mock, data_bootstrap(), data_states(now))
    await setup_entry(hass)
    assert aioclient_mock.mock_calls
    for _method, _url, _data, headers in aioclient_mock.mock_calls:
        assert headers["X-HydroNode-Capabilities"] == "data1"


async def test_value_types_and_kinds(hass, aioclient_mock):
    now = dt_util.utcnow().isoformat()
    mock_api(aioclient_mock, data_bootstrap(), data_states(now))
    await setup_entry(hass)

    # MQTT sensor: device with a model, no old-style entity.
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "plug")})
    assert device.model == "MQTT sensor"
    assert entity_id(hass, "sensor", "plug_POWER_power") is None
    # Devices keep their device entry without a model.
    tank = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "st1")})
    assert tank.model is None

    power = hass.states.get(entity_id(hass, "sensor", "dp_p-power"))
    assert power.state == "41.5"
    assert power.attributes["unit_of_measurement"] == "W"
    assert power.attributes["device_class"] == "power"
    assert power.attributes["data_point_id"] == "p-power"

    plug_on = hass.states.get(entity_id(hass, "binary_sensor", "dp_p-on"))
    assert plug_on.state == "on"
    assert plug_on.attributes["label"] == "On"

    status = hass.states.get(entity_id(hass, "sensor", "dp_p-status"))
    assert status.state == "Heating"

    # A LoRaWAN number read as on/off: an extra binary sensor with its labels.
    soil = hass.states.get(entity_id(hass, "binary_sensor", "dp_p-soil"))
    assert soil.state == "on"
    assert soil.attributes["label"] == "Wet"
    assert soil.attributes["friendly_name"] == "Field node soil"


async def test_writable_points_are_controls(hass, aioclient_mock):
    now = dt_util.utcnow().isoformat()
    mock_api(aioclient_mock, data_bootstrap(), data_states(now))
    await setup_entry(hass)

    heater_id = entity_id(hass, "select", "dp_p-heater_write")
    heater = hass.states.get(heater_id)
    assert heater.state == "Eco"
    assert heater.attributes["options"] == ["Off", "Eco", "Boost"]
    # A choice the token can write has no extra read entity.
    assert entity_id(hass, "sensor", "dp_p-heater") is None

    setpoint = hass.states.get(entity_id(hass, "number", "dp_p-setpoint_write"))
    assert setpoint.attributes["min"] == 10.0
    assert setpoint.attributes["max"] == 30.0
    assert setpoint.attributes["step"] == 0.5

    pump_id = entity_id(hass, "switch", "dp_p-pump_write")
    assert hass.states.get(pump_id).state == "off"

    put_url = f"{BASE_URL}/api/data/v1/points/p-pump/value"
    aioclient_mock.put(
        put_url,
        status=202,
        json={
            "id": "p-pump",
            "latest": {"value": 0.0, "label": "Off", "at": now},
            "desired": {"value": 1.0, "label": "On", "state": "PENDING", "at": now},
        },
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": pump_id}, blocking=True
    )
    method, url, data, headers = aioclient_mock.mock_calls[-1]
    assert method == "PUT"
    assert str(url) == put_url
    assert data == {"value": 1}
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    # Pending: the switch shows what was asked until the device confirms.
    pump = hass.states.get(pump_id)
    assert pump.state == "on"
    assert pump.attributes["write_state"] == "pending"

    select_url = f"{BASE_URL}/api/data/v1/points/p-heater/value"
    aioclient_mock.put(
        select_url, status=202, json={"id": "p-heater", "latest": None, "desired": None}
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": heater_id, "option": "Boost"},
        blocking=True,
    )
    assert aioclient_mock.mock_calls[-1][2] == {"value": 2}


async def test_refused_write_tells_why(hass, aioclient_mock):
    now = dt_util.utcnow().isoformat()
    mock_api(aioclient_mock, data_bootstrap(), data_states(now))
    entry = await setup_entry(hass)
    aioclient_mock.put(
        f"{BASE_URL}/api/data/v1/points/p-pump/value",
        status=429,
        json={"error": "TOO_SOON", "retryAfterSeconds": 2},
    )
    pump_id = entity_id(hass, "switch", "dp_p-pump_write")
    with pytest.raises(HomeAssistantError, match="Try again in a few seconds"):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": pump_id}, blocking=True
        )
    # A refused write is no auth failure.
    assert entry.runtime_data.coordinator.auth_failed is False


async def test_read_access_and_read_tokens_never_switch(hass, aioclient_mock):
    now = dt_util.utcnow().isoformat()
    bootstrap = data_bootstrap()
    # The share only allows reading: the pump stays a read entity.
    pump = bootstrap["stations"][0]["sensors"][0]["dataPoints"][2]
    pump["canWrite"] = False
    mock_api(aioclient_mock, bootstrap, data_states(now))
    entry = await setup_entry(hass)
    assert entity_id(hass, "switch", "dp_p-pump_write") is None
    assert hass.states.get(entity_id(hass, "binary_sensor", "dp_p-pump")).state == "off"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    # A read token makes no control at all, whatever the server says.
    mock_api(aioclient_mock, data_bootstrap(), data_states(now))
    await setup_entry(hass, token=READ_TOKEN)
    for unique_id in ("dp_p-pump_write", "dp_p-heater_write"):
        assert entity_id(hass, "switch", unique_id) is None
        assert entity_id(hass, "select", unique_id) is None
    assert entity_id(hass, "number", "dp_p-setpoint_write") is None
    heater = hass.states.get(entity_id(hass, "sensor", "dp_p-heater"))
    assert heater.state == "Eco"
    assert heater.attributes["device_class"] == "enum"
    assert entity_id(hass, "sensor", "dp_p-setpoint") is None


async def test_older_server_keeps_the_old_world(hass, aioclient_mock):
    """No kinds, no data points in the answer: exactly the entities of 0.5.x."""
    now = dt_util.utcnow().isoformat()
    mock_api(aioclient_mock, LEGACY_BOOTSTRAP, legacy_states(now))
    entry = await setup_entry(hass)
    assert {unique_id for _domain, unique_id in registry_snapshot(hass, entry)} >= {
        "dev1_WATER_TEMPERATURE_",
        "dev1_HEATER_MODE_",
        "dev2_SOIL_MOISTURE_soil",
    }
    assert not [
        unique_id
        for _domain, unique_id in registry_snapshot(hass, entry)
        if unique_id.startswith("dp_")
    ]


async def test_push_of_a_device_series_moves_its_data_point(hass, aioclient_mock):
    """value.updated has no data point id; the series key routes it."""
    now = dt_util.utcnow().isoformat()
    mock_api(aioclient_mock, data_bootstrap(), data_states(now))
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.coordinator
    later = dt_util.utcnow().isoformat()
    assert coordinator.apply_ws_value_update(
        {
            "sensorId": "dev1",
            "type": "HEATER_MODE",
            "channelName": None,
            "value": 2.0,
            "timestamp": later,
        }
    )
    await hass.async_block_till_done()
    heater = hass.states.get(entity_id(hass, "select", "dp_p-heater_write"))
    assert heater.state == "Boost"
