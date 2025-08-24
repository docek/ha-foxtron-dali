"""The Foxtron DALI integration."""

import asyncio
import contextlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    storage,
)

from .const import DOMAIN
from .driver import FoxtronDaliDriver

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT, Platform.EVENT]

STORE_VERSION = 1
DEFAULT_NAMES_FILE = "foxtron_dali_names.json"


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

    # Start connection in background so light services register quickly
    connect_task = hass.async_create_task(driver.connect())
    driver.connect_task = connect_task

    async def _post_connect() -> None:
        await connect_task
        await driver.set_fade_time(fade_time)

    hass.async_create_task(_post_connect())

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
            _LOGGER.info(
                f"Executing set_fade_time({fade_time}) for all configured DALI buses"
            )
            for driver in hass.data[DOMAIN].values():
                await driver.set_fade_time(fade_time)

        async def handle_scan_for_lights(call: ServiceCall) -> None:
            """Handle the scan_for_lights service call for all buses."""
            _LOGGER.info("Executing scan_for_lights for all configured DALI buses")
            for driver in hass.data[DOMAIN].values():
                await driver.scan_for_devices()

        def _extract_address(entry) -> str | None:
            """Extract DALI address from an entity registry entry."""
            parts = entry.unique_id.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                return parts[1]
            return None

        async def handle_export_names(call: ServiceCall) -> None:
            """Export DALI address to name mapping to a JSON file."""
            path = call.data.get("path", DEFAULT_NAMES_FILE)
            ent_reg = er.async_get(hass)
            area_reg = ar.async_get(hass)
            dev_reg = dr.async_get(hass)
            data: dict[str, dict] = {}
            for entry in ent_reg.entities.values():
                if entry.platform != DOMAIN:
                    continue
                address = _extract_address(entry)
                if address is None:
                    continue
                area_name = None
                if entry.area_id:
                    area = area_reg.async_get_area(entry.area_id)
                    if area:
                        area_name = area.name
                device_id = entry.device_id
                device_name = None
                if device_id:
                    device = dev_reg.async_get(device_id)
                    if device:
                        device_name = device.name_by_user or device.name
                data[address] = {
                    "entity_id": entry.entity_id,
                    "unique_id": entry.unique_id,
                    "name": entry.name or entry.original_name,
                    "area": area_name,
                    "device_id": device_id,
                    "device_name": device_name,
                }

            store = storage.Store(hass, STORE_VERSION, path)
            await store.async_save(data)

        async def handle_import_names(call: ServiceCall) -> None:
            """Import DALI address to name mapping from a JSON file."""
            path = call.data.get("path", DEFAULT_NAMES_FILE)
            store = storage.Store(hass, STORE_VERSION, path)
            data = await store.async_load() or {}
            if not isinstance(data, dict):
                _LOGGER.warning("No data loaded from %s", path)
                return
            ent_reg = er.async_get(hass)
            area_reg = ar.async_get(hass)
            dev_reg = dr.async_get(hass)

            for item in data.values():
                entity_id = item.get("entity_id")
                entry_obj = ent_reg.async_get(entity_id) if entity_id else None
                if entry_obj is None:
                    unique_id = item.get("unique_id")
                    for entry in ent_reg.entities.values():
                        if entry.platform == DOMAIN and entry.unique_id == unique_id:
                            entity_id = entry.entity_id
                            entry_obj = entry
                            break
                if entry_obj is None:
                    continue
                updates: dict = {}
                if name := item.get("name"):
                    updates["name"] = name
                if area_name := item.get("area"):
                    area = area_reg.async_get_area_by_name(area_name)
                    if area:
                        updates["area_id"] = area.id
                if updates:
                    ent_reg.async_update_entity(entity_id, **updates)
                device_id = item.get("device_id")
                device_name = item.get("device_name")
                if device_id and device_name:
                    dev_reg.async_update_device(device_id, name=device_name)

        hass.services.async_register(DOMAIN, "broadcast_on", handle_broadcast_on)
        hass.services.async_register(DOMAIN, "broadcast_off", handle_broadcast_off)
        hass.services.async_register(DOMAIN, "set_fade_time", handle_set_fade_time)
        hass.services.async_register(DOMAIN, "scan_for_lights", handle_scan_for_lights)
        hass.services.async_register(DOMAIN, "export_names", handle_export_names)
        hass.services.async_register(DOMAIN, "import_names", handle_import_names)

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
        connect_task = getattr(driver, "connect_task", None)
        if connect_task:
            connect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await connect_task
        await driver.disconnect()

        # If this was the last configured entry, clean up the global services
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
            for service in (
                "broadcast_on",
                "broadcast_off",
                "set_fade_time",
                "scan_for_lights",
            ):
                hass.services.async_remove(DOMAIN, service)

    return unload_ok
