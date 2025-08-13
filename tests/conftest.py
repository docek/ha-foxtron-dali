"""Pytest configuration to enforce Europe/Prague timezone."""

from __future__ import annotations

from zoneinfo import ZoneInfo
import pytest
from homeassistant.util import dt as dt_util

PRAGUE_TZ = ZoneInfo("Europe/Prague")


@pytest.fixture(autouse=True)
def set_prague_timezone(request, monkeypatch):
    """Set Home Assistant to use Europe/Prague timezone in tests."""
    original_get_time_zone = dt_util.get_time_zone

    def _get_time_zone(name: str):
        if name == "US/Pacific":
            return PRAGUE_TZ
        return original_get_time_zone(name)

    monkeypatch.setattr(dt_util, "get_time_zone", _get_time_zone, raising=False)
    dt_util.set_default_time_zone(PRAGUE_TZ)
    if "hass" in request.fixturenames:
        hass = request.getfixturevalue("hass")
        hass.config.set_time_zone("Europe/Prague")
