from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT

import custom_components.foxtron_dali as foxtron_dali


@pytest.mark.asyncio
async def test_setup_entry_copies_existing_options():
    """A new entry inherits options from existing entries."""
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.data = {}

    existing = MagicMock(entry_id="1", options={"fade_time": 4})
    existing.data = {CONF_HOST: "1.1.1.1", CONF_PORT: 23}
    existing.async_unload = AsyncMock(return_value=True)

    new = MagicMock(entry_id="2", options={})
    new.data = {CONF_HOST: "2.2.2.2", CONF_PORT: 23}
    new.async_unload = AsyncMock(return_value=True)

    hass.config_entries.async_entries.return_value = [existing, new]

    driver = AsyncMock()
    with (
        patch("custom_components.foxtron_dali.FoxtronDaliDriver", return_value=driver),
        patch("custom_components.foxtron_dali.dr.async_get") as mock_dr,
        patch.object(hass.services, "has_service", return_value=False),
        patch.object(hass.services, "async_register"),
    ):
        device_registry = MagicMock()
        mock_dr.return_value = device_registry
        device_registry.async_get_or_create = MagicMock()

        assert await foxtron_dali.async_setup_entry(hass, new)

    hass.config_entries.async_update_entry.assert_called_once_with(
        new, options={"fade_time": 4}
    )
