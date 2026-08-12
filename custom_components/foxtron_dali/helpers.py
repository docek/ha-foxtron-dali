"""Shared helpers for per-light platforms (light, select)."""

from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_RESCAN
from .driver import FoxtronDaliDriver


def bus_id(entry: ConfigEntry) -> str:
    """Return the host_port identifier used in unique_ids."""
    return f"{entry.data[CONF_HOST]}_{entry.data[CONF_PORT]}"


def switch_identifier(
    bus_id_str: str, address: int, upper_instance: int, lower_instance: int
) -> str:
    """Unique identifier of a paired DALI switch device."""
    return f"dali4sw_{bus_id_str}_{address}_{upper_instance}_{lower_instance}"


def light_device_info(entry: ConfigEntry, address: int) -> DeviceInfo:
    """Device shared by a light and its config entities (e.g. fade time)."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{bus_id(entry)}_light_{address}")},
        name=f"DALI Light {address}",
        manufacturer="Foxtron",
        via_device=(DOMAIN, entry.entry_id),
    )


def registry_light_addresses(hass: HomeAssistant, entry: ConfigEntry) -> set[int]:
    """Return DALI addresses of lights already known to the entity registry."""
    registry = er.async_get(hass)
    prefix = f"{bus_id(entry)}_"
    addresses: set[int] = set()
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain != "light" or not reg_entry.unique_id.startswith(prefix):
            continue
        suffix = reg_entry.unique_id.removeprefix(prefix)
        if suffix.isdigit():
            addresses.add(int(suffix))
    return addresses


async def async_setup_scanned_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    entity_factory: Callable[[FoxtronDaliDriver, int], Entity],
) -> None:
    """Create one entity per known DALI address, now and on rescans.

    Addresses come from a bus scan plus the entity registry seed: a scan
    can occasionally miss a reply on a busy bus, so entity existence must
    not depend on scan luck — the scan only discovers NEW gear.
    """
    driver: FoxtronDaliDriver = hass.data[DOMAIN][entry.entry_id]

    known_addresses: set[int] = set()
    registry_addresses = registry_light_addresses(hass, entry)

    async def _scan_and_add(refresh: bool = False) -> None:
        # The connection is established by async_setup_entry before the
        # platforms are forwarded; the scan itself runs in the background
        # so it doesn't block startup. scan_for_devices caches, so the
        # second platform doesn't trigger another bus scan.
        addresses = set(await driver.scan_for_devices(refresh=refresh))
        addresses |= registry_addresses
        new = sorted(addr for addr in addresses if addr not in known_addresses)
        known_addresses.update(new)
        if new:
            async_add_entities([entity_factory(driver, addr) for addr in new])

    hass.async_create_task(_scan_and_add())

    async def _rescan() -> None:
        """Rescan on demand (scan_for_lights service)."""
        await _scan_and_add(refresh=True)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_RESCAN, _rescan))
