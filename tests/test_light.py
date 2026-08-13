import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT

sys.path.append(str(Path(__file__).resolve().parents[1]))

import custom_components.foxtron_dali.helpers as helpers_module
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
    driver.ensure_fade_time = AsyncMock()
    driver.fade_profile_seconds = {}
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
    driver.ensure_fade_time = AsyncMock()
    driver.fade_profile_seconds = {}
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
async def test_fade_code_proportional_to_delta():
    """The fade duration scales with the brightness delta (fade-rate feel).

    Full-range profile 2.0 s: 255->128 is half the range, so ~1.0 s
    (code 2); a small step is near-instant (code 0)."""
    light = _make_light(address=1)
    light._apply_level(255)

    await light.async_turn_on(brightness=128)
    light._driver.ensure_fade_time.assert_awaited_once_with(1, 2)

    light._driver.ensure_fade_time.reset_mock()
    await light.async_turn_on(brightness=118)  # delta 10 -> ~0.08 s -> code 0
    light._driver.ensure_fade_time.assert_awaited_once_with(1, 0)


@pytest.mark.asyncio
async def test_fade_time_written_before_dapc():
    """The fade code must reach the ballast before the level command."""
    light = _make_light(address=1)
    light._apply_level(255)
    parent = MagicMock()
    parent.attach_mock(light._driver.ensure_fade_time, "ensure_fade_time")
    parent.attach_mock(light._driver.set_device_level, "set_device_level")

    await light.async_turn_off()

    names = [c[0] for c in parent.mock_calls]
    assert names == ["ensure_fade_time", "set_device_level"]


@pytest.mark.asyncio
async def test_unknown_brightness_falls_back_to_full_range_fade():
    """Unknown current level (after startup) -> conservative full fade."""
    light = _make_light(address=1)
    assert light.brightness is None

    await light.async_turn_off()
    light._driver.ensure_fade_time.assert_awaited_once_with(1, 4)  # 2.0 s


@pytest.mark.asyncio
async def test_turn_off_fade_scales_with_current_level():
    """Turning off a dim light fades briefly, a bright one slowly."""
    light = _make_light(address=1)
    light._apply_level(100)  # delta 100 -> ~0.78 s -> code 1 (0.7 s)

    await light.async_turn_off()
    light._driver.ensure_fade_time.assert_awaited_once_with(1, 1)


@pytest.mark.asyncio
async def test_transition_overrides_computed_fade():
    """An explicit transition maps to the nearest DALI fade code."""
    light = _make_light(address=1)
    light._apply_level(255)

    await light.async_turn_on(brightness=254, transition=5.7)
    light._driver.ensure_fade_time.assert_awaited_once_with(1, 7)

    light._driver.ensure_fade_time.reset_mock()
    await light.async_turn_off(transition=5.7)
    light._driver.ensure_fade_time.assert_awaited_once_with(1, 7)


@pytest.mark.asyncio
async def test_no_fade_profile_always_code_zero():
    """The 'No fade' profile (0.0 s) disables fading entirely."""
    light = _make_light(address=1)
    light._driver.fade_profile_seconds[1] = 0.0
    light._apply_level(255)

    await light.async_turn_off()
    light._driver.ensure_fade_time.assert_awaited_once_with(1, 0)


def test_light_supports_transition():
    """The entity advertises transition support to HA."""
    light = _make_light()
    assert light.supported_features & light_module.LightEntityFeature.TRANSITION


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
    light._handle_event(recall_event)
    assert light.is_on is True
    assert light.brightness == 255

    # Off command
    off_event = DaliCommandEvent(b"", address_byte=3, opcode_byte=DALI_CMD_OFF)
    light._handle_event(off_event)
    assert light.is_on is False
    assert light.brightness == 0

    # Direct level (DAPC) frame — LSB of the address byte is 0
    level_opcode = 100
    level_event = DaliCommandEvent(b"", address_byte=2, opcode_byte=level_opcode)
    light._handle_event(level_event)
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
    light._handle_event(
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
    light._handle_event(
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
    light._handle_event(off_event)
    assert light.is_on is False

    max_event = DaliCommandEvent(
        b"", address_byte=3, opcode_byte=DALI_CMD_RECALL_MAX_LEVEL
    )
    light._handle_event(max_event)
    assert light.is_on is True
    assert light.brightness == 255


@pytest.mark.asyncio
async def test_frames_for_other_addresses_ignored():
    """Frames addressed elsewhere (or group/special) leave state alone."""
    light = _make_light(address=1)
    light._is_on = True
    light._brightness = 100

    for address_byte in (4, 5, 0x81, 0xA3):  # other short addr, group, special
        light._handle_event(
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
    light._handle_event(DaliCommandEvent(b"", address_byte=2, opcode_byte=DALI_MASK))
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
    hass.async_create_task = lambda coro: (
        tasks.append(asyncio.ensure_future(coro)) or tasks[-1]
    )

    added = []
    captured = {}

    def fake_dispatcher_connect(hass_, signal, target):
        captured["rescan"] = target
        return MagicMock()

    monkeypatch.setattr(
        helpers_module, "async_dispatcher_connect", fake_dispatcher_connect
    )
    monkeypatch.setattr(helpers_module, "registry_light_addresses", lambda h, e: set())

    await light_module.async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    await tasks[0]  # initial background scan
    assert len(added) == 2
    driver.scan_for_devices.assert_awaited_with(refresh=False)

    await captured["rescan"]()  # scan_for_lights service fires the signal
    assert len(added) == 3
    assert added[-1]._address == 3
    driver.scan_for_devices.assert_awaited_with(refresh=True)


@pytest.mark.asyncio
async def test_registry_known_lights_survive_scan_miss(monkeypatch):
    """Regression: a flaky scan must not drop lights known from the registry.

    A busy bus can occasionally miss a query reply; previously that light's
    entity simply didn't get created after a restart.
    """
    driver = MagicMock()
    # Scan misses address 5 (known from a previous run) but sees 1 and 2
    driver.scan_for_devices = AsyncMock(return_value=[1, 2])
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
    entry.async_on_unload = MagicMock()

    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": driver}}
    tasks = []
    hass.async_create_task = lambda coro: (
        tasks.append(asyncio.ensure_future(coro)) or tasks[-1]
    )

    added = []
    monkeypatch.setattr(
        helpers_module, "async_dispatcher_connect", lambda h, s, t: MagicMock()
    )
    monkeypatch.setattr(helpers_module, "registry_light_addresses", lambda h, e: {1, 5})

    await light_module.async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    await tasks[0]

    assert sorted(light._address for light in added) == [1, 2, 5]


def test_registry_addresses_parses_unique_ids(monkeypatch):
    """Addresses are parsed from light unique_ids of this config entry."""
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}

    def _reg_entry(domain, unique_id):
        e = MagicMock()
        e.domain = domain
        e.unique_id = unique_id
        return e

    entries = [
        _reg_entry("light", "1.2.3.4_23_7"),
        _reg_entry("light", "1.2.3.4_23_42"),
        _reg_entry("light", "9.9.9.9_24_3"),  # other bus
        _reg_entry("event", "1.2.3.4_23_button_events"),  # other domain
        _reg_entry("light", "1.2.3.4_23_button_events"),  # non-numeric
    ]
    monkeypatch.setattr(helpers_module.er, "async_get", lambda hass: MagicMock())
    monkeypatch.setattr(
        helpers_module.er,
        "async_entries_for_config_entry",
        lambda registry, entry_id: entries,
    )

    assert helpers_module.registry_light_addresses(MagicMock(), entry) == {7, 42}


@pytest.mark.asyncio
async def test_light_has_own_device():
    """Each light is its own HA device, linked to the bus via via_device."""
    driver = MagicMock()
    entry = MagicMock()
    entry.entry_id = "bus1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}

    light = DaliLight(driver, address=5, entry=entry)

    info = light.device_info
    assert info["identifiers"] == {(DOMAIN, "1.2.3.4_23_light_5")}
    assert info["via_device"] == (DOMAIN, "bus1")
    assert info["name"] == "DALI Light 5"


@pytest.mark.asyncio
async def test_failed_query_keeps_last_known_state():
    """A None from query_actual_level must not overwrite known state.

    Regression: a single noisy-bus timeout made a lit lamp report 'off'.
    """
    light = _make_light()
    light._apply_level(200)

    light._driver.query_actual_level = AsyncMock(return_value=None)
    await light.async_update()

    assert light.is_on is True
    assert light.brightness == 200


@pytest.mark.asyncio
async def test_mask_query_response_keeps_state():
    """MASK (0xFF) from a query means 'fading', not a level of 255+."""
    light = _make_light()
    light._apply_level(100)

    light._driver.query_actual_level = AsyncMock(return_value=255)
    await light.async_update()

    assert light.brightness == 100


def test_handle_event_is_sync_callback():
    """The per-frame event handler must be sync — an async handler makes
    the driver allocate a task per light per bus frame (138 per frame)."""
    import inspect

    assert not inspect.iscoroutinefunction(DaliLight._handle_event)


@pytest.mark.asyncio
async def test_registry_lights_added_before_scan_completes(monkeypatch):
    """Known lights must not wait for the (slow) bus scan.

    The scan takes up to ~13 s per bus; registry-known entities are
    available instantly and only NEW gear depends on the scan.
    """
    scan_started = asyncio.Event()
    scan_release = asyncio.Event()

    async def slow_scan(refresh=False):
        scan_started.set()
        await scan_release.wait()
        return [2]

    driver = MagicMock()
    driver.scan_for_devices = slow_scan
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
    entry.async_on_unload = MagicMock()

    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": driver}}
    tasks = []
    hass.async_create_task = lambda coro: (
        tasks.append(asyncio.ensure_future(coro)) or tasks[-1]
    )

    added = []
    monkeypatch.setattr(
        helpers_module, "async_dispatcher_connect", lambda h, s, t: MagicMock()
    )
    monkeypatch.setattr(helpers_module, "registry_light_addresses", lambda h, e: {1, 5})

    await light_module.async_setup_entry(hass, entry, lambda e: added.extend(e))
    # Registry lights present immediately, before the scan resolves
    assert sorted(light._address for light in added) == [1, 5]

    scan_release.set()
    await asyncio.gather(*tasks)
    assert sorted(light._address for light in added) == [1, 2, 5]


@pytest.mark.asyncio
async def test_scan_task_cancelled_on_unload(monkeypatch):
    """The startup scan task must die with the config entry."""
    started = asyncio.Event()

    async def hanging_scan(refresh=False):
        started.set()
        await asyncio.sleep(60)
        return []

    driver = MagicMock()
    driver.scan_for_devices = hanging_scan
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
    unload_callbacks = []
    entry.async_on_unload = lambda cb: unload_callbacks.append(cb)

    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": driver}}
    tasks = []
    hass.async_create_task = lambda coro: (
        tasks.append(asyncio.ensure_future(coro)) or tasks[-1]
    )

    monkeypatch.setattr(
        helpers_module, "async_dispatcher_connect", lambda h, s, t: MagicMock()
    )
    monkeypatch.setattr(helpers_module, "registry_light_addresses", lambda h, e: set())

    await light_module.async_setup_entry(hass, entry, lambda e: None)
    await started.wait()

    for cb in unload_callbacks:
        cb()
    await asyncio.sleep(0)
    assert any(t.cancelled() for t in tasks), "unload must cancel the scan task"


@pytest.mark.asyncio
async def test_added_to_hass_does_not_block_on_bus_query():
    """Entity add must not await a bus round-trip.

    276 serialized queries per bus used to gate platform startup; the
    initial state read now runs as a tracked background task.
    """
    light = _make_light()
    query_release = asyncio.Event()

    async def slow_query(addr):
        await query_release.wait()
        return 100

    light._driver.query_actual_level = slow_query
    light._driver.add_event_listener = MagicMock(return_value=MagicMock())
    light._driver.add_disconnect_callback = MagicMock(return_value=MagicMock())
    light._driver.add_connect_callback = MagicMock(return_value=MagicMock())
    tasks = []
    light.hass = MagicMock()
    light.hass.async_create_task = lambda coro: (
        tasks.append(asyncio.ensure_future(coro)) or tasks[-1]
    )

    await asyncio.wait_for(light.async_added_to_hass(), timeout=0.1)
    assert light.brightness is None, "state must not depend on the blocked query"

    query_release.set()
    await asyncio.gather(*tasks)
    assert light.brightness == round(100 * 255 / 254)


@pytest.mark.asyncio
async def test_pending_refresh_cancelled_on_remove():
    """A refresh still in flight dies with the entity."""
    light = _make_light()
    started = asyncio.Event()

    async def hanging_query(addr):
        started.set()
        await asyncio.sleep(60)

    light._driver.query_actual_level = hanging_query
    light._driver.add_event_listener = MagicMock(return_value=MagicMock())
    light._driver.add_disconnect_callback = MagicMock(return_value=MagicMock())
    light._driver.add_connect_callback = MagicMock(return_value=MagicMock())
    tasks = []
    light.hass = MagicMock()
    light.hass.async_create_task = lambda coro: (
        tasks.append(asyncio.ensure_future(coro)) or tasks[-1]
    )

    await light.async_added_to_hass()
    await started.wait()
    await light.async_will_remove_from_hass()
    await asyncio.sleep(0)
    assert all(t.cancelled() or t.done() for t in tasks)
