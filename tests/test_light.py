import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT

sys.path.append(str(Path(__file__).resolve().parents[1]))

import custom_components.foxtron_dali.light as light_module
from custom_components.foxtron_dali.light import DaliLight
from custom_components.foxtron_dali.driver import (
    DaliCommandEvent,
    DALI_BROADCAST,
    DALI_BROADCAST_DAPC,
    DALI_CMD_OFF,
    DALI_CMD_RECALL_MAX_LEVEL,
    DALI_CMD_SET_FADE_TIME,
    DALI_MASK,
)
from custom_components.foxtron_dali.const import DOMAIN


def _make_light(address: int = 1) -> DaliLight:
    """Build a DaliLight with a mocked driver and state writer."""
    driver = MagicMock()
    driver.set_device_level = AsyncMock()
    driver.query_actual_level = AsyncMock(return_value=None)
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
    light = DaliLight(driver, address=address, entry=entry)
    light.async_write_ha_state = MagicMock()
    return light


@pytest.mark.asyncio
async def test_async_turn_on_off_sends_dali_levels_and_updates_state():
    """Ensure turn_on/turn_off send correct levels and update state."""
    driver = MagicMock()
    driver.set_device_level = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}

    light = DaliLight(driver, address=1, entry=entry)
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on()
    driver.set_device_level.assert_awaited_once_with(1, 254)
    assert light.is_on is True
    assert light.brightness == 255

    driver.set_device_level.reset_mock()
    await light.async_turn_off()
    driver.set_device_level.assert_awaited_once_with(1, 0)
    assert light.is_on is False
    assert light.brightness == 0


@pytest.mark.asyncio
async def test_handle_dali_command_events_updates_state():
    """Simulate DaliCommandEvent messages and confirm state transitions."""
    driver = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}

    light = DaliLight(driver, address=1, entry=entry)
    light.async_write_ha_state = MagicMock()

    # Recall max level — a command frame (LSB of the address byte is 1)
    recall_event = DaliCommandEvent(
        b"", address_byte=3, opcode_byte=DALI_CMD_RECALL_MAX_LEVEL
    )
    await light._handle_event(recall_event)
    assert light.is_on is True
    assert light.brightness == 255

    # Off command
    off_event = DaliCommandEvent(b"", address_byte=3, opcode_byte=DALI_CMD_OFF)
    await light._handle_event(off_event)
    assert light.is_on is False
    assert light.brightness == 0

    # Direct level (DAPC) frame — LSB of the address byte is 0
    level_opcode = 100
    level_event = DaliCommandEvent(b"", address_byte=2, opcode_byte=level_opcode)
    await light._handle_event(level_event)
    assert light.is_on is True
    expected_brightness = round(level_opcode * 255 / 254)
    assert light.brightness == expected_brightness


@pytest.mark.asyncio
async def test_turn_on_without_brightness_restores_last_level():
    """turn_on with no brightness restores the last known level, not 100 %."""
    light = _make_light()
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on(brightness=100)
    light._driver.set_device_level.reset_mock()
    await light.async_turn_off()

    await light.async_turn_on()
    assert light.brightness == 100

    # A light that never had a known level falls back to full brightness
    fresh = _make_light()
    fresh.async_write_ha_state = MagicMock()
    await fresh.async_turn_on()
    assert fresh.brightness == 255


@pytest.mark.asyncio
async def test_broadcast_dapc_sets_brightness():
    """0xFE broadcast DAPC carries a light level for all lights."""
    light = _make_light()
    await light._handle_event(
        DaliCommandEvent(b"", address_byte=DALI_BROADCAST_DAPC, opcode_byte=127)
    )
    assert light.is_on is True
    assert light.brightness == round(127 * 255 / 254)


@pytest.mark.asyncio
async def test_broadcast_command_is_not_a_level():
    """Regression: 0xFF broadcast SET FADE TIME must not become brightness.

    The old decoder read any 0xFF opcode as a level, so a SET FADE TIME
    (0x2F) from another master produced a phantom ~18% brightness.
    """
    light = _make_light()
    light._is_on = True
    light._brightness = 255
    await light._handle_event(
        DaliCommandEvent(
            b"", address_byte=DALI_BROADCAST, opcode_byte=DALI_CMD_SET_FADE_TIME
        )
    )
    assert light.is_on is True
    assert light.brightness == 255
    light.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_addressed_command_to_own_address():
    """LSB=1 frames to the light's own address carry commands, not levels."""
    light = _make_light(address=1)
    light._is_on = True
    light._brightness = 200

    off_event = DaliCommandEvent(b"", address_byte=3, opcode_byte=DALI_CMD_OFF)
    await light._handle_event(off_event)
    assert light.is_on is False

    max_event = DaliCommandEvent(
        b"", address_byte=3, opcode_byte=DALI_CMD_RECALL_MAX_LEVEL
    )
    await light._handle_event(max_event)
    assert light.is_on is True
    assert light.brightness == 255


@pytest.mark.asyncio
async def test_frames_for_other_addresses_ignored():
    """Frames addressed elsewhere (or group/special) leave state alone."""
    light = _make_light(address=1)
    light._is_on = True
    light._brightness = 100

    for address_byte in (4, 5, 0x81, 0xA3):  # other short addr, group, special
        await light._handle_event(
            DaliCommandEvent(b"", address_byte=address_byte, opcode_byte=0)
        )
    assert light.is_on is True
    assert light.brightness == 100
    light.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_dapc_mask_is_ignored():
    """DAPC level 0xFF (MASK = stop fading) is not a real level."""
    light = _make_light(address=1)
    light._is_on = True
    light._brightness = 100
    await light._handle_event(
        DaliCommandEvent(b"", address_byte=2, opcode_byte=DALI_MASK)
    )
    assert light.brightness == 100
    light.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_availability_follows_driver_connection():
    """Lights go unavailable on disconnect and refresh on reconnect."""
    light = _make_light(address=1)
    light._driver.query_actual_level = AsyncMock(return_value=170)
    refresh_tasks = []
    light.hass = MagicMock()
    light.hass.async_create_task = lambda coro: refresh_tasks.append(coro)

    light._handle_driver_disconnect()
    assert light.available is False

    light._handle_driver_connect()
    assert light.available is True
    assert refresh_tasks, "reconnect must schedule a state refresh"
    await refresh_tasks[0]
    assert light.is_on is True
    assert light.brightness == round(170 * 255 / 254)


@pytest.mark.asyncio
async def test_broadcast_service_updates_state_optimistically():
    """The broadcast dispatcher signal flips is_on/brightness directly."""
    light = _make_light()
    light._handle_broadcast_state(True)
    assert light.is_on is True
    assert light.brightness == 255
    light._handle_broadcast_state(False)
    assert light.is_on is False
    assert light.brightness == 0


@pytest.mark.asyncio
async def test_rescan_signal_adds_only_new_lights(monkeypatch):
    """Regression: the scan_for_lights service must add newly found lights.

    The old service called scan_for_devices() without refresh and discarded
    the result, so new lights were never added after startup.
    """
    driver = MagicMock()
    driver.scan_for_devices = AsyncMock(side_effect=[[1, 2], [1, 2, 3]])
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
    entry.async_on_unload = MagicMock()

    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": driver}}
    tasks = []
    hass.async_create_task = lambda coro: tasks.append(coro)

    added = []
    captured = {}

    def fake_dispatcher_connect(hass_, signal, target):
        captured["rescan"] = target
        return MagicMock()

    monkeypatch.setattr(
        light_module, "async_dispatcher_connect", fake_dispatcher_connect
    )

    await light_module.async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    await tasks[0]  # initial background scan
    assert len(added) == 2
    driver.scan_for_devices.assert_awaited_with(refresh=False)

    await captured["rescan"]()  # scan_for_lights service fires the signal
    assert len(added) == 3
    assert added[-1]._address == 3
    driver.scan_for_devices.assert_awaited_with(refresh=True)


@pytest.mark.asyncio
async def test_light_attached_to_bus_device():
    """Lights share the bus device."""
    driver = MagicMock()
    entry = MagicMock()
    entry.entry_id = "bus1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}

    light = DaliLight(driver, address=1, entry=entry)

    assert light.device_info["identifiers"] == {(DOMAIN, "bus1")}
