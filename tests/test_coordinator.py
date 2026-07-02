"""Tests for the HydroNode DataUpdateCoordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.hydronode.coordinator import HydroNodeCoordinator

STATES = [
    {
        "sensorId": "s1",
        "type": "WATER_TEMPERATURE",
        "channelName": None,
        "value": 21.5,
        "timestamp": "2026-07-02T14:30:22Z",
    },
    {
        "sensorId": "s1",
        "type": "WATER_PH",
        "channelName": None,
        "value": 6.8,
        "timestamp": "2026-07-02T14:29:15Z",
    },
    {
        "sensorId": "s2",
        "type": "TEMPERATURE",
        "channelName": "channel_1",
        "value": 19.3,
        "timestamp": "2026-07-02T14:30:45Z",
    },
]

BOOTSTRAP = {
    "user": {"id": "u1", "displayName": "Test User"},
    "stations": [
        {
            "id": "st1",
            "name": "Gewaechshaus",
            "role": "OWNER",
            "isPublic": False,
            "sensors": [],
        }
    ],
}


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.states.return_value = STATES
    client.bootstrap.return_value = BOOTSTRAP
    return client


async def test_async_update_data_indexes_states_by_sensor_type_channel(hass, mock_client):
    """States are indexed as dict[(sensorId, type, channelName)] = state."""
    coordinator = HydroNodeCoordinator(hass, mock_client, poll_interval=60)

    data = await coordinator._async_update_data()

    assert set(data.keys()) == {
        ("s1", "WATER_TEMPERATURE", None),
        ("s1", "WATER_PH", None),
        ("s2", "TEMPERATURE", "channel_1"),
    }
    assert data[("s1", "WATER_TEMPERATURE", None)]["value"] == 21.5
    assert data[("s2", "TEMPERATURE", "channel_1")]["value"] == 19.3


async def test_async_update_data_refreshes_stale_bootstrap(hass, mock_client):
    """The first poll always refreshes bootstrap; a fresh one does not."""
    coordinator = HydroNodeCoordinator(hass, mock_client, poll_interval=60)
    assert coordinator._bootstrap_is_stale() is True

    await coordinator._async_update_data()

    mock_client.bootstrap.assert_awaited_once()
    assert coordinator.bootstrap == BOOTSTRAP
    assert coordinator._bootstrap_is_stale() is False

    await coordinator._async_update_data()
    mock_client.bootstrap.assert_awaited_once()  # still just once, not stale yet


async def test_new_entity_listener_invoked_on_bootstrap_refresh(hass, mock_client):
    """Listeners registered via add_new_entity_listener fire with the bootstrap payload."""
    coordinator = HydroNodeCoordinator(hass, mock_client, poll_interval=60)
    seen: list[dict] = []
    coordinator.add_new_entity_listener(seen.append)

    await coordinator.async_refresh_bootstrap()

    assert len(seen) == 1
    assert seen[0]["user"]["id"] == "u1"


async def test_apply_ws_value_update_patches_coordinator_data(hass, mock_client):
    """A WS value.updated push patches the indexed data without a full re-poll."""
    coordinator = HydroNodeCoordinator(hass, mock_client, poll_interval=60)
    await coordinator.async_refresh()

    coordinator.apply_ws_value_update(
        {
            "sensorId": "s1",
            "type": "WATER_TEMPERATURE",
            "channelName": None,
            "value": 99.9,
            "timestamp": "2026-07-02T15:00:00Z",
        }
    )

    assert coordinator.data[("s1", "WATER_TEMPERATURE", None)]["value"] == 99.9
    # Unrelated keys are preserved.
    assert coordinator.data[("s1", "WATER_PH", None)]["value"] == 6.8


async def test_filter_stations_excludes_followed(hass):
    """filter_stations drops FOLLOWED stations when include_followed is False."""
    bootstrap = {
        "stations": [
            {"id": "a", "role": "OWNER"},
            {"id": "b", "role": "SHARED"},
            {"id": "c", "role": "FOLLOWED"},
        ]
    }

    assert [s["id"] for s in HydroNodeCoordinator.filter_stations(bootstrap, True)] == [
        "a",
        "b",
        "c",
    ]
    assert [
        s["id"] for s in HydroNodeCoordinator.filter_stations(bootstrap, False)
    ] == ["a", "b"]
