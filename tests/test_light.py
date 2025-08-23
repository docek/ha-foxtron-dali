import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from custom_components.foxtron_dali.light import DaliLight
from custom_components.foxtron_dali.driver import (
    DaliCommandEvent,
    DALI_CMD_OFF,
    DALI_CMD_RECALL_MAX_LEVEL,
)
from custom_components.foxtron_dali.const import DOMAIN


@pytest.mark.asyncio
async def test_async_turn_on_off_sends_dali_levels_and_updates_state():
    """Ensure turn_on/turn_off send correct levels and update state."""
    driver = MagicMock()
    driver.set_device_level = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "entry1"

    light = DaliLight(driver, address=1, entry=entry, config={})
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

    light = DaliLight(driver, address=1, entry=entry, config={})
    light.async_write_ha_state = MagicMock()

    # Recall max level
    recall_event = DaliCommandEvent(
        b"", address_byte=2, opcode_byte=DALI_CMD_RECALL_MAX_LEVEL
    )
    await light._handle_event(recall_event)
    assert light.is_on is True
    assert light.brightness == 255

    # Off command
    off_event = DaliCommandEvent(b"", address_byte=2, opcode_byte=DALI_CMD_OFF)
    await light._handle_event(off_event)
    assert light.is_on is False
    assert light.brightness == 0

    # Direct level command
    level_opcode = 100
    level_event = DaliCommandEvent(b"", address_byte=2, opcode_byte=level_opcode)
    await light._handle_event(level_event)
    assert light.is_on is True
    expected_brightness = round(level_opcode * 255 / 254)
    assert light.brightness == expected_brightness


@pytest.mark.asyncio
async def test_light_attached_to_bus_device():
    """Lights share the bus device."""
    driver = MagicMock()
    entry = MagicMock()
    entry.entry_id = "bus1"

    light = DaliLight(driver, address=1, entry=entry, config={})

    assert light.device_info["identifiers"] == {(DOMAIN, "bus1")}
