import sys
from pathlib import Path
import json

sys.path.append(str(Path(__file__).resolve().parents[1]))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

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
    """Test successful JSON upload handling."""
    json_path = tmp_path / "lights.json"
    json_path.write_text(
        json.dumps({"1": {"name": "New Light", "area": "Room", "unique_id": "uid1"}})
    )

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "1.2.3.4", CONF_PORT: 23}, options={}
    )
    entry.add_to_hass(hass)

    area_reg = ar.async_get(hass)
    room = area_reg.async_get_or_create("Room")
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    entity = entity_reg.async_get_or_create(
        "light",
        DOMAIN,
        "uid1",
        suggested_object_id="dali_light_1",
        device_id=device.id,
    )

    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_upload_config(
        user_input={"file_path": str(json_path)}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["light_config"] == {
        1: {"name": "New Light", "area": "Room", "unique_id": "uid1"}
    }
    entity_entry = entity_reg.async_get(entity.entity_id)
    assert entity_entry.name == "New Light"
    assert entity_entry.area_id == room.id


@pytest.mark.asyncio
async def test_upload_config_updates_existing_unique_id(hass, tmp_path):
    """Ensure upload renames existing entities instead of creating new ones."""
    json_path = tmp_path / "lights.json"
    json_path.write_text(
        json.dumps({"1": {"name": "New Light", "area": "Room", "unique_id": "uid1"}})
    )

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "1.2.3.4", CONF_PORT: 23}, options={}
    )
    entry.add_to_hass(hass)

    area_reg = ar.async_get(hass)
    room = area_reg.async_get_or_create("Room")
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    # Create entity with default unique ID
    default_uid = f"{entry.data[CONF_HOST]}_{entry.data[CONF_PORT]}_1"
    entity_reg.async_get_or_create(
        "light",
        DOMAIN,
        default_uid,
        suggested_object_id="dali_light_1",
        device_id=device.id,
    )

    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_upload_config(
        user_input={"file_path": str(json_path)}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity_id = entity_reg.async_get_entity_id("light", DOMAIN, "uid1")
    assert entity_id is not None
    entry_after = entity_reg.async_get(entity_id)
    assert entry_after.name == "New Light"
    assert entry_after.area_id == room.id
    assert entity_reg.async_get_entity_id("light", DOMAIN, default_uid) is None
    # Only one entity should remain for this integration
    assert sum(1 for e in entity_reg.entities.values() if e.platform == DOMAIN) == 1


@pytest.mark.asyncio
async def test_upload_config_mismatch_notification(hass, tmp_path):
    """Notify when backup differs from discovered lights."""
    json_path = tmp_path / "lights.json"
    json_path.write_text(
        json.dumps({"1": {"name": "Light", "area": "Room", "unique_id": "uid1"}})
    )

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "1.2.3.4", CONF_PORT: 23}, options={}
    )
    entry.add_to_hass(hass)

    area_reg = ar.async_get(hass)
    area_reg.async_get_or_create("Room")
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    default_uid = f"{entry.data[CONF_HOST]}_{entry.data[CONF_PORT]}_1"
    entity_reg.async_get_or_create(
        "light",
        DOMAIN,
        default_uid,
        suggested_object_id="dali_light_1",
        device_id=device.id,
    )

    driver = AsyncMock()
    driver.scan_for_devices.return_value = [1, 2]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = driver

    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    with patch(
        "homeassistant.components.persistent_notification.async_create",
    ) as mock_notify:
        await flow.async_step_upload_config(user_input={"file_path": str(json_path)})
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][1]
        assert "New lights" in msg


@pytest.mark.asyncio
async def test_upload_config_invalid_json(hass, tmp_path):
    """Test JSON upload with invalid content."""
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("not a json")

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "1.2.3.4", CONF_PORT: 23}, options={}
    )
    entry.add_to_hass(hass)
    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_upload_config(
        user_input={"file_path": str(bad_path)}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_json"


@pytest.mark.asyncio
async def test_upload_config_file_not_found(hass, tmp_path):
    """Test JSON upload with missing file."""
    missing = tmp_path / "missing.json"

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "1.2.3.4", CONF_PORT: 23}, options={}
    )
    entry.add_to_hass(hass)
    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_upload_config(user_input={"file_path": str(missing)})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "file_not_found"


@pytest.mark.asyncio
async def test_backup_config_success(hass, tmp_path):
    """Test successful backup of light configuration."""
    backup_path = tmp_path / "backup.json"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "1.2.3.4", CONF_PORT: 23},
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
    data = json.loads(backup_path.read_text())
    assert data == {"1": {"name": "Light", "area": "Room", "unique_id": "uid1"}}


@pytest.mark.asyncio
async def test_backup_config_uses_entity_area(hass, tmp_path):
    """Export uses entity name and entity area."""
    backup_path = tmp_path / "backup.json"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "1.2.3.4", CONF_PORT: 23},
        options={"light_config": {1: {"unique_id": "uid1"}}},
    )
    entry.add_to_hass(hass)

    area_reg = ar.async_get(hass)
    room = area_reg.async_get_or_create("Room")
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )
    entity = entity_reg.async_get_or_create(
        "light",
        DOMAIN,
        "uid1",
        suggested_object_id="dali_light_1",
        device_id=device.id,
    )
    entity_reg.async_update_entity(entity.entity_id, name="Friendly", area_id=room.id)

    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_backup_config(
        user_input={"file_path": str(backup_path)}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = json.loads(backup_path.read_text())
    assert data == {"1": {"name": "Friendly", "area": "Room", "unique_id": "uid1"}}


@pytest.mark.asyncio
async def test_backup_config_discovers_devices(hass, tmp_path):
    """Backup uses discovered devices when no config is present."""
    backup_path = tmp_path / "backup.json"
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "1.2.3.4", CONF_PORT: 23}, options={}
    )
    entry.add_to_hass(hass)

    driver = AsyncMock()
    driver.scan_for_devices.return_value = [1, 2]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = driver

    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_backup_config(
        user_input={"file_path": str(backup_path)}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = json.loads(backup_path.read_text())
    assert data == {
        "1": {
            "name": "DALI Light 1",
            "area": "",
            "unique_id": f"{entry.data[CONF_HOST]}_{entry.data[CONF_PORT]}_1",
        },
        "2": {
            "name": "DALI Light 2",
            "area": "",
            "unique_id": f"{entry.data[CONF_HOST]}_{entry.data[CONF_PORT]}_2",
        },
    }


@pytest.mark.asyncio
async def test_backup_config_no_config(hass, tmp_path):
    """Backing up with no devices or config returns an error."""
    backup_path = tmp_path / "backup.json"
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "1.2.3.4", CONF_PORT: 23}, options={}
    )
    entry.add_to_hass(hass)

    driver = AsyncMock()
    driver.scan_for_devices.return_value = []
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = driver

    flow = config_flow.FoxtronDaliOptionsFlowHandler(entry)
    flow.hass = hass

    result = await flow.async_step_backup_config(
        user_input={"file_path": str(backup_path)}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "no_config"


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
