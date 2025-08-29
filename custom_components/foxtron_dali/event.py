import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .driver import (
    DaliInputNotificationEvent,
    FoxtronDaliDriver,
    EVENT_BUTTON_PRESSED,
    EVENT_BUTTON_RELEASED,
    format_button_id,
)

_LOGGER = logging.getLogger(__name__)

# Default timing constants (in seconds)
DEFAULT_LONG_PRESS_THRESHOLD = 0.2
DEFAULT_LONG_PRESS_REPEAT = 0.2
DEFAULT_MULTI_PRESS_WINDOW = 0.3


@dataclass
class _ButtonState:
    """Holds temporary state for a button address."""

    press_count: int = 0
    finalize_task: asyncio.Task | None = None
    long_press_task: asyncio.Task | None = None
    long_press_started: bool = False
    last_event_data: dict = field(default_factory=dict)


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
        self._entry = entry
        self._log = _LOGGER.getChild(f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}")
        self._attr_name = "DALI Button Events"
        self._attr_unique_id = (
            f"{entry.data[CONF_HOST]}_{entry.data[CONF_PORT]}_button_events"
        )
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
            "triple_press",
            "long_press_start",
            "long_press_repeat",
            "long_press_stop",
        ]
        self._unsub: Callable[[], None] | None = None
        self._button_states: dict[str, _ButtonState] = {}
        options = entry.options
        self._long_press_threshold = options.get(
            "long_press_threshold", DEFAULT_LONG_PRESS_THRESHOLD
        )
        self._long_press_repeat = options.get(
            "long_press_repeat", DEFAULT_LONG_PRESS_REPEAT
        )
        self._multi_press_window = options.get(
            "multi_press_window", DEFAULT_MULTI_PRESS_WINDOW
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        self._unsub = self._driver.add_event_listener(self._handle_event)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        if self._unsub:
            self._unsub()
        await super().async_will_remove_from_hass()

    def _trigger_event(
        self, event_type: str, event_attributes: dict | None = None
    ) -> None:
        """Fire both entity and Home Assistant bus events."""
        super()._trigger_event(event_type, event_attributes)
        if getattr(self.hass, "bus", None):
            self.hass.bus.async_fire(f"{DOMAIN}_{event_type}", event_attributes or {})

    async def _handle_event(self, event) -> None:
        """Process a single event from the DALI driver."""
        if not isinstance(event, DaliInputNotificationEvent):
            return

        if event.address is None or event.event_code not in (
            EVENT_BUTTON_PRESSED,
            EVENT_BUTTON_RELEASED,
        ):
            return

        key = format_button_id(event.address, event.instance_number)
        data = {
            "button_id": key,
            "address": event.address,
            "address_type": event.address_type,
            "instance_number": event.instance_number,
            "unique_id": self._attr_unique_id,
        }

        state = self._button_states.setdefault(key, _ButtonState())
        state.last_event_data = data

        if event.event_code == EVENT_BUTTON_PRESSED:
            self._trigger_event("button_pressed", data)

            if state.finalize_task:
                state.finalize_task.cancel()
                state.finalize_task = None

            state.long_press_task = self.hass.async_create_task(
                self._handle_long_press(key)
            )

        else:  # EVENT_BUTTON_RELEASED
            self._trigger_event("button_released", data)

            if state.long_press_task:
                state.long_press_task.cancel()
                state.long_press_task = None

            if state.long_press_started:
                self._trigger_event("long_press_stop", data)
                state.long_press_started = False
                state.press_count = 0
            else:
                state.press_count += 1
                state.finalize_task = self.hass.async_create_task(
                    self._finalize_presses(key)
                )

    async def _handle_long_press(self, key: str) -> None:
        """Handle long press start and repeat events for a button."""
        state = self._button_states[key]
        try:
            await asyncio.sleep(self._long_press_threshold)
            state.long_press_started = True
            data = state.last_event_data
            self._trigger_event("long_press_start", data)
            while True:
                await asyncio.sleep(self._long_press_repeat)
                self._trigger_event("long_press_repeat", data)
        except asyncio.CancelledError:
            return

    async def _finalize_presses(self, key: str) -> None:
        """Determine if the sequence was short, double or triple press."""
        state = self._button_states[key]
        try:
            await asyncio.sleep(self._multi_press_window)
        except asyncio.CancelledError:
            return

        count = state.press_count
        data = state.last_event_data
        event_map = {1: "short_press", 2: "double_press", 3: "triple_press"}
        if event_name := event_map.get(count):
            self._trigger_event(event_name, data)

        state.press_count = 0
        state.finalize_task = None
