"""Shared helpers for the per-bus entity platforms."""

import asyncio
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


class ConnectionAwareEntity(Entity):
    """Availability follows the driver connection; state reads run in
    tracked background tasks that die with the entity.

    Subclasses set ``_driver`` before ``async_added_to_hass`` runs and
    implement ``async_update`` for the state read. Flags:

    - ``_refresh_on_add``: read state right after the entity is added
      (in the background — entity add must not await a bus round-trip).
    - ``_refresh_on_reconnect``: re-read state after a gateway reconnect.
    """

    _attr_should_poll = False
    _driver: FoxtronDaliDriver
    _refresh_on_add = True
    _refresh_on_reconnect = True
    _refresh_task: asyncio.Task | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._driver.add_disconnect_callback(self._handle_driver_disconnect)
        )
        self.async_on_remove(
            self._driver.add_connect_callback(self._handle_driver_connect)
        )
        if self._refresh_on_add:
            self._schedule_refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        await super().async_will_remove_from_hass()

    def _schedule_refresh(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = self.hass.async_create_task(self._async_refresh())

    async def _async_refresh(self) -> None:
        await self.async_update()
        self.async_write_ha_state()

    def _handle_driver_disconnect(self) -> None:
        self._attr_available = False
        self._on_driver_disconnect()
        self.async_write_ha_state()

    def _handle_driver_connect(self) -> None:
        self._attr_available = True
        self.async_write_ha_state()
        if self._refresh_on_reconnect:
            self._schedule_refresh()

    def _on_driver_disconnect(self) -> None:
        """Hook for subclass cleanup on disconnect (default: nothing)."""


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

    def _add_new(addresses: set[int]) -> None:
        new = sorted(addr for addr in addresses if addr not in known_addresses)
        known_addresses.update(new)
        if new:
            async_add_entities([entity_factory(driver, addr) for addr in new])

    # Registry-known addresses get entities immediately — they must not
    # wait for the slow bus sweep (up to ~13 s per bus); the scan only
    # discovers NEW gear.
    _add_new(registry_light_addresses(hass, entry))

    async def _scan_and_add(refresh: bool = False) -> None:
        # The connection is established by async_setup_entry before the
        # platforms are forwarded; the scan itself runs in the background
        # so it doesn't block startup. scan_for_devices caches, so the
        # second platform doesn't trigger another bus scan.
        _add_new(set(await driver.scan_for_devices(refresh=refresh)))

    scan_task = hass.async_create_task(_scan_and_add())
    # The scan must die with the entry, or a reload mid-scan lets it call
    # async_add_entities against a torn-down platform
    entry.async_on_unload(scan_task.cancel)

    async def _rescan() -> None:
        """Rescan on demand (scan_for_lights service)."""
        await _scan_and_add(refresh=True)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_RESCAN, _rescan))
