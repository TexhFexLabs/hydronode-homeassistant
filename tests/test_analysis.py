"""Analysis entities fed by the `anomaly.ai_analyzed` WebSocket push."""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_TOKEN
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronode.const import (
    ALERT_SEVERITY_OPTIONS,
    ANOMALY_TYPE_OPTIONS,
    CONF_BASE_URL,
    DOMAIN,
)

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
                    "types": [{"type": "TEMPERATURE", "channelName": None}],
                }
            ],
        }
    ],
}

AI_ANALYZED = {
    "anomalyId": "an1",
    "sensorId": "sensor1",
    "stationId": "st1",
    "sensorName": "Wanne 1",
    "anomalyType": "DRIFT",
    "deviation": 3.3682294,
    "currentValue": 4.03,
    "expectedValue": 0.39787644,
    "reason": "Gradual drift: reference avg 0.89 (6h), now 4.03 (3.37s)",
    "timestamp": "2026-09-10T11:10:47.316883Z",
    "values": {"UV_INDEX:UVraw": 3.26},
    "provider": "bedrock",
    "analysis": {
        "explanation": "The UV Index has shown a significant increase.",
        "confidence": 0.7,
        "air_quality_score": 0.9,
        "is_ventilation_event": False,
        "should_alert": True,
        "alert_severity": "MEDIUM",
        "environment_score": 0.8,
        "affected_sensors": ["UV_INDEX"],
        "recommendation": "Investigate the cause of the UV Index anomaly.",
    },
}


async def _setup(hass, aioclient_mock) -> tuple[MockConfigEntry, Any]:
    """Set up the entry with a faked WS client and return (entry, on_ai_analyzed)."""
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

    return entry, ws_cls.call_args.kwargs["on_ai_analyzed"]


def _entity_id(hass, platform: str, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"st1_analysis_{key}"
    )


async def test_ai_analysis_push_populates_analysis_entities(hass, aioclient_mock):
    """One `ai_analyzed` push fills all eight analysis entities of the station."""
    _entry, on_ai_analyzed = await _setup(hass, aioclient_mock)

    on_ai_analyzed(AI_ANALYZED)
    await hass.async_block_till_done()

    expected_sensors = {
        "air_quality_score": "90.0",
        "environment_score": "80.0",
        "confidence": "70.0",
        "alert_severity": "MEDIUM",
        "anomaly_type": "DRIFT",
    }
    for key, value in expected_sensors.items():
        entity_id = _entity_id(hass, "sensor", key)
        assert entity_id is not None, f"sensor for {key} was not created"
        assert hass.states.get(entity_id).state == value, key

    timestamp_id = _entity_id(hass, "sensor", "analysis_timestamp")
    assert timestamp_id is not None
    # Home Assistant renders timestamp sensors truncated to whole seconds.
    assert hass.states.get(timestamp_id).state == "2026-09-10T11:10:47+00:00"

    expected_binary = {"ventilation_event": "off", "alert": "on"}
    for key, value in expected_binary.items():
        entity_id = _entity_id(hass, "binary_sensor", key)
        assert entity_id is not None, f"binary_sensor for {key} was not created"
        assert hass.states.get(entity_id).state == value, key


async def test_missing_environment_score_stays_unknown(hass, aioclient_mock):
    """A null `environment_score` must not become 0 %."""
    _entry, on_ai_analyzed = await _setup(hass, aioclient_mock)

    payload = {**AI_ANALYZED, "analysis": {**AI_ANALYZED["analysis"]}}
    payload["analysis"]["environment_score"] = None
    on_ai_analyzed(payload)
    await hass.async_block_till_done()

    entity_id = _entity_id(hass, "sensor", "environment_score")
    assert hass.states.get(entity_id).state == "unknown"
    # The other scores of the same payload still arrive.
    assert (
        hass.states.get(_entity_id(hass, "sensor", "air_quality_score")).state == "90.0"
    )


async def test_unknown_anomaly_type_is_reported_as_unknown(hass, aioclient_mock):
    """A type outside ANOMALY_TYPE_OPTIONS must not break the enum sensor."""
    _entry, on_ai_analyzed = await _setup(hass, aioclient_mock)

    on_ai_analyzed({**AI_ANALYZED, "anomalyType": "SOMETHING_NEW"})
    await hass.async_block_till_done()

    assert (
        hass.states.get(_entity_id(hass, "sensor", "anomaly_type")).state == "unknown"
    )
    # An unusable enum value must not take the rest of the payload down with it.
    assert (
        hass.states.get(_entity_id(hass, "sensor", "alert_severity")).state == "MEDIUM"
    )


async def test_plain_anomaly_event_leaves_analysis_untouched(hass, aioclient_mock):
    """`anomaly` events carry no analysis, so they must not clear the entities."""
    _entry, on_ai_analyzed = await _setup(hass, aioclient_mock)

    on_ai_analyzed(AI_ANALYZED)
    await hass.async_block_till_done()

    entry = _entry
    for listener in list(entry.runtime_data.event_listeners):
        listener("anomaly", {"stationId": "st1", "anomalyType": "SPIKE"})
    await hass.async_block_till_done()

    assert (
        hass.states.get(_entity_id(hass, "sensor", "air_quality_score")).state == "90.0"
    )
    assert hass.states.get(_entity_id(hass, "sensor", "anomaly_type")).state == "DRIFT"


async def test_analysis_survives_a_reload(hass, aioclient_mock):
    """After a restart the last analysis is restored, not blanked."""
    entry, on_ai_analyzed = await _setup(hass, aioclient_mock)

    on_ai_analyzed(AI_ANALYZED)
    await hass.async_block_till_done()

    with patch("custom_components.hydronode.HydroNodeWebSocketClient") as ws_cls:
        ws_cls.return_value.stop = AsyncMock()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    # The store is empty again after the reload; these values come from the
    # restore cache, because the backend has no endpoint to seed them.
    assert (
        hass.states.get(_entity_id(hass, "sensor", "air_quality_score")).state == "90.0"
    )
    assert (
        hass.states.get(_entity_id(hass, "sensor", "alert_severity")).state == "MEDIUM"
    )
    assert hass.states.get(_entity_id(hass, "binary_sensor", "alert")).state == "on"
    assert (
        hass.states.get(_entity_id(hass, "binary_sensor", "ventilation_event")).state
        == "off"
    )


async def test_bootstrap_refresh_keeps_analysis_entities(hass, aioclient_mock):
    """The registry cleanup must not delete the analysis entities of a live station."""
    entry, on_ai_analyzed = await _setup(hass, aioclient_mock)

    on_ai_analyzed(AI_ANALYZED)
    await hass.async_block_till_done()

    await entry.runtime_data.coordinator.async_refresh_bootstrap()
    await hass.async_block_till_done()

    for platform, key in (
        ("sensor", "air_quality_score"),
        ("sensor", "analysis_timestamp"),
        ("binary_sensor", "alert"),
    ):
        entity_id = _entity_id(hass, platform, key)
        assert entity_id is not None, f"{platform}.{key} was removed by the cleanup"
        assert hass.states.get(entity_id) is not None


async def test_entity_names_come_from_translations(hass, aioclient_mock):
    """Entity names are translated, the device supplies the station prefix."""
    _entry, on_ai_analyzed = await _setup(hass, aioclient_mock)

    on_ai_analyzed(AI_ANALYZED)
    await hass.async_block_till_done()

    names = {
        ("sensor", "air_quality_score"): "Gewaechshaus Air quality",
        ("sensor", "environment_score"): "Gewaechshaus Environment",
        ("sensor", "confidence"): "Gewaechshaus Analysis confidence",
        ("sensor", "alert_severity"): "Gewaechshaus Alert severity",
        ("sensor", "anomaly_type"): "Gewaechshaus Anomaly type",
        ("sensor", "analysis_timestamp"): "Gewaechshaus Last analysis",
        ("binary_sensor", "ventilation_event"): "Gewaechshaus Ventilation event",
        ("binary_sensor", "alert"): "Gewaechshaus Alert",
    }
    for (platform, key), expected in names.items():
        state = hass.states.get(_entity_id(hass, platform, key))
        assert state.attributes.get("friendly_name") == expected, key


async def test_every_declared_anomaly_type_renders(hass, aioclient_mock):
    """Each value in ANOMALY_TYPE_OPTIONS must survive as a state, none as unknown."""
    _entry, on_ai_analyzed = await _setup(hass, aioclient_mock)
    entity_id = None

    for anomaly_type in ANOMALY_TYPE_OPTIONS:
        on_ai_analyzed({**AI_ANALYZED, "anomalyType": anomaly_type})
        await hass.async_block_till_done()
        entity_id = entity_id or _entity_id(hass, "sensor", "anomaly_type")
        assert hass.states.get(entity_id).state == anomaly_type

    # The declared options must reach Home Assistant, otherwise it rejects the state.
    assert hass.states.get(entity_id).attributes["options"] == ANOMALY_TYPE_OPTIONS


async def test_every_declared_alert_severity_renders(hass, aioclient_mock):
    """Each value in ALERT_SEVERITY_OPTIONS must survive as a state."""
    _entry, on_ai_analyzed = await _setup(hass, aioclient_mock)
    entity_id = None

    for severity in ALERT_SEVERITY_OPTIONS:
        payload = {**AI_ANALYZED, "analysis": {**AI_ANALYZED["analysis"]}}
        payload["analysis"]["alert_severity"] = severity
        on_ai_analyzed(payload)
        await hass.async_block_till_done()
        entity_id = entity_id or _entity_id(hass, "sensor", "alert_severity")
        assert hass.states.get(entity_id).state == severity

    assert hass.states.get(entity_id).attributes["options"] == ALERT_SEVERITY_OPTIONS


def test_enum_options_are_translated_in_every_language():
    """A declared option without a translation shows as a raw key in the UI."""
    base = pathlib.Path(__file__).parent.parent / "custom_components" / "hydronode"
    expected = {
        "anomaly_type": ANOMALY_TYPE_OPTIONS,
        "alert_severity": ALERT_SEVERITY_OPTIONS,
    }
    for language in ("en", "de"):
        translations = json.loads(
            (base / "translations" / f"{language}.json").read_text()
        )
        sensors = translations["entity"]["sensor"]
        for key, options in expected.items():
            translated = sensors[key]["state"]
            missing = [option for option in options if option not in translated]
            assert not missing, f"{language}.json misses {key} states {missing}"
            extra = [state for state in translated if state not in options]
            assert not extra, f"{language}.json has stale {key} states {extra}"


async def test_invalid_analysis_does_not_clear_latest(hass, aioclient_mock):
    entry, on_ai_analyzed = await _setup(hass, aioclient_mock)
    on_ai_analyzed(AI_ANALYZED)
    on_ai_analyzed({**AI_ANALYZED, "analysis": "malformed"})
    await hass.async_block_till_done()
    assert (
        hass.states.get(_entity_id(hass, "sensor", "air_quality_score")).state == "90.0"
    )
    assert entry.runtime_data.analysis_store.latest("st1") == AI_ANALYZED


async def test_out_of_order_analysis_is_ignored(hass, aioclient_mock):
    _entry, on_ai_analyzed = await _setup(hass, aioclient_mock)
    on_ai_analyzed(AI_ANALYZED)
    on_ai_analyzed(
        {**AI_ANALYZED, "timestamp": "2026-09-09T10:00:00Z", "anomalyType": "SPIKE"}
    )
    await hass.async_block_till_done()
    assert hass.states.get(_entity_id(hass, "sensor", "anomaly_type")).state == "DRIFT"


def test_scores_reject_non_finite_and_out_of_range_values():
    from custom_components.hydronode.analysis import score_percent

    score = score_percent("confidence")
    for value in (float("nan"), float("inf"), True, -0.1, 1.1, 10**400):
        assert score({"analysis": {"confidence": value}}) is None
