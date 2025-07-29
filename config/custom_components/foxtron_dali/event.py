import logging

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .driver import DaliInputNotificationEvent, FoxtronDaliDriver

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DALI buttons from a config entry."""
    driver: FoxtronDaliDriver = hass.data[DOMAIN][entry.entry_id]

    # For buttons, we don't scan. We listen for events and let the user
    # add them via the options flow.
    # For now, we will create a single event entity to handle all button events.

    async_add_entities([DaliButton(driver)])


class DaliButton(EventEntity):
    """Representation of a DALI button event handler."""

    def __init__(self, driver: FoxtronDaliDriver) -> None:
        """Initialize the button event handler."""
        self._driver = driver
        self._attr_name = "DALI Button Events"
        self._attr_unique_id = "dali_button_events"
        self._attr_event_types = [
            "button_pressed",
            "button_released",
            "short_press",
            "double_press",
            "long_press_start",
            "long_press_repeat",
            "long_press_stop",
        ]

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()

        async def event_listener():
            while True:
                event = await self._driver.get_event()
                if isinstance(event, DaliInputNotificationEvent):
                    event_type = self._driver.EVENT_CODE_NAMES.get(
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
                        self.async_write_ha_state()

        self.async_on_remove(
            self.hass.async_create_task(event_listener())
        )