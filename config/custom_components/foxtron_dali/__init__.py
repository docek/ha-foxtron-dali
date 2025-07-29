"""The Foxtron DALI integration."""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .driver import FoxtronDaliDriver

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT, Platform.EVENT]


asyn_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Foxtron DALI from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    driver = FoxtronDaliDriver(host, port)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = driver

    async def connect_and_listen():
        await driver.connect()
        # The driver will automatically reconnect if the connection is lost.
        # We just need to keep the task running.
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour

    # Start the driver connection in the background
    entry.async_create_background_task(
        hass, connect_and_listen(), "dali-driver-connect"
    )

    # Set up the platforms
    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
            ]
        )
    )

    if unload_ok:
        driver: FoxtronDaliDriver = hass.data[DOMAIN].pop(entry.entry_id)
        await driver.disconnect()

    return unload_ok