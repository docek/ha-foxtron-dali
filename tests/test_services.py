import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.foxtron_dali.const import DOMAIN
import custom_components.foxtron_dali as foxtron_dali


@pytest.mark.asyncio
async def test_export_import_round_trip(hass, tmp_path, enable_custom_integrations):
    """Verify that names can be exported and imported."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "1.2.3.4", CONF_PORT: 23}, options={}
    )
    entry.add_to_hass(hass)
    hass.config.config_dir = str(tmp_path)

    with patch("custom_components.foxtron_dali.FoxtronDaliDriver") as mock_driver_cls:
        driver = AsyncMock()
        mock_driver_cls.return_value = driver
        await foxtron_dali.async_setup_entry(hass, entry)
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)

    area = area_reg.async_get_or_create("Old Area")
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "dev1")},
        name="Orig Device",
    )
    unique_id = "1.2.3.4_23_1"
    entity = ent_reg.async_get_or_create(
        "light",
        DOMAIN,
        unique_id,
        suggested_object_id="dali_light_1",
        config_entry=entry,
        device_id=device.id,
    )
    ent_reg.async_update_entity(
        entity.entity_id,
        name="Orig Light",
        area_id=area.id,
        hidden_by=er.RegistryEntryHider.USER,
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    class FakeStore:
        def __init__(self, hass, version, key):
            self.path = Path(hass.config.config_dir) / key

        async def async_save(self, data):
            self.path.write_text(json.dumps(data))

        async def async_load(self):
            if self.path.exists():
                return json.loads(self.path.read_text())
            return None

    file_path = "names.json"
    with patch("custom_components.foxtron_dali.storage.Store", FakeStore):
        await hass.services.async_call(
            DOMAIN, "export_names", {"path": file_path}, blocking=True
        )
        data = json.loads((Path(hass.config.config_dir) / file_path).read_text())
    assert data == {
        "1": {
            "entity_id": entity.entity_id,
            "unique_id": unique_id,
            "name": "Orig Light",
            "area": "Old Area",
            "device_id": device.id,
            "device_name": "Orig Device",
            "hidden_by": "user",
            "disabled_by": "user",
        }
    }

    ent_reg.async_remove(entity.entity_id)
    entry2 = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "1.2.3.4", CONF_PORT: 23}, options={}
    )
    entry2.add_to_hass(hass)
    device2 = dev_reg.async_get_or_create(
        config_entry_id=entry2.entry_id,
        identifiers={(DOMAIN, "dev2")},
        name="New Device",
    )
    new_entity = ent_reg.async_get_or_create(
        "light",
        DOMAIN,
        unique_id,
        suggested_object_id="new_light",
        config_entry=entry2,
        device_id=device2.id,
    )
    ent_reg.async_update_entity(
        new_entity.entity_id,
        name="Changed",
        area_id=None,
        hidden_by=None,
        disabled_by=None,
    )

    with patch("custom_components.foxtron_dali.storage.Store", FakeStore):
        await hass.services.async_call(
            DOMAIN, "import_names", {"path": file_path}, blocking=True
        )

    restored = ent_reg.async_get(new_entity.entity_id)
    assert restored.name == "Orig Light"
    assert restored.area_id == area.id
    assert restored.hidden_by == er.RegistryEntryHider.USER
    assert restored.disabled_by == er.RegistryEntryDisabler.USER
