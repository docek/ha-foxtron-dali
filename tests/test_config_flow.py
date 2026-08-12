from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType

sys.path.append(str(Path(__file__).resolve().parents[1]))

from custom_components.foxtron_dali import config_flow
from custom_components.foxtron_dali.const import DISCOVERY_DURATION_SECONDS


@pytest.mark.asyncio
async def test_user_step_success(hass):
    """Test user step succeeds with valid connection."""
    flow = config_flow.FoxtronDaliConfigFlow()
    flow.context = {}
    flow.hass = hass
    with patch(
        "custom_components.foxtron_dali.config_flow.FoxtronDaliDriver"
    ) as mock_driver_cls:
        driver = AsyncMock()
        mock_driver_cls.return_value = driver
        driver.query_firmware_version.return_value = "1.0"

        result = await flow.async_step_user({CONF_HOST: "1.2.3.4", CONF_PORT: 23})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"] == {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
        driver.connect.assert_awaited_once()
        driver.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_step_cannot_connect(hass):
    """Test user step handles connection errors."""
    flow = config_flow.FoxtronDaliConfigFlow()
    flow.context = {}
    flow.hass = hass
    with patch(
        "custom_components.foxtron_dali.config_flow.FoxtronDaliDriver"
    ) as mock_driver_cls:
        driver = AsyncMock()
        mock_driver_cls.return_value = driver
        driver.connect.side_effect = ConnectionError

        result = await flow.async_step_user({CONF_HOST: "bad", CONF_PORT: 23})

        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_options_flow_init():
    """Ensure options flow initializes without error."""
    entry = MagicMock()
    entry.options = {}
    entry.add_update_listener = MagicMock(return_value=MagicMock())

    options_flow = config_flow.FoxtronDaliConfigFlow.async_get_options_flow(entry)

    assert isinstance(options_flow, config_flow.FoxtronDaliOptionsFlowHandler)

    result = await options_flow.async_step_init()
    assert result["type"] == FlowResultType.MENU
    # Global fade time was removed: fade time is per-light (select entity)
    assert "set_fade_time" not in result["menu_options"]


def test_set_fade_time_step_removed():
    """The global fade time options step no longer exists."""
    assert not hasattr(
        config_flow.FoxtronDaliOptionsFlowHandler, "async_step_set_fade_time"
    )


@pytest.mark.asyncio
async def test_options_update_applies_globally():
    """Setting options for one entry updates all entries."""
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_reload = AsyncMock()

    entry1 = MagicMock(entry_id="1", options={})
    entry2 = MagicMock(entry_id="2", options={})
    hass.config_entries.async_entries.return_value = [entry1, entry2]
    hass.config_entries.async_get_known_entry = MagicMock(return_value=entry1)

    options_flow = config_flow.FoxtronDaliOptionsFlowHandler()
    options_flow.hass = hass
    options_flow.handler = "1"  # entry_id; config_entry resolves through hass

    timing = {
        "long_press_threshold": 0.25,
        "long_press_repeat": 0.2,
        "multi_press_window": 0.3,
    }
    result = await options_flow.async_step_set_event_timing(dict(timing))
    assert result["type"] == FlowResultType.CREATE_ENTRY
    hass.config_entries.async_update_entry.assert_any_call(entry1, options=timing)
    hass.config_entries.async_update_entry.assert_any_call(entry2, options=timing)
    hass.config_entries.async_reload.assert_has_awaits(
        [call("1"), call("2")], any_order=True
    )


@pytest.mark.asyncio
async def test_start_discovery_uses_fixed_duration():
    """Starting discovery fires a fixed 5-minute pairing event."""
    hass = MagicMock()
    hass.bus.async_fire = MagicMock()

    options_flow = config_flow.FoxtronDaliOptionsFlowHandler()
    options_flow.hass = hass

    result = await options_flow.async_step_start_discovery()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "discovery_started"
    hass.bus.async_fire.assert_called_once_with(
        "foxtron_dali_start_discovery",
        {"duration": DISCOVERY_DURATION_SECONDS},
    )


@pytest.mark.asyncio
async def test_user_step_firmware_query_failure_is_cannot_connect(hass):
    """A gateway that connects but doesn't answer the firmware query is
    reported as cannot_connect, not as a created entry."""
    flow = config_flow.FoxtronDaliConfigFlow()
    flow.context = {}
    flow.hass = hass
    with patch(
        "custom_components.foxtron_dali.config_flow.FoxtronDaliDriver"
    ) as mock_driver_cls:
        driver = AsyncMock()
        mock_driver_cls.return_value = driver
        driver.wait_connected.return_value = True
        driver.query_firmware_version.return_value = None

        result = await flow.async_step_user({CONF_HOST: "1.2.3.4", CONF_PORT: 23})

        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == "cannot_connect"
        driver.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_step_connect_timeout_is_cannot_connect(hass):
    """wait_connected timing out must not create an entry."""
    flow = config_flow.FoxtronDaliConfigFlow()
    flow.context = {}
    flow.hass = hass
    with patch(
        "custom_components.foxtron_dali.config_flow.FoxtronDaliDriver"
    ) as mock_driver_cls:
        driver = AsyncMock()
        mock_driver_cls.return_value = driver
        driver.wait_connected.return_value = False

        result = await flow.async_step_user({CONF_HOST: "1.2.3.4", CONF_PORT: 23})

        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == "cannot_connect"
        driver.disconnect.assert_awaited_once()
