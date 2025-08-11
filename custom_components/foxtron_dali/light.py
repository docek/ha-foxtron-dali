import logging
from typing import Any, Optional, Dict, Callable

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .driver import (
    FoxtronDaliDriver,
    DaliCommandEvent,
    DALI_BROADCAST,
    DALI_CMD_OFF,
    DALI_CMD_RECALL_MAX_LEVEL,
)

_LOGGER = logging.getLogger(__name__)

def _generate_unique_id(light_config: Dict, discovered_addresses: list) -> Dict:
    """Generate unique IDs for lights that don't have one."""
    name_area_counters = {}
    for address in discovered_addresses:
        if address in light_config:
            config = light_config[address]
            if not config.get("unique_id"):
                name = config.get("name", f"DALI Light {address}")
                area = config.get("area", "")
                key = (name, area)
                if key not in name_area_counters:
                    name_area_counters[key] = 1
                else:
                    name_area_counters[key] += 1
                
                config["unique_id"] = (
                    f"light.{area.lower().replace(' ', '_')}_"
                    f"{name.lower().replace(' ', '_')}_{name_area_counters[key]}"
                )
    return light_config

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DALI lights from a config entry."""
    driver: FoxtronDaliDriver = hass.data[DOMAIN][entry.entry_id]
    light_config = entry.options.get("light_config", {})

    async def _scan_and_add() -> None:
        """Scan the bus and add discovered lights."""
        discovered_addresses = await driver.scan_for_devices()
        updated_config = _generate_unique_id(light_config, discovered_addresses)
        lights = [
            DaliLight(driver, addr, entry, updated_config.get(addr))
            for addr in discovered_addresses
        ]
        async_add_entities(lights)

    hass.async_create_task(_scan_and_add())


class DaliLight(LightEntity):
    """Representation of a DALI light."""

    _attr_should_poll = False

    def __init__(self, driver: FoxtronDaliDriver, address: int, entry: ConfigEntry, config: Dict) -> None:
        """Initialize the light."""
        self._driver = driver
        self._address = address
        self._entry = entry
        self._config = config or {}
        self._attr_color_mode = ColorMode.BRIGHTNESS
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._brightness: Optional[int] = None
        self._is_on = False
        self._unsub: Callable[[], None] | None = None

    @property
    def name(self) -> str:
        """Return the name of the light."""
        return self._config.get("name", f"DALI Light {self._address}")

    @property
    def unique_id(self) -> str:
        """Return a unique ID for the light."""
        return self._config.get("unique_id", f"{self._entry.entry_id}_{self._address}")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer="Foxtron",
            via_device=(DOMAIN, self._entry.entry_id),
            suggested_area=self._config.get("area"),
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
        await self.async_update()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup when entity is removed from Home Assistant."""
        if self._unsub:
            self._unsub()
        await super().async_will_remove_from_hass()

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
        """Handle incoming DALI bus events to update light state."""
        if not isinstance(event, DaliCommandEvent):
            return

        if event.address_byte not in (DALI_BROADCAST, self._address * 2):
            return

        opcode = event.opcode_byte
        if opcode == DALI_CMD_OFF:
            self._is_on = False
            self._brightness = 0
        elif opcode == DALI_CMD_RECALL_MAX_LEVEL:
            self._is_on = True
            self._brightness = 255
        elif 0 <= opcode <= 254:
            self._brightness = round(opcode * 255 / 254)
            self._is_on = self._brightness > 0
        else:
            return

        self.async_write_ha_state()
