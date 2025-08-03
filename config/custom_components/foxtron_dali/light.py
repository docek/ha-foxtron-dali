import logging
from typing import Any, Optional

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .driver import FoxtronDaliDriver

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DALI lights from a config entry."""
    driver: FoxtronDaliDriver = hass.data[DOMAIN][entry.entry_id]

    # Discover lights on the DALI bus
    discovered_addresses = await driver.scan_for_devices()
    lights = [DaliLight(driver, addr, entry) for addr in discovered_addresses]
    async_add_entities(lights)


class DaliLight(LightEntity):
    """Representation of a DALI light."""

    def __init__(self, driver: FoxtronDaliDriver, address: int, entry: ConfigEntry) -> None:
        """Initialize the light."""
        self._driver = driver
        self._address = address
        self._entry = entry
        self._attr_color_mode = ColorMode.BRIGHTNESS
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._brightness: Optional[int] = None
        self._is_on = False

    @property
    def name(self) -> str:
        """Return the name of the light."""
        return f"DALI Light {self._address}"

    @property
    def unique_id(self) -> str:
        """Return a unique ID for the light."""
        return f"{self._entry.entry_id}_{self._address}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"DALI Bus ({self._entry.data[CONF_HOST]}:{self._entry.data[CONF_PORT]})",
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
