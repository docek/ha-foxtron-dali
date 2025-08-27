from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType

if not hasattr(config_entries, "OptionsFlowWithReload"):

    class OptionsFlowWithReload(config_entries.OptionsFlow):
        """Fallback OptionsFlowWithReload for older Home Assistant."""

        pass

    config_entries.OptionsFlowWithReload = OptionsFlowWithReload

sys.path.append(str(Path(__file__).resolve().parents[1]))

from custom_components.foxtron_dali import config_flow


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


@pytest.mark.asyncio
async def test_options_update_applies_globally():
    """Setting options for one entry updates all entries."""
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_reload = AsyncMock()

    entry1 = MagicMock(entry_id="1", options={})
    entry2 = MagicMock(entry_id="2", options={})
    hass.config_entries.async_entries.return_value = [entry1, entry2]

    options_flow = config_flow.FoxtronDaliOptionsFlowHandler(entry1)
    options_flow.hass = hass

    result = await options_flow.async_step_set_fade_time({"fade_time": 5})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    hass.config_entries.async_update_entry.assert_any_call(
        entry1, options={"fade_time": 5}
    )
    hass.config_entries.async_update_entry.assert_any_call(
        entry2, options={"fade_time": 5}
    )
    hass.config_entries.async_reload.assert_has_awaits(
        [call("1"), call("2")], any_order=True
    )
