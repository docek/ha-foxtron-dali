import asyncio
import logging

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .driver import (
    DaliInputNotificationEvent,
    FoxtronDaliDriver,
    EVENT_CODE_NAMES,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DALI buttons from a config entry."""
    driver: FoxtronDaliDriver = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DaliButton(entry, driver)])


class DaliButton(EventEntity):
    """Representation of a DALI button event handler."""

    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, driver: FoxtronDaliDriver) -> None:
        """Initialize the button event handler."""
        self._driver = driver
        self._attr_name = "DALI Button Events"
        # Make the unique_id specific to the config entry
        self._attr_unique_id = f"{entry.entry_id}_dali_button_events"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"DALI Bus ({entry.data['host']})",
            "manufacturer": "Foxtron",
            "model": "DALI2net",
        }
        self._attr_event_types = [
            "button_pressed",
            "button_released",
            "short_press",
            "double_press",
            "long_press_start",
            "long_press_repeat",
            "long_press_stop",
        ]
        self._listener_task = None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        self._listener_task = self.hass.async_create_task(self._event_listener())

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        await super().async_will_remove_from_hass()

    async def _event_listener(self) -> None:
        """Listen for and process events from the DALI driver."""
        try:
            while self.hass.is_running:
                event = await self._driver.get_event()
                if not isinstance(event, DaliInputNotificationEvent):
                    continue

                event_type = EVENT_CODE_NAMES.get(
                    event.event_code, "unknown"
                ).lower().replace(" ", "_")

                if event_type in self._attr_event_types:
                    self._trigger_event(
                        event_type,
                        {
                            "address": event.address,
                            "address_type": event.address_type,
                            "instance_number": event.instance_number,
                        },
                    )
                    # EventEntity does not have a visual state,
                    # so async_write_ha_state is not needed here.
        except asyncio.CancelledError:
            _LOGGER.debug("DALI event listener task cancelled.")
        except Exception:
            _LOGGER.exception("Unexpected error in DALI event listener")
