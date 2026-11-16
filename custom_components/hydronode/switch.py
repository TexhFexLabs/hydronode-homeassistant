"""Switch platform: writable on/off data points (full-access token only)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydroNodeConfigEntry
from .data_points import HydroNodePointEntity, control_unique_id, setup_point_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydroNodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switches and follow the bootstrap."""
    setup_point_platform(entry, "switch", async_add_entities, HydroNodePointSwitch)


class HydroNodePointSwitch(HydroNodePointEntity, SwitchEntity):
    """Sets the data point to 1 or 0. A device confirms on its next delivery."""

    def __init__(self, coordinator, client, station, sensor, point) -> None:
        super().__init__(
            coordinator, client, station, sensor, point, control_unique_id(point["id"])
        )

    @property
    def is_on(self) -> bool | None:
        value = self._value()
        if value not in (0.0, 1.0):
            return None
        return value == 1.0

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(0)
