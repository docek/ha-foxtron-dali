from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

if not hasattr(config_entries, "OptionsFlowWithReload"):

    class OptionsFlowWithReload(config_entries.OptionsFlow):
        """Fallback OptionsFlowWithReload for older Home Assistant."""

        pass

    config_entries.OptionsFlowWithReload = OptionsFlowWithReload

sys.path.append(str(Path(__file__).resolve().parents[1]))

from custom_components.foxtron_dali.const import DOMAIN
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
async def test_discover_buttons_merges_options(hass):
    """Test discovered buttons are merged into options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "1.2.3.4", CONF_PORT: 23},
        options={"buttons": ["1-1"]},
    )
    entry.add_to_hass(hass)

    driver = MagicMock()
    driver.get_newly_discovered_buttons.return_value = ["2-2"]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = driver

    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    form = await flow.async_step_discover_buttons()
    assert form["type"] == FlowResultType.FORM

    result = await flow.async_step_discover_buttons(user_input={"buttons": ["2-2"]})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["buttons"] == ["1-1", "2-2"]
    driver.add_known_button.assert_called_once_with("2-2")
    driver.clear_newly_discovered_buttons.assert_called_once()
