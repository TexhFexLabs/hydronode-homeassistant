"""Shared fixtures for the HydroNode integration test suite."""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/hydronode loadable by Home Assistant in tests."""
    yield
