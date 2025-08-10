"""The Foxtron DALI integration."""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .driver import FoxtronDaliDriver

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT, Platform.EVENT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Foxtron DALI from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    # Get the list of known buttons from the config entry options
    known_buttons = entry.options.get("buttons", [])
    fade_time = entry.options.get("fade_time", 0)

    driver = FoxtronDaliDriver(host, port, known_buttons)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = driver

    # Create a device for the DALI bus
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"DALI Bus ({host}:{port})",
        manufacturer="Foxtron",
    )

    # Connect to the driver
    await driver.connect()

    # Set the fade time
    await driver.set_fade_time(fade_time)

    # Set up the platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    

    # --- Register Global Services ---
    # We only want to register the services once
    if not hass.services.has_service(DOMAIN, "broadcast_on"):

        async def handle_broadcast_on(call: ServiceCall) -> None:
            """Handle the broadcast_on service call for all buses."""
            _LOGGER.info("Executing broadcast_on for all configured DALI buses")
            for driver in hass.data[DOMAIN].values():
                await driver.broadcast_on()

        async def handle_broadcast_off(call: ServiceCall) -> None:
            """Handle the broadcast_off service call for all buses."""
            _LOGGER.info("Executing broadcast_off for all configured DALI buses")
            for driver in hass.data[DOMAIN].values():
                await driver.broadcast_off()

        async def handle_set_fade_time(call: ServiceCall) -> None:
            """Handle the set_fade_time service call for all buses."""
            fade_time = call.data.get("fade_time", 0)
            _LOGGER.info(f"Executing set_fade_time({fade_time}) for all configured DALI buses")
            for driver in hass.data[DOMAIN].values():
                await driver.set_fade_time(fade_time)

        hass.services.async_register(DOMAIN, "broadcast_on", handle_broadcast_on)
        hass.services.async_register(DOMAIN, "broadcast_off", handle_broadcast_off)
        hass.services.async_register(DOMAIN, "set_fade_time", handle_set_fade_time)

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



