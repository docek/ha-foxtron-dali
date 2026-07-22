"""Tests for the DALI bus diagnostic binary sensors."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT

sys.path.append(str(Path(__file__).resolve().parents[1]))

import custom_components.foxtron_dali.binary_sensor as bs_module
from custom_components.foxtron_dali.binary_sensor import (
    DaliBusConnectedSensor,
    DaliBusPowerSensor,
)
from custom_components.foxtron_dali.driver import (
    GW_EVENT_POWER_LOSS,
    GW_EVENT_POWER_OK,
    SpecialGatewayEvent,
)


def _entry():
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
    return entry


def test_connected_sensor_reflects_driver_state():
    """The connectivity sensor mirrors driver.is_connected."""
    driver = MagicMock()
    driver.is_connected = True
    sensor = DaliBusConnectedSensor(_entry(), driver)
    assert sensor.is_on is True

    driver.is_connected = False
    assert sensor.is_on is False


@pytest.mark.asyncio
async def test_power_sensor_tracks_gateway_events(monkeypatch):
    """Power events flip the sensor and manage the persistent notification."""
    notifications = MagicMock()
    monkeypatch.setattr(bs_module, "persistent_notification", notifications)

    sensor = DaliBusPowerSensor(_entry(), MagicMock())
    sensor.hass = MagicMock()
    sensor.async_write_ha_state = MagicMock()

    assert sensor.is_on is None  # unknown until the first report

    sensor._handle_event(SpecialGatewayEvent(b"", GW_EVENT_POWER_LOSS))
    assert sensor.is_on is False
    assert sensor.extra_state_attributes["status"] == "DALI Power Loss"
    notifications.async_create.assert_called_once()

    sensor._handle_event(SpecialGatewayEvent(b"", GW_EVENT_POWER_OK))
    assert sensor.is_on is True
    notifications.async_dismiss.assert_called()

    # Non-power gateway events (e.g. checksum error) are ignored
    sensor._handle_event(SpecialGatewayEvent(b"", 5))
    assert sensor.is_on is True


@pytest.mark.asyncio
async def test_power_sensor_reads_initial_status(monkeypatch):
    """The initial power state is read from gateway config item 3."""
    monkeypatch.setattr(bs_module, "persistent_notification", MagicMock())
    driver = MagicMock()
    driver.query_config_item = AsyncMock(return_value=GW_EVENT_POWER_OK)

    sensor = DaliBusPowerSensor(_entry(), driver)
    sensor.hass = MagicMock()
    sensor.async_write_ha_state = MagicMock()

    await sensor._async_refresh_status()
    assert sensor.is_on is True
    driver.query_config_item.assert_awaited_once_with(3, timeout=3)


@pytest.mark.asyncio
async def test_power_sensor_availability_follows_connection(monkeypatch):
    """The power sensor is unavailable while the gateway is unreachable."""
    monkeypatch.setattr(bs_module, "persistent_notification", MagicMock())
    driver = MagicMock()
    driver.query_config_item = AsyncMock(return_value=None)

    sensor = DaliBusPowerSensor(_entry(), driver)
    sensor.hass = MagicMock()
    sensor.hass.async_create_task = MagicMock()
    sensor.async_write_ha_state = MagicMock()

    sensor._handle_driver_disconnect()
    assert sensor.available is False

    sensor._handle_driver_connect()
    assert sensor.available is True
    sensor.hass.async_create_task.assert_called()  # re-reads the status
