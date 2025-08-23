import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

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
async def test_upload_config_success(hass, tmp_path):
    """Test successful CSV upload handling."""
    csv_path = tmp_path / "lights.csv"
    csv_path.write_text("dali_address,name,area,unique_id\n1,Light,Room,uid1\n")

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)

    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_upload_config(
        user_input={"file_path": str(csv_path)}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["light_config"] == {
        1: {"name": "Light", "area": "Room", "unique_id": "uid1"}
    }


@pytest.mark.asyncio
async def test_upload_config_bad_header(hass, tmp_path):
    """Test CSV upload with invalid header."""
    bad_path = tmp_path / "bad.csv"
    bad_path.write_text("wrong,header\n")

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_upload_config(
        user_input={"file_path": str(bad_path)}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_csv_header"


@pytest.mark.asyncio
async def test_upload_config_file_not_found(hass, tmp_path):
    """Test CSV upload with missing file."""
    missing = tmp_path / "missing.csv"

    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_upload_config(user_input={"file_path": str(missing)})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "file_not_found"


@pytest.mark.asyncio
async def test_backup_config_success(hass, tmp_path):
    """Test successful backup of light configuration."""
    backup_path = tmp_path / "backup.csv"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            "light_config": {1: {"name": "Light", "area": "Room", "unique_id": "uid1"}}
        },
    )
    entry.add_to_hass(hass)
    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_backup_config(
        user_input={"file_path": str(backup_path)}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    lines = backup_path.read_text().splitlines()
    assert lines == [
        "dali_address,name,area,unique_id",
        "1,Light,Room,uid1",
    ]


@pytest.mark.asyncio
async def test_discover_buttons_merges_options(hass):
    """Test discovered buttons are merged into options."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={"buttons": ["1-1"]})
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
