import logging
from typing import Any, Optional, Callable

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import helpers
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


def _dali_to_brightness(level: int) -> int:
    """Scale a DALI level (0-254) to HA brightness (0-255)."""
    return round(level * 255 / 254)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DALI lights from a config entry."""
    await helpers.async_setup_scanned_entities(
        hass,
        entry,
        async_add_entities,
        lambda driver, addr: DaliLight(driver, addr, entry),
    )


class DaliLight(helpers.ConnectionAwareEntity, LightEntity):
    """Representation of a DALI light."""

    # The entity takes the name of its per-light device ("DALI Light N")
    _attr_has_entity_name = True
    _attr_name = None

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
        self._last_on_brightness: Optional[int] = None
        self._is_on = False
        self._unsub: Callable[[], None] | None = None

    def _apply_level(self, brightness: int) -> None:
        """Set brightness/is_on and remember the last non-zero level."""
        self._brightness = brightness
        self._is_on = brightness > 0
        if brightness > 0:
            self._last_on_brightness = brightness

    @property
    def unique_id(self) -> str:
        """Return a unique ID for the light."""
        return f"{helpers.bus_id(self._entry)}_{self._address}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this device."""
        return helpers.light_device_info(self._entry, self._address)

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
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            # HA convention: restore the last known brightness, full as fallback
            brightness = self._last_on_brightness or 255
        # Scale HA brightness (0-255) to DALI level (0-254)
        dali_level = round(brightness * 254 / 255)

        await self._driver.set_device_level(self._address, dali_level)
        self._apply_level(brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._driver.set_device_level(self._address, 0)
        self._apply_level(0)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for bus events when added to Home Assistant."""
        await super().async_added_to_hass()
        self._unsub = self._driver.add_event_listener(self._handle_event)

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup when entity is removed from Home Assistant."""
        if self._unsub:
            self._unsub()
        await super().async_will_remove_from_hass()

    async def async_update(self) -> None:
        """Fetch new state data for this light.

        A failed query (None: timeout, disconnect race) or MASK (0xFF =
        "fading", not a level) must not overwrite the last known state —
        a single noisy-bus timeout used to flip a lit lamp to "off".
        """
        level = await self._driver.query_actual_level(self._address)
        if level is None or level == DALI_MASK:
            return
        # Scale DALI level (0-254) to HA brightness (0-255)
        self._apply_level(_dali_to_brightness(level))

    @callback
    def _handle_event(self, event) -> None:
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
            self._apply_level(_dali_to_brightness(level))
        elif command == DALI_CMD_OFF:
            self._apply_level(0)
        elif command == DALI_CMD_RECALL_MAX_LEVEL:
            self._apply_level(255)
        else:
            return  # Other commands don't directly change the level

        self.async_write_ha_state()
