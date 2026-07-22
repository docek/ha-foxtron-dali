import logging
from typing import Any, Optional, Callable

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .driver import (
    FoxtronDaliDriver,
    DaliCommandEvent,
    DALI_BROADCAST,
    DALI_BROADCAST_DAPC,
    DALI_CMD_OFF,
    DALI_CMD_RECALL_MAX_LEVEL,
    DALI_MASK,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DALI lights from a config entry."""
    driver: FoxtronDaliDriver = hass.data[DOMAIN][entry.entry_id]

    async def _scan_and_add() -> None:
        """Scan the bus and add discovered lights."""
        # The connection is established by async_setup_entry before the
        # platforms are forwarded; the scan itself runs in the background
        # so it doesn't block startup.
        addresses = await driver.scan_for_devices()
        lights = [DaliLight(driver, addr, entry) for addr in addresses]
        async_add_entities(lights)

    hass.async_create_task(_scan_and_add())


class DaliLight(LightEntity):
    """Representation of a DALI light."""

    _attr_should_poll = False

    def __init__(
        self,
        driver: FoxtronDaliDriver,
        address: int,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the light."""
        self._driver = driver
        self._address = address
        self._entry = entry
        self._attr_color_mode = ColorMode.BRIGHTNESS
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._brightness: Optional[int] = None
        self._is_on = False
        self._unsub: Callable[[], None] | None = None

    @property
    def name(self) -> str:
        """Return the name of the light."""
        return f"DALI Light {self._address}"

    @property
    def unique_id(self) -> str:
        """Return a unique ID for the light."""
        return f"{self._entry.data[CONF_HOST]}_{self._entry.data[CONF_PORT]}_{self._address}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Foxtron",
        )

    @property
    def is_on(self) -> bool:
        """Return true if the light is on."""
        return self._is_on

    @property
    def brightness(self) -> Optional[int]:
        """Return the brightness of the light."""
        return self._brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
        # Scale HA brightness (0-255) to DALI level (0-254)
        dali_level = round(brightness * 254 / 255)

        await self._driver.set_device_level(self._address, dali_level)
        self._is_on = True
        self._brightness = brightness
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._driver.set_device_level(self._address, 0)
        self._is_on = False
        self._brightness = 0
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for bus events when added to Home Assistant."""
        await super().async_added_to_hass()
        self._unsub = self._driver.add_event_listener(self._handle_event)
        self.async_on_remove(
            self._driver.add_disconnect_callback(self._handle_driver_disconnect)
        )
        self.async_on_remove(
            self._driver.add_connect_callback(self._handle_driver_connect)
        )
        await self.async_update()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup when entity is removed from Home Assistant."""
        if self._unsub:
            self._unsub()
        await super().async_will_remove_from_hass()

    def _handle_driver_disconnect(self) -> None:
        """Mark the light unavailable while the gateway is disconnected."""
        self._attr_available = False
        self.async_write_ha_state()

    def _handle_driver_connect(self) -> None:
        """Restore availability and refresh state after a reconnect."""
        self._attr_available = True
        self.async_write_ha_state()
        # The light may have changed while the gateway was away
        self.hass.async_create_task(self._async_refresh_state())

    async def _async_refresh_state(self) -> None:
        """Re-query the actual level and publish the fresh state."""
        await self.async_update()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Fetch new state data for this light."""
        level = await self._driver.query_actual_level(self._address)
        if level is not None:
            self._is_on = level > 0
            # Scale DALI level (0-254) to HA brightness (0-255)
            self._brightness = round(level * 255 / 254)
        else:
            self._is_on = False
            self._brightness = 0

    async def _handle_event(self, event) -> None:
        """Handle incoming DALI bus events to update light state.

        The LSB of the DALI address byte selects the meaning of the second
        byte: 0 = DAPC (a light level), 1 = a command opcode. Broadcasts
        follow the same rule: 0xFE is broadcast DAPC, 0xFF is a broadcast
        command. Group addressing is not tracked (lights don't know their
        group membership).
        """
        if not isinstance(event, DaliCommandEvent):
            return

        address_byte = event.address_byte
        level: Optional[int] = None
        command: Optional[int] = None

        if address_byte == DALI_BROADCAST_DAPC:
            level = event.opcode_byte
        elif address_byte == DALI_BROADCAST:
            command = event.opcode_byte
        elif address_byte == self._address * 2:
            level = event.opcode_byte
        elif address_byte == self._address * 2 + 1:
            command = event.opcode_byte
        else:
            return  # Other address, group or special command frame

        if level is not None:
            if level == DALI_MASK:
                return  # MASK = "stop fading", not a level
            self._brightness = round(level * 255 / 254)
            self._is_on = level > 0
        elif command == DALI_CMD_OFF:
            self._is_on = False
            self._brightness = 0
        elif command == DALI_CMD_RECALL_MAX_LEVEL:
            self._is_on = True
            self._brightness = 255
        else:
            return  # Other commands don't directly change the level

        self.async_write_ha_state()
