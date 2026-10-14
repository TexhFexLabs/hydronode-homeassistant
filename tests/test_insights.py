"""Incidents (V2) and analyses: /insights snapshot, WS pushes, entities, cleanup."""

from __future__ import annotations

import json
import pathlib
import re
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_TOKEN
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.hydronode.const import (
    CONF_BASE_URL,
    DOMAIN,
    EVENT_ANALYSIS,
    EVENT_INCIDENT,
)
from custom_components.hydronode.insights import (
    EVENT_TYPES,
    SEVERITY_OPTIONS,
    HydroNodeInsightsStore,
    incident_event_type,
    normalize_analysis,
    normalize_incident,
)

BASE_URL = "https://hydronode.example.com"
TOKEN = "hat_test1234567890"
INSIGHTS_URL = f"{BASE_URL}/api/ha/v1/insights"

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
                    "types": [{"type": "TEMPERATURE", "channelName": None}],
                }
            ],
        },
        {
            # Pseudo-station: a sensor without station, id == sensor id.
            "id": "solo",
            "name": "Balkon",
            "role": "OWNER",
            "isPublic": False,
            "sensors": [
                {
                    "id": "solo",
                    "name": "Balkon",
                    "active": True,
                    "types": [{"type": "TEMPERATURE", "channelName": None}],
                }
            ],
        },
    ],
}

INCIDENT = {
    "incidentId": "inc1",
    "sensorId": "sensor1",
    "stationId": "st1",
    "sensorName": "Wanne 1",
    "channel": {"canonicalType": "TEMPERATURE", "displayName": "Temperature"},
    "category": "THRESHOLD",
    "severity": "WARNING",
    "lifecycle": "OPEN",
    "direction": "UP",
    "triggerValue": 31.5,
    "occurrenceCount": 1,
    "actionRequired": True,
    "firstSeenAt": "2026-10-13T08:00:00Z",
    "lastSeenAt": "2026-10-13T08:05:00Z",
    "recoveredAt": None,
}

ANALYSIS = {
    "analysisId": "an1",
    "sensorId": "sensor1",
    "stationId": "st1",
    "sensorName": "Wanne 1",
    "generatedAt": "2026-10-13T07:00:00Z",
    "source": "LLM",
    "severity": "HIGH",
    "shouldAlert": True,
    "summary": "Temperature is rising faster than usual.",
    "findings": [{"title": "Fast rise", "detail": "3 K in 1 h", "certainty": "LIKELY"}],
    "recommendations": [{"actionCode": "VENTILATE", "text": "Open the window."}],
    "airQuality": {"value": 72, "band": "MODERATE", "limitingPollutant": "PM2_5"},
    "environmentScore": 0.8,
}


def _incident(**overrides: Any) -> dict[str, Any]:
    return {**deepcopy(INCIDENT), **overrides}


def _transition(reason: str, from_: str | None, to: str, revision: int):
    return {
        "reason": reason,
        "fromLifecycle": from_,
        "toLifecycle": to,
        "revision": revision,
    }


def _insights(incidents=(), analyses=()) -> dict[str, Any]:
    return {
        "stations": [
            {"id": "st1", "incidents": list(incidents), "analyses": list(analyses)}
        ]
    }


async def _setup(
    hass, aioclient_mock, insights: dict | None = None, status: int = 200
) -> tuple[MockConfigEntry, dict[str, Any]]:
    """Set up with a faked WS client; returns (entry, WS constructor kwargs)."""
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP)
    aioclient_mock.get(
        f"{BASE_URL}/api/ha/v1/states",
        json=[
            {
                "sensorId": "sensor1",
                "type": "TEMPERATURE",
                "channelName": None,
                "value": 21.5,
                "timestamp": dt_util.utcnow().isoformat(),
            }
        ],
    )
    if status == 200:
        aioclient_mock.get(INSIGHTS_URL, json=insights or {"stations": []})
    else:
        aioclient_mock.get(INSIGHTS_URL, status=status)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="u1",
        data={CONF_BASE_URL: BASE_URL, CONF_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.hydronode.HydroNodeWebSocketClient") as ws_cls:
        ws_cls.return_value.stop = AsyncMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, ws_cls.call_args.kwargs


def _entity_id(hass, platform: str, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


def _state(hass, platform: str, unique_id: str):
    entity_id = _entity_id(hass, platform, unique_id)
    assert entity_id is not None, f"{platform} {unique_id} was not created"
    return hass.states.get(entity_id)


# --- store ------------------------------------------------------------------------


def test_event_type_mapping():
    assert incident_event_type("OPEN", {"reason": "first_detection"}) == (
        "incident_opened"
    )
    assert incident_event_type("OPEN", {"fromLifecycle": "CANDIDATE"}) == (
        "incident_opened"
    )
    assert incident_event_type("ONGOING", {"reason": "severity_raised"}) == (
        "incident_escalated"
    )
    assert incident_event_type("RESOLVED", {"reason": "recovered"}) == (
        "incident_recovered"
    )
    for lifecycle in ("INVALIDATED", "SUPPRESSED", "EXPIRED"):
        assert incident_event_type(lifecycle, {}) == "incident_closed"
    for reason in ("still_present", "recovering", "condition_returned"):
        assert incident_event_type("ONGOING", {"reason": reason}) is None


def test_store_opens_updates_and_closes_incidents():
    store = HydroNodeInsightsStore()
    calls: list[str] = []
    store.async_add_listener("st1", lambda: calls.append("st1"))

    result = store.apply_incident(
        "st1",
        _incident(transition=_transition("first_detection", "CANDIDATE", "OPEN", 1)),
    )
    assert result is not None and result[2] == "incident_opened"
    assert store.highest_severity("st1") == "warning"

    store.apply_incident(
        "st1",
        _incident(
            severity="CRITICAL",
            lifecycle="ONGOING",
            transition=_transition("severity_raised", "OPEN", "ONGOING", 2),
        ),
    )
    assert len(store.open_incidents("st1")) == 1
    assert store.highest_severity("st1") == "critical"

    # An older revision arriving late is dropped.
    assert (
        store.apply_incident(
            "st1",
            _incident(transition=_transition("first_detection", None, "OPEN", 1)),
        )
        is None
    )
    assert store.highest_severity("st1") == "critical"

    store.apply_incident(
        "st1",
        _incident(
            lifecycle="RESOLVED",
            transition=_transition("recovered", "RECOVERING", "RESOLVED", 3),
        ),
    )
    assert store.open_incidents("st1") == []
    assert store.highest_severity("st1") == "none"
    assert len(calls) == 3


def test_store_keeps_only_newer_analysis():
    store = HydroNodeInsightsStore()
    assert store.apply_analysis("st1", ANALYSIS) is not None
    assert store.apply_analysis("st1", {**ANALYSIS, "analysisId": "old"}) is None
    older = {**ANALYSIS, "analysisId": "older", "generatedAt": "2026-10-12T07:00:00Z"}
    assert store.apply_analysis("st1", older) is None
    newer = {**ANALYSIS, "analysisId": "new", "generatedAt": "2026-10-13T09:00:00Z"}
    assert store.apply_analysis("st1", newer) is not None
    assert store.analysis("st1", "sensor1")["analysis_id"] == "new"


def test_push_during_snapshot_wins():
    """A push applied while /insights is in flight must not be undone by it."""
    store = HydroNodeInsightsStore()
    started = store.begin_snapshot()
    store.apply_incident(
        "st1",
        _incident(
            lifecycle="EXPIRED",
            transition=_transition("expired", "ONGOING", "EXPIRED", 5),
        ),
    )
    store.apply_incident(
        "st1",
        _incident(
            incidentId="inc2",
            transition=_transition("first_detection", None, "OPEN", 1),
        ),
    )
    newer = {**ANALYSIS, "generatedAt": "2026-10-13T09:00:00Z"}
    store.apply_analysis("st1", newer)

    # The snapshot was read before the pushes: still shows inc1, not inc2.
    store.apply_snapshot(
        started,
        {"st1": [normalize_incident(INCIDENT)]},
        {"st1": [normalize_analysis(ANALYSIS)]},
    )
    assert [i["incident_id"] for i in store.open_incidents("st1")] == ["inc2"]
    assert store.analysis("st1", "sensor1")["generated_at"].hour == 9

    # The next snapshot is authoritative again.
    store.apply_snapshot(store.begin_snapshot(), {}, {})
    assert store.open_incidents("st1") == []
    assert store.analysis("st1", "sensor1") is None


def test_retain_drops_lost_sensors():
    store = HydroNodeInsightsStore()
    store.apply_incident("st1", _incident())
    store.apply_analysis("st1", ANALYSIS)
    store.retain({"sensor1": "st1"})
    assert store.open_incidents("st1")
    store.retain({})
    assert store.open_incidents("st1") == []
    assert store.analysis("st1", "sensor1") is None


def test_malformed_payloads_are_rejected():
    store = HydroNodeInsightsStore()
    for bad in (
        {},
        _incident(incidentId=None),
        _incident(lifecycle="CANDIDATE"),
        _incident(lifecycle=5),
    ):
        assert store.apply_incident("st1", bad) is None
    for bad in (
        {},
        {**ANALYSIS, "generatedAt": "yesterday"},
        {**ANALYSIS, "generatedAt": "2026-10-13T07:00:00"},  # no timezone
        {**ANALYSIS, "sensorId": ""},
    ):
        assert store.apply_analysis("st1", bad) is None
    # Bad optional fields degrade to None instead of rejecting the analysis.
    analysis = normalize_analysis(
        {
            **ANALYSIS,
            "severity": "EXTREME",
            "airQuality": {"value": 140},
            "findings": "text",
            "environmentScore": float("nan"),
        }
    )
    assert analysis["severity"] is None
    assert analysis["air_quality"] is None
    assert analysis["findings"] == []
    assert analysis["environment_score"] is None


# --- setup / REST -----------------------------------------------------------------


async def test_insights_snapshot_populates_station_entities(hass, aioclient_mock):
    await _setup(
        hass,
        aioclient_mock,
        _insights(
            incidents=[INCIDENT, _incident(incidentId="inc2", severity="INFO")],
            analyses=[ANALYSIS],
        ),
    )

    open_incidents = _state(hass, "sensor", "st1_incidents_open")
    assert open_incidents.state == "2"
    assert open_incidents.attributes["friendly_name"] == "Gewaechshaus Open incidents"
    first = open_incidents.attributes["incidents"][0]
    assert first["sensor_name"] == "Wanne 1"
    assert first["channel"] == "Temperature"
    assert first["severity"] in ("warning", "info")
    assert first["lifecycle"] == "open"
    assert first["since"] == "2026-10-13T08:00:00+00:00"

    severity = _state(hass, "sensor", "st1_incident_severity")
    assert severity.state == "warning"
    assert severity.attributes["options"] == SEVERITY_OPTIONS
    assert severity.attributes["friendly_name"] == "Gewaechshaus Incident severity"

    incident = _state(hass, "binary_sensor", "st1_incident")
    assert incident.state == "on"
    assert incident.attributes["device_class"] == "problem"
    assert incident.attributes["friendly_name"] == "Gewaechshaus Incident"

    analysis = _state(hass, "sensor", "st1_sensor1_analysis")
    assert analysis.state == "high"
    assert analysis.attributes["friendly_name"] == "Gewaechshaus Wanne 1 analysis"
    assert analysis.attributes["summary"] == ANALYSIS["summary"]
    assert analysis.attributes["findings"][0]["title"] == "Fast rise"
    assert analysis.attributes["recommendations"] == [
        {"action_code": "VENTILATE", "text": "Open the window."}
    ]
    assert analysis.attributes["generated_at"] == "2026-10-13T07:00:00+00:00"
    assert analysis.attributes["source"] == "LLM"

    aqi = _state(hass, "sensor", "st1_sensor1_air_quality_index")
    assert aqi.state == "72"
    assert aqi.attributes["band"] == "MODERATE"
    assert aqi.attributes["friendly_name"] == ("Gewaechshaus Wanne 1 air quality index")

    analysis_time = _state(hass, "sensor", "st1_sensor1_analysis_time")
    assert analysis_time.state == "2026-10-13T07:00:00+00:00"

    events = _state(hass, "event", "st1_events")
    assert events.attributes["event_types"] == EVENT_TYPES

    # The pseudo-station has incident entities, but no analysis yet.
    assert _state(hass, "sensor", "solo_incidents_open").state == "0"
    assert _state(hass, "sensor", "solo_incident_severity").state == "none"
    assert _state(hass, "binary_sensor", "solo_incident").state == "off"
    assert _entity_id(hass, "sensor", "solo_solo_analysis") is None


async def test_missing_endpoint_is_silently_empty(hass, aioclient_mock):
    """An older backend without /insights (404) must not fail the setup."""
    entry, _ws = await _setup(hass, aioclient_mock, status=404)
    assert entry.state.value == "loaded"
    assert _state(hass, "sensor", "st1_incidents_open").state == "0"
    assert _state(hass, "sensor", "st1_incident_severity").state == "none"
    assert _state(hass, "binary_sensor", "st1_incident").state == "off"
    assert _entity_id(hass, "sensor", "st1_sensor1_analysis") is None


async def test_server_error_keeps_setup_alive(hass, aioclient_mock):
    entry, _ws = await _setup(hass, aioclient_mock, status=500)
    assert entry.state.value == "loaded"


async def test_items_of_unknown_sensors_are_dropped(hass, aioclient_mock):
    """The payload station id is not trusted; only bootstrap sensors count."""
    payload = {
        "stations": [
            {
                "id": "st1",
                "incidents": [_incident(sensorId="foreign"), "garbage"],
                "analyses": [{**ANALYSIS, "sensorId": "foreign"}, None],
            }
        ]
    }
    entry, _ws = await _setup(hass, aioclient_mock, payload)
    assert _state(hass, "sensor", "st1_incidents_open").state == "0"
    assert entry.runtime_data.insights.diagnostics()["analyses"] == 0


async def test_bootstrap_refresh_reloads_insights_and_drops_lost_stations(
    hass, aioclient_mock
):
    entry, _ws = await _setup(hass, aioclient_mock, _insights(incidents=[INCIDENT]))
    assert _state(hass, "sensor", "st1_incidents_open").state == "1"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP)
    aioclient_mock.get(INSIGHTS_URL, json={"stations": []})
    await entry.runtime_data.coordinator.async_refresh_bootstrap()
    await hass.async_block_till_done()
    assert any(str(call[1]) == INSIGHTS_URL for call in aioclient_mock.mock_calls)
    assert _state(hass, "sensor", "st1_incidents_open").state == "0"

    # Access lost: the station leaves the bootstrap, its data is discarded at once.
    entry.runtime_data.insights.apply_incident("st1", _incident())
    reduced = {**BOOTSTRAP, "stations": BOOTSTRAP["stations"][1:]}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=reduced)
    aioclient_mock.get(INSIGHTS_URL, json=_insights(incidents=[INCIDENT]))
    await entry.runtime_data.coordinator.async_refresh_bootstrap()
    await hass.async_block_till_done()
    assert entry.runtime_data.insights.open_incidents("st1") == []
    assert _entity_id(hass, "sensor", "st1_incidents_open") is None


# --- WebSocket --------------------------------------------------------------------


async def test_incident_push_updates_entities_and_fires_events(hass, aioclient_mock):
    _entry, ws = await _setup(hass, aioclient_mock)
    bus = async_capture_events(hass, EVENT_INCIDENT)

    opened = _incident(
        transition=_transition("first_detection", "CANDIDATE", "OPEN", 1)
    )
    ws["on_incident_updated"](opened)
    await hass.async_block_till_done()

    assert _state(hass, "sensor", "st1_incidents_open").state == "1"
    assert _state(hass, "binary_sensor", "st1_incident").state == "on"
    events = _state(hass, "event", "st1_events")
    assert events.attributes["event_type"] == "incident_opened"
    assert events.attributes["incident_id"] == "inc1"
    assert events.attributes["severity"] == "warning"
    assert events.attributes["reason"] == "first_detection"
    # Bus payload is the wire data, flat: HaIncident fields plus `transition`.
    assert bus[0].data == opened
    assert bus[0].data["sensorName"] == "Wanne 1"
    assert bus[0].data["severity"] == "WARNING"

    ws["on_incident_updated"](
        _incident(
            severity="CRITICAL",
            lifecycle="ONGOING",
            transition=_transition("severity_raised", "OPEN", "ONGOING", 2),
        )
    )
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "st1_incident_severity").state == "critical"
    assert _state(hass, "event", "st1_events").attributes["event_type"] == (
        "incident_escalated"
    )

    # still_present only updates the store, no event-entity trigger.
    ws["on_incident_updated"](
        _incident(
            severity="CRITICAL",
            lifecycle="ONGOING",
            transition=_transition("still_present", "ONGOING", "ONGOING", 3),
        )
    )
    await hass.async_block_till_done()
    assert _state(hass, "event", "st1_events").attributes["event_type"] == (
        "incident_escalated"
    )

    ws["on_incident_updated"](
        _incident(
            lifecycle="SUPPRESSED",
            transition=_transition("suppressed", "ONGOING", "SUPPRESSED", 4),
        )
    )
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "st1_incidents_open").state == "0"
    assert _state(hass, "binary_sensor", "st1_incident").state == "off"
    assert _state(hass, "event", "st1_events").attributes["event_type"] == (
        "incident_closed"
    )
    assert len(bus) == 4


async def test_incident_of_pseudo_station_lands_on_sensor_device(hass, aioclient_mock):
    _entry, ws = await _setup(hass, aioclient_mock)
    ws["on_incident_updated"](
        _incident(
            sensorId="solo",
            stationId=None,
            transition=_transition("recovered", "RECOVERING", "RESOLVED", 1),
            lifecycle="RESOLVED",
        )
    )
    await hass.async_block_till_done()
    assert _state(hass, "event", "solo_events").attributes["event_type"] == (
        "incident_recovered"
    )


async def test_push_for_unknown_sensor_is_ignored(hass, aioclient_mock):
    _entry, ws = await _setup(hass, aioclient_mock)
    bus = async_capture_events(hass, EVENT_INCIDENT)
    ws["on_incident_updated"](_incident(sensorId="foreign", stationId="st1"))
    ws["on_analysis_completed"]({**ANALYSIS, "sensorId": "foreign"})
    await hass.async_block_till_done()
    assert bus == []
    assert _state(hass, "sensor", "st1_incidents_open").state == "0"


async def test_analysis_push_creates_entities_and_fires_events(hass, aioclient_mock):
    _entry, ws = await _setup(hass, aioclient_mock)
    bus = async_capture_events(hass, EVENT_ANALYSIS)
    assert _entity_id(hass, "sensor", "st1_sensor1_analysis") is None

    ws["on_analysis_completed"](ANALYSIS)
    await hass.async_block_till_done()

    assert _state(hass, "sensor", "st1_sensor1_analysis").state == "high"
    assert _state(hass, "sensor", "st1_sensor1_air_quality_index").state == "72"
    events = _state(hass, "event", "st1_events")
    assert events.attributes["event_type"] == "analysis_ready"
    assert events.attributes["summary"] == ANALYSIS["summary"]
    assert events.attributes["air_quality_index"] == 72
    assert bus[0].data == ANALYSIS

    # An older analysis neither replaces the state nor fires again.
    ws["on_analysis_completed"](
        {**ANALYSIS, "severity": "INFO", "generatedAt": "2026-10-12T00:00:00Z"}
    )
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "st1_sensor1_analysis").state == "high"
    assert len(bus) == 1


async def test_analysis_without_air_quality_skips_index(hass, aioclient_mock):
    _entry, ws = await _setup(hass, aioclient_mock)
    ws["on_analysis_completed"](
        {**ANALYSIS, "sensorId": "solo", "airQuality": None, "severity": None}
    )
    await hass.async_block_till_done()
    analysis = _state(hass, "sensor", "solo_solo_analysis")
    assert analysis.state == "none"
    # Pseudo-station: the device already carries the sensor name.
    assert analysis.attributes["friendly_name"] == "Balkon Analysis"
    assert _state(hass, "sensor", "solo_solo_analysis_time").attributes[
        "friendly_name"
    ] == ("Balkon Analysis time")
    assert _entity_id(hass, "sensor", "solo_solo_air_quality_index") is None


async def test_reconnect_reloads_insights(hass, aioclient_mock):
    _entry, ws = await _setup(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP)
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/states", json=[])  # coordinator refresh
    aioclient_mock.get(INSIGHTS_URL, json=_insights(incidents=[INCIDENT]))
    await ws["on_reconnect"]()
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "st1_incidents_open").state == "1"


# --- registry / restore -----------------------------------------------------------


async def test_registry_cleanup_removes_v1_entities_and_keeps_v2(hass, aioclient_mock):
    registry = er.async_get(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="u1",
        data={CONF_BASE_URL: BASE_URL, CONF_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)
    for platform, unique_id in (
        ("sensor", "st1_analysis_air_quality_score"),
        ("binary_sensor", "st1_analysis_alert"),
    ):
        registry.async_get_or_create(platform, DOMAIN, unique_id, config_entry=entry)

    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP)
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/states", json=[])
    aioclient_mock.get(INSIGHTS_URL, json=_insights(analyses=[ANALYSIS]))
    with patch("custom_components.hydronode.HydroNodeWebSocketClient") as ws_cls:
        ws_cls.return_value.stop = AsyncMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _entity_id(hass, "sensor", "st1_analysis_air_quality_score") is None
    assert _entity_id(hass, "binary_sensor", "st1_analysis_alert") is None

    await entry.runtime_data.coordinator.async_refresh_bootstrap()
    await hass.async_block_till_done()
    for platform, unique_id in (
        ("sensor", "st1_incidents_open"),
        ("sensor", "st1_incident_severity"),
        ("binary_sensor", "st1_incident"),
        ("event", "st1_events"),
        ("sensor", "st1_sensor1_analysis"),
        ("sensor", "st1_sensor1_air_quality_index"),
        ("sensor", "st1_sensor1_analysis_time"),
    ):
        assert _entity_id(hass, platform, unique_id) is not None, unique_id


async def test_analysis_survives_reload_without_endpoint(hass, aioclient_mock):
    """Registered analysis entities restore their last value if /insights is gone."""
    entry, _ws = await _setup(hass, aioclient_mock, _insights(analyses=[ANALYSIS]))
    assert _state(hass, "sensor", "st1_sensor1_analysis").state == "high"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/bootstrap", json=BOOTSTRAP)
    aioclient_mock.get(f"{BASE_URL}/api/ha/v1/states", json=[])
    aioclient_mock.get(INSIGHTS_URL, status=404)
    with patch("custom_components.hydronode.HydroNodeWebSocketClient") as ws_cls:
        ws_cls.return_value.stop = AsyncMock()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    analysis = _state(hass, "sensor", "st1_sensor1_analysis")
    assert analysis.state == "high"
    assert analysis.attributes["summary"] == ANALYSIS["summary"]
    assert _state(hass, "sensor", "st1_sensor1_air_quality_index").state == "72"
    assert _state(hass, "sensor", "st1_sensor1_analysis_time").state == (
        "2026-10-13T07:00:00+00:00"
    )


# --- translations -----------------------------------------------------------------


def test_states_and_event_types_are_translated_in_every_language():
    base = pathlib.Path(__file__).parent.parent / "custom_components" / "hydronode"
    for language in ("en", "de"):
        translations = json.loads(
            (base / "translations" / f"{language}.json").read_text()
        )["entity"]
        for key in ("incident_severity", "analysis", "sensor_analysis"):
            assert set(translations["sensor"][key]["state"]) == set(SEVERITY_OPTIONS)
        event_states = translations["event"]["events"]["state_attributes"][
            "event_type"
        ]["state"]
        assert set(event_states) == set(EVENT_TYPES), language
        for key in (
            "sensor_analysis",
            "sensor_air_quality_index",
            "sensor_analysis_time",
        ):
            assert "{sensor_name}" in translations["sensor"][key]["name"]
    for option in SEVERITY_OPTIONS + EVENT_TYPES:
        assert re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", option)
