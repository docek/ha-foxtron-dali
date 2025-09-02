import asyncio
import os
import sys
from types import ModuleType
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import importlib
import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Minimal stubs for the required Home Assistant classes. These stubs allow the
# tests to run in environments where the ``homeassistant`` package is not
# available. Only the attributes and methods accessed by the integration are
# implemented.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Stub package structure for custom_components to avoid executing integration
# __init__ modules during import.
custom_components_pkg = ModuleType("custom_components")
custom_components_pkg.__path__ = [os.path.abspath("custom_components")]
foxtron_pkg = ModuleType("custom_components.foxtron_dali")
foxtron_pkg.__path__ = [os.path.abspath("custom_components/foxtron_dali")]
sys.modules.setdefault("custom_components", custom_components_pkg)
sys.modules.setdefault("custom_components.foxtron_dali", foxtron_pkg)

ha = ModuleType("homeassistant")
sys_modules = {
    "homeassistant": ha,
    "homeassistant.components": ModuleType("homeassistant.components"),
    "homeassistant.components.event": ModuleType("homeassistant.components.event"),
    "homeassistant.config_entries": ModuleType("homeassistant.config_entries"),
    "homeassistant.core": ModuleType("homeassistant.core"),
    "homeassistant.helpers": ModuleType("homeassistant.helpers"),
    "homeassistant.helpers.entity_platform": ModuleType(
        "homeassistant.helpers.entity_platform"
    ),
}


class EventEntity:
    """Very small subset of HA's EventEntity used in tests."""

    async def async_added_to_hass(self):
        return None

    async def async_will_remove_from_hass(self):
        return None

    def _trigger_event(self, event_type: str, event_attributes: dict | None = None):
        return None


class ConfigEntries:
    """Stub for hass.config_entries."""

    def __init__(self):
        self.updated: list[tuple[object, dict]] = []

    def async_update_entry(self, entry, options=None):
        entry.options = options or {}
        self.updated.append((entry, entry.options))
        return True


class Bus:
    """Simple event bus collecting fired events."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def async_fire(self, event_type: str, event_data: dict | None = None) -> None:
        self.events.append((event_type, event_data or {}))


class HomeAssistant:
    """Minimal HomeAssistant implementation."""

    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.config_entries = ConfigEntries()
        self.bus = Bus()

    def async_create_task(self, coro):
        return self.loop.create_task(coro)


class ConfigEntry:
    def __init__(self, entry_id: str, data: dict):
        self.entry_id = entry_id
        self.data = data


class AddEntitiesCallback:
    pass


setattr(sys_modules["homeassistant.components.event"], "EventEntity", EventEntity)
setattr(sys_modules["homeassistant.core"], "HomeAssistant", HomeAssistant)
setattr(sys_modules["homeassistant.config_entries"], "ConfigEntry", ConfigEntry)
setattr(
    sys_modules["homeassistant.helpers.entity_platform"],
    "AddEntitiesCallback",
    AddEntitiesCallback,
)

# Register stub modules
for name, module in sys_modules.items():
    sys.modules.setdefault(name, module)


event_module = importlib.import_module("custom_components.foxtron_dali.event")
DaliButton = event_module.DaliButton
driver_module = importlib.import_module("custom_components.foxtron_dali.driver")
DaliInputNotificationEvent = driver_module.DaliInputNotificationEvent
EVENT_BUTTON_PRESSED = driver_module.EVENT_BUTTON_PRESSED
EVENT_BUTTON_RELEASED = driver_module.EVENT_BUTTON_RELEASED
EVENT_LONG_PRESS_START = driver_module.EVENT_LONG_PRESS_START

if TYPE_CHECKING:
    from custom_components.foxtron_dali.driver import (
        DaliInputNotificationEvent as _DINEvent,
    )


class MockDriver:
    """Minimal driver used for testing DaliButton."""

    def __init__(self) -> None:
        self._callback = None

    def add_event_listener(self, callback):
        self._callback = callback

        def _unsub():
            self._callback = None

        return _unsub

    async def emit(self, event):
        if self._callback:
            result = self._callback(event)
            if asyncio.iscoroutine(result):
                await result


def _make_event(code: int) -> "_DINEvent":
    """Helper to create a DaliInputNotificationEvent with a fixed address."""
    return DaliInputNotificationEvent(bytes([0x02, 0x04, code]))


@pytest_asyncio.fixture
async def button():
    """Provide a DaliButton instance ready for testing."""
    hass = HomeAssistant()
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.data = {"host": "test", "port": 23}
    entry.options = {}
    driver = MockDriver()
    button = DaliButton(entry, driver)
    button.hass = hass
    await button.async_added_to_hass()
    yield button
    await button.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_button_pressed_and_short_press(button, monkeypatch):
    events = []

    def capture(event_type, data):
        events.append((event_type, data))

    monkeypatch.setattr(button, "_trigger_event", capture)
    button._multi_press_window = 0.01

    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))
    await asyncio.sleep(0.02)

    assert events[0] == (
        "button_pressed",
        {
            "bus_id": "test_23",
            "address": 1,
            "address_type": "Short",
            "instance_number": 1,
        },
    )
    assert events[1][0] == "button_released"
    assert events[-1][0] == "short_press"


@pytest.mark.asyncio
async def test_double_and_triple_press(button, monkeypatch):
    events = []

    def capture(event_type, data):
        events.append(event_type)

    monkeypatch.setattr(button, "_trigger_event", capture)
    button._multi_press_window = 0.01

    # Double press
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))
    await asyncio.sleep(0.02)
    assert events[-1] == "double_press"

    events.clear()

    # Triple press
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))
    await asyncio.sleep(0.02)
    assert events[-1] == "triple_press"


@pytest.mark.asyncio
async def test_long_press_sequence(button, monkeypatch):
    events = []

    def capture(event_type, data):
        events.append(event_type)

    monkeypatch.setattr(button, "_trigger_event", capture)
    button._long_press_threshold = 0.01
    button._long_press_repeat = 0.01

    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await asyncio.sleep(0.015)
    await asyncio.sleep(0.015)
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))

    assert events[0] == "button_pressed"
    assert "long_press_start" in events
    assert "long_press_repeat" in events
    assert events[-1] == "long_press_stop"


@pytest.mark.asyncio
async def test_ignores_other_events(button, monkeypatch):
    events = []

    def capture(event_type, data):
        events.append(event_type)

    monkeypatch.setattr(button, "_trigger_event", capture)

    await button._handle_event(_make_event(EVENT_LONG_PRESS_START))
    assert events == []


@pytest.mark.asyncio
async def test_button_event_does_not_store_options():
    hass = HomeAssistant()
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.data = {"host": "test", "port": 23}
    entry.options = {}
    driver = MockDriver()
    button = DaliButton(entry, driver)
    button.hass = hass
    await button.async_added_to_hass()
    button._multi_press_window = 0.01
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))
    await asyncio.sleep(0.02)
    assert entry.options == {}
    assert button.unique_id == "test_23_button_events"
    await button.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_fires_hass_event(button):
    """Ensure events are fired on Home Assistant's event bus."""
    button._multi_press_window = 0.01
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))
    await asyncio.sleep(0.02)
    assert (
        "foxtron_dali_button_event",
        {
            "bus_id": "test_23",
            "address": 1,
            "address_type": "Short",
            "instance_number": 1,
            "press_type": "button_pressed",
        },
    ) in button.hass.bus.events
