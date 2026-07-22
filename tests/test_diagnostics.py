"""Tests for the diagnostics download."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT

sys.path.append(str(Path(__file__).resolve().parents[1]))

from custom_components.foxtron_dali.const import DOMAIN
from custom_components.foxtron_dali.diagnostics import (
    async_get_config_entry_diagnostics,
)


def _setup(connected: bool):
    hass = MagicMock()
    driver = MagicMock()
    driver.is_connected = connected
    driver.diagnostics_snapshot.return_value = {"is_connected": connected}
    driver.query_firmware_version = AsyncMock(return_value="4.6")
    driver.query_config_item = AsyncMock(return_value=0)

    entry = MagicMock()
    entry.entry_id = "e1"
    entry.title = "DALI Bus (1.2.3.4:23)"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
    entry.options = {"fade_time": 4}

    hass.data = {DOMAIN: {"e1": driver}}
    return hass, entry, driver


@pytest.mark.asyncio
async def test_diagnostics_redacts_host_and_reports_state():
    """Diagnostics contain driver state with the gateway IP redacted."""
    hass, entry, _ = _setup(connected=True)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["data"][CONF_HOST] == "**REDACTED**"
    assert result["entry"]["data"][CONF_PORT] == 23
    assert result["entry"]["options"] == {"fade_time": 4}
    assert result["driver"] == {"is_connected": True}
    assert result["firmware_version"] == "4.6"
    assert result["bus_power_status"] == 0


@pytest.mark.asyncio
async def test_diagnostics_skips_queries_when_disconnected():
    """No gateway queries are attempted while disconnected."""
    hass, entry, driver = _setup(connected=False)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["firmware_version"] is None
    assert result["bus_power_status"] is None
    driver.query_firmware_version.assert_not_awaited()
