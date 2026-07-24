import asyncio
import importlib.util
from importlib.machinery import ModuleSpec
from pathlib import Path
from unittest.mock import MagicMock

# Load driver module without importing the package (which requires Home Assistant)
MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "foxtron_dali"
    / "driver.py"
)
_spec: ModuleSpec | None = importlib.util.spec_from_file_location("driver", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)

FoxtronMessage = driver.FoxtronMessage
FoxtronDaliDriver = driver.FoxtronDaliDriver
DaliCommandEvent = driver.DaliCommandEvent
DaliInputNotificationEvent = driver.DaliInputNotificationEvent
MSG_TYPE_DALI_EVENT_NO_ANSWER = driver.MSG_TYPE_DALI_EVENT_NO_ANSWER
EVENT_BUTTON_PRESSED = driver.EVENT_BUTTON_PRESSED


def test_calculate_checksum_and_build_frame():
    """Validate checksum calculation and frame construction."""
    payload = bytes([MSG_TYPE_DALI_EVENT_NO_ANSWER, 0x10, 0x01, 0x02])
    # Known checksum from manual calculation
    assert FoxtronMessage.calculate_checksum(payload) == 0xE8
    expected_frame = b"\x01" + b"04100102E8" + b"\x17"
    assert FoxtronMessage.build_frame(payload) == expected_frame


def test_set_fade_time_sends_config_command_twice():
    """SET FADE TIME is a DALI config command and must be sent twice."""

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        calls = []

        async def fake_send(address_byte, opcode_byte, send_twice=True):
            calls.append((address_byte, opcode_byte, send_twice))

        driver_instance.send_dali_command = fake_send
        await driver_instance.set_fade_time(4)

        assert calls[0] == (driver.DALI_CMD_DTR0, 4, False)
        assert calls[1] == (
            driver.DALI_BROADCAST,
            driver.DALI_CMD_SET_FADE_TIME,
            True,
        )

    asyncio.run(run_test())


def test_scan_uses_presence_query_without_retries():
    """The bus scan probes all 64 addresses with QUERY CONTROL GEAR PRESENT
    (0x91) and no retries, and skips caching while disconnected."""

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        seen = []

        async def fake_query(
            address_byte,
            opcode_byte,
            timeout=0.5,
            retries=2,
            backoff=0.1,
            warn_on_timeout=True,
        ):
            seen.append((address_byte, opcode_byte, retries, warn_on_timeout))
            return 0xFF if address_byte == (5 * 2) + 1 else None

        driver_instance.send_dali_query = fake_query
        result = await driver_instance.scan_for_devices()

        assert result == [5]
        assert len(seen) == 64
        assert all(
            opcode == driver.DALI_CMD_QUERY_CONTROL_GEAR_PRESENT
            and retries == 0
            # Empty addresses are expected during a scan; probing must not warn.
            and warn_on_timeout is False
            for _, opcode, retries, warn_on_timeout in seen
        )
        # Not connected -> the (possibly incomplete) result is not cached
        assert driver_instance._scan_cache is None

    asyncio.run(run_test())


def _make_timing_out_driver():
    """A driver whose queries always time out (frame sent, no reply ever)."""
    driver_instance = FoxtronDaliDriver("host", 1234)
    driver_instance._log = MagicMock()

    async def _noop_frame(dali_command, params=0x00):
        return None

    driver_instance._send_dali_frame = _noop_frame
    return driver_instance


def test_query_timeout_warns_by_default():
    """A real query that never gets a reply logs a WARNING after all attempts."""

    async def run_test():
        driver_instance = _make_timing_out_driver()
        result = await driver_instance.send_dali_query(
            0x03, 0xA0, timeout=0.01, retries=0
        )
        assert result is None
        assert driver_instance._log.warning.called
        assert not driver_instance._log.debug.call_args_list or all(
            "after" not in (call.args[0] if call.args else "")
            for call in driver_instance._log.debug.call_args_list
        )

    asyncio.run(run_test())


def test_query_timeout_stays_debug_when_warn_disabled():
    """Presence probing (warn_on_timeout=False) must not emit a WARNING."""

    async def run_test():
        driver_instance = _make_timing_out_driver()
        result = await driver_instance.send_dali_query(
            0x03, 0x91, timeout=0.01, retries=0, warn_on_timeout=False
        )
        assert result is None
        assert not driver_instance._log.warning.called
        assert any(
            call.args and "after" in call.args[0]
            for call in driver_instance._log.debug.call_args_list
        )

    asyncio.run(run_test())


def test_parse_and_queue_message_events():
    """Feed sample frames and ensure events are queued."""

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)

        # Frame for a 16-bit DALI command (address 0x01, opcode 0x02)
        cmd_frame_hex = b"04100102E8"
        await driver_instance._parse_and_queue_message(cmd_frame_hex)
        cmd_event = await driver_instance._event_queue.get()
        assert isinstance(cmd_event, DaliCommandEvent)
        assert cmd_event.address_byte == 0x01
        assert cmd_event.opcode_byte == 0x02

        # Frame for a DALI-2 input notification (short address 5, instance 1)
        input_payload = bytes(
            [MSG_TYPE_DALI_EVENT_NO_ANSWER, 0x18, 0x0A, 0x04, EVENT_BUTTON_PRESSED]
        )
        input_frame_hex = FoxtronMessage.build_frame(input_payload)[1:-1]
        await driver_instance._parse_and_queue_message(input_frame_hex)
        input_event = await driver_instance._event_queue.get()
        assert isinstance(input_event, DaliInputNotificationEvent)
        assert input_event.address == 5
        assert input_event.instance_number == 1

    asyncio.run(run_test())
