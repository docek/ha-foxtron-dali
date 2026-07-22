"""Diagnostics support for the Foxtron DALI integration."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .binary_sensor import CONFIG_ITEM_BUS_POWER
from .const import DOMAIN
from .driver import FoxtronDaliDriver

TO_REDACT = [CONF_HOST]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (one DALI bus)."""
    driver: FoxtronDaliDriver = hass.data[DOMAIN][entry.entry_id]

    firmware_version = None
    bus_power_status = None
    if driver.is_connected:
        firmware_version = await driver.query_firmware_version()
        bus_power_status = await driver.query_config_item(
            CONFIG_ITEM_BUS_POWER, timeout=3
        )

    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "driver": driver.diagnostics_snapshot(),
            "firmware_version": firmware_version,
            "bus_power_status": bus_power_status,
        },
        TO_REDACT,
    )
