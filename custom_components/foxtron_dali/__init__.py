"""The Foxtron DALI integration."""

import asyncio
import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONNECT_TIMEOUT_SECONDS,
    DOMAIN,
    SIGNAL_BROADCAST_STATE,
    SIGNAL_RESCAN,
)
from .driver import FoxtronDaliDriver

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT, Platform.EVENT, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Foxtron DALI from a config entry."""
    # If this is a new entry, copy options from an existing one
    if not entry.options:
        for existing in hass.config_entries.async_entries(DOMAIN):
            if existing.entry_id != entry.entry_id and existing.options:
                hass.config_entries.async_update_entry(entry, options=existing.options)
                break

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    fade_time = entry.options.get("fade_time", 0)

    driver = FoxtronDaliDriver(host, port)

    # Fail fast if the gateway is unreachable: Home Assistant retries the
    # setup with increasing intervals until the gateway comes online (e.g.
    # after a power outage where HA boots faster than the gateway).
    await driver.connect()
    if not await driver.wait_connected(CONNECT_TIMEOUT_SECONDS):
        await driver.disconnect()
        raise ConfigEntryNotReady(f"Cannot connect to Foxtron gateway at {host}:{port}")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = driver

    # Create a device for the DALI bus
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"DALI Bus ({host}:{port})",
        manufacturer="Foxtron",
    )

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
            # Own commands return as confirmations, not bus events, so
            # update the light entities optimistically.
            async_dispatcher_send(hass, SIGNAL_BROADCAST_STATE, True)

        async def handle_broadcast_off(call: ServiceCall) -> None:
            """Handle the broadcast_off service call for all buses."""
            _LOGGER.info("Executing broadcast_off for all configured DALI buses")
            for driver in hass.data[DOMAIN].values():
                await driver.broadcast_off()
            async_dispatcher_send(hass, SIGNAL_BROADCAST_STATE, False)

        async def handle_set_fade_time(call: ServiceCall) -> None:
            """Handle the set_fade_time service call for all buses."""
            fade_time = call.data.get("fade_time", 0)
            _LOGGER.info(
                f"Executing set_fade_time({fade_time}) for all configured DALI buses"
            )
            for driver in hass.data[DOMAIN].values():
                await driver.set_fade_time(fade_time)

        async def handle_scan_for_lights(call: ServiceCall) -> None:
            """Handle the scan_for_lights service call for all buses."""
            _LOGGER.info("Executing scan_for_lights for all configured DALI buses")
            # Each light platform rescans its bus (with a cache refresh)
            # and adds any newly discovered lights.
            async_dispatcher_send(hass, SIGNAL_RESCAN)

        async def handle_remove_paired_switch(call: ServiceCall) -> None:
            """Remove a paired DALI switch device created by this integration."""
            device_id = call.data["device_id"]
            device_registry = dr.async_get(hass)
            device = device_registry.async_get(device_id)

            if device is None:
                raise ServiceValidationError(
                    f"Device '{device_id}' was not found in the device registry."
                )

            is_paired_switch = any(
                domain == DOMAIN and identifier.startswith("dali4sw_")
                for domain, identifier in device.identifiers
            )
            if not is_paired_switch:
                raise ServiceValidationError(
                    f"Device '{device_id}' is not a paired Foxtron DALI switch device."
                )

            device_registry.async_remove_device(device_id)
            _LOGGER.info("Removed paired DALI switch device %s", device_id)

        hass.services.async_register(DOMAIN, "broadcast_on", handle_broadcast_on)
        hass.services.async_register(DOMAIN, "broadcast_off", handle_broadcast_off)
        hass.services.async_register(DOMAIN, "set_fade_time", handle_set_fade_time)
        hass.services.async_register(DOMAIN, "scan_for_lights", handle_scan_for_lights)
        hass.services.async_register(
            DOMAIN,
            "remove_paired_switch",
            handle_remove_paired_switch,
            schema=vol.Schema({vol.Required("device_id"): str}),
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

        # If this was the last configured entry, clean up the global services
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
            for service in (
                "broadcast_on",
                "broadcast_off",
                "set_fade_time",
                "scan_for_lights",
                "remove_paired_switch",
            ):
                hass.services.async_remove(DOMAIN, service)

    return unload_ok
