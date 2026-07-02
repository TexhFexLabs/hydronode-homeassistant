"""Diagnostics support for HydroNode."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant

from . import HydroNodeConfigEntry

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HydroNodeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with the PAT redacted."""
    runtime_data = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "bootstrap": runtime_data.coordinator.bootstrap if runtime_data else None,
        "states_count": len(runtime_data.coordinator.data or {}) if runtime_data else 0,
    }
