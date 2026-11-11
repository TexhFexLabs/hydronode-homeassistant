"""Number platform: writable number data points (full-access token only)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydroNodeConfigEntry
from .data_points import HydroNodePointEntity, control_unique_id, setup_point_platform

# Without limits on the data point: a wide box instead of a 0..100 slider.
DEFAULT_MIN = -1_000_000.0
DEFAULT_MAX = 1_000_000.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydroNodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the numbers and follow the bootstrap."""
    setup_point_platform(entry, "number", async_add_entities, HydroNodePointNumber)


class HydroNodePointNumber(HydroNodePointEntity, NumberEntity):
    """Sets a number; min, max and step come from the data point's write binding."""

    def __init__(self, coordinator, client, station, sensor, point) -> None:
        super().__init__(
            coordinator, client, station, sensor, point, control_unique_id(point["id"])
        )
        self._attr_native_min_value = (
            point["min"] if point["min"] is not None else DEFAULT_MIN
        )
        self._attr_native_max_value = (
            point["max"] if point["max"] is not None else DEFAULT_MAX
        )
        decimals = point["decimals"] if point["decimals"] is not None else 0
        self._attr_native_step = (
            point["step"] if point["step"] is not None else 10.0**-decimals
        )
        self._attr_native_unit_of_measurement = point["unit"]
        has_range = point["min"] is not None and point["max"] is not None
        self._attr_mode = NumberMode.AUTO if has_range else NumberMode.BOX

    @property
    def native_value(self) -> float | None:
        return self._value()

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write(value)
