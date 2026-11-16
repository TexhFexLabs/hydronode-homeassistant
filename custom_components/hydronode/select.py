"""Select platform: writable choice (ENUM) data points (full-access token only)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydroNodeConfigEntry
from .data_points import HydroNodePointEntity, control_unique_id, setup_point_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HydroNodeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the selects and follow the bootstrap."""
    setup_point_platform(entry, "select", async_add_entities, HydroNodePointSelect)


class HydroNodePointSelect(HydroNodePointEntity, SelectEntity):
    """Picks an option by its label; HydroNode stores the option's value."""

    def __init__(self, coordinator, client, station, sensor, point) -> None:
        super().__init__(
            coordinator, client, station, sensor, point, control_unique_id(point["id"])
        )
        self._attr_options = [label for _, label in point["options"]]
        self._values = {label: value for value, label in point["options"]}

    @property
    def current_option(self) -> str | None:
        label = self._label()
        return label if label in self._values else None

    async def async_select_option(self, option: str) -> None:
        if option not in self._values:
            raise HomeAssistantError(f"Unknown option {option}")
        await self._async_write(self._values[option])
