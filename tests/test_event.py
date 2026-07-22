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

    def async_on_remove(self, func):
        return None

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
        self.listeners: dict[str, list] = {}

    def async_fire(self, event_type: str, event_data: dict | None = None) -> None:
        self.events.append((event_type, event_data or {}))

    def async_listen(self, event_type: str, listener):
        self.listeners.setdefault(event_type, []).append(listener)

        def _unsub() -> None:
            self.listeners[event_type].remove(listener)

        return _unsub


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
        self._disconnect_callbacks: list = []

    def add_disconnect_callback(self, callback):
        self._disconnect_callbacks.append(callback)

        def _unsub() -> None:
            if callback in self._disconnect_callbacks:
                self._disconnect_callbacks.remove(callback)

        return _unsub

    def add_connect_callback(self, callback):
        self._connect_callbacks = getattr(self, "_connect_callbacks", [])
        self._connect_callbacks.append(callback)

        def _unsub() -> None:
            if callback in self._connect_callbacks:
                self._connect_callbacks.remove(callback)

        return _unsub

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


@pytest.fixture(autouse=True)
def switch_devices(monkeypatch):
    """Replace the HA device registry with a fake backed by a plain list.

    Tests can append fake paired-switch devices to the yielded list to make
    them visible to DaliButton's device lookups.
    """
    devices: list = []
    monkeypatch.setattr(event_module.dr, "async_get", lambda hass: MagicMock())
    monkeypatch.setattr(
        event_module.dr,
        "async_entries_for_config_entry",
        lambda registry, entry_id: devices,
    )
    yield devices


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
    button.async_write_ha_state = MagicMock()
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
    button.async_write_ha_state = MagicMock()
    await button.async_added_to_hass()
    button._multi_press_window = 0.01
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    await button._handle_event(_make_event(EVENT_BUTTON_RELEASED))
    await asyncio.sleep(0.02)
    assert entry.options == {}
    assert button.unique_id == "test_23_button_events"
    await button.async_will_remove_from_hass()


DOMAIN = event_module.DOMAIN


def _make_switch_device(identifier: str, hw_version=None, sw_version=None):
    """Build a fake paired-switch DeviceEntry."""
    device = MagicMock()
    device.identifiers = {(DOMAIN, identifier)}
    device.hw_version = hw_version
    device.sw_version = sw_version
    device.name = "Test Switch"
    device.id = "device123"
    return device


def test_parse_switch_identity(button):
    """Address and instance mapping are parsed from the device identifier."""
    device = _make_switch_device(
        "dali4sw_test_23_1_1_0",
        hw_version="Addr 1",
        sw_version="↑ Inst 1, ↓ Inst 0",
    )
    assert button._parse_switch_identity(device) == (1, 1, 0)


def test_parse_switch_identity_rejects_malformed(button):
    """Devices without the identifier-borne mapping yield no identity."""
    device = _make_switch_device("dali4sw_test_23_1", hw_version="1,0")
    assert button._parse_switch_identity(device) == (None, None, None)


@pytest.mark.asyncio
async def test_device_trigger_fires_for_new_format_device(button, switch_devices):
    """Regression: EVENT_BUTTON_ACTION must fire for newly paired switches.

    Devices paired by the current code store 'Addr N' in hw_version; the old
    inline parser in _trigger_event choked on it and never fired the native
    device trigger.
    """
    switch_devices.append(
        _make_switch_device(
            "dali4sw_test_23_1_1_0",
            hw_version="Addr 1",
            sw_version="↑ Inst 1, ↓ Inst 0",
        )
    )

    # _make_event: address 1, instance 1 -> upper flap
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    button._cancel_all_button_tasks()

    assert (
        event_module.EVENT_BUTTON_ACTION,
        {"device_id": "device123", "flap": "upper", "press_type": "button_pressed"},
    ) in button.hass.bus.events


@pytest.mark.asyncio
async def test_device_trigger_lower_flap(button, switch_devices):
    """Instance matching the lower slot fires with flap=lower."""
    # _make_event address 1 instance 1 -> lower when identifier maps 1 as lower
    switch_devices.append(
        _make_switch_device("dali4sw_test_23_1_0_1", hw_version="Addr 1")
    )

    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    button._cancel_all_button_tasks()

    assert (
        event_module.EVENT_BUTTON_ACTION,
        {"device_id": "device123", "flap": "lower", "press_type": "button_pressed"},
    ) in button.hass.bus.events


@pytest.mark.asyncio
async def test_no_device_trigger_without_paired_device(button):
    """No EVENT_BUTTON_ACTION when the pressed button has no paired device."""
    await button._handle_event(_make_event(EVENT_BUTTON_PRESSED))
    button._cancel_all_button_tasks()

    fired = [e for e, _ in button.hass.bus.events]
    assert event_module.EVENT_BUTTON_ACTION not in fired


@pytest.mark.asyncio
async def test_button_availability_follows_driver(button):
    """The event entity goes unavailable on disconnect and back on connect."""
    button._handle_driver_disconnect()
    assert button.available is False
    button._handle_driver_connect()
    assert button.available is True


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
