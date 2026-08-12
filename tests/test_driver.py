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


def test_set_fade_time_sends_correct_opcode_bytes():
    """SET FADE TIME must send DTR0 (0xA3) then opcode 0x2E, twice.

    Regression: 0x2F is SET FADE RATE per IEC 62386-102; asserting the
    literal bytes (not the module constants) catches a wrong constant.
    """

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        calls = []

        async def fake_send(address_byte, opcode_byte, send_twice=True):
            calls.append((address_byte, opcode_byte, send_twice))

        driver_instance.send_dali_command = fake_send
        await driver_instance.set_fade_time(4)

        assert calls == [(0xA3, 4, False), (0xFF, 0x2E, True)]

    asyncio.run(run_test())


def test_set_fade_rate_sends_correct_opcode_bytes():
    """SET FADE RATE broadcasts DTR0 (0xA3) then opcode 0x2F, twice."""

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        calls = []

        async def fake_send(address_byte, opcode_byte, send_twice=True):
            calls.append((address_byte, opcode_byte, send_twice))

        driver_instance.send_dali_command = fake_send
        await driver_instance.set_fade_rate(7)

        assert calls == [(0xA3, 7, False), (0xFF, 0x2F, True)]

    asyncio.run(run_test())


def test_concurrent_fade_config_writes_do_not_interleave():
    """DTR0 is global bus state: DTR0 -> SET pairs must stay adjacent
    even when two coroutines write fade config concurrently."""

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        calls = []

        async def fake_send(address_byte, opcode_byte, send_twice=True):
            calls.append((address_byte, opcode_byte))
            await asyncio.sleep(0)  # give the other coroutine a chance to run

        driver_instance.send_dali_command = fake_send
        await asyncio.gather(
            driver_instance.set_fade_time(2),
            driver_instance.set_fade_time(9),
        )

        assert len(calls) == 4
        for i in (0, 2):
            assert calls[i][0] == 0xA3, f"call {i} must be DTR0, got {calls}"
            assert calls[i + 1] == (0xFF, 0x2E), f"pair broken: {calls}"
        # Both fade codes were written
        assert {calls[0][1], calls[2][1]} == {2, 9}

    asyncio.run(run_test())


def test_set_fade_time_per_device_bytes():
    """Per-device SET FADE TIME addresses the short address (addr*2+1)."""

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        calls = []

        async def fake_send(address_byte, opcode_byte, send_twice=True):
            calls.append((address_byte, opcode_byte, send_twice))

        driver_instance.send_dali_command = fake_send
        await driver_instance.set_fade_time(4, short_address=12)

        assert calls == [(0xA3, 4, False), (12 * 2 + 1, 0x2E, True)]

    asyncio.run(run_test())


def test_query_fade_time_parses_upper_nibble():
    """QUERY FADE TIME/FADE RATE (0xA5): UPPER nibble is fade time.

    Regression: the first implementation returned the lower nibble (fade
    rate), so every select showed the remediated fade rate 7 instead of
    the actual fade time (verified against IEC 62386-102 / python-dali).
    """

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        seen = []

        async def fake_query(address_byte, opcode_byte, **kwargs):
            seen.append((address_byte, opcode_byte))
            return 0x47  # fade time 4 (upper), fade rate 7 (lower)

        driver_instance.send_dali_query = fake_query

        assert await driver_instance.query_fade_time(3) == 4
        assert seen == [(3 * 2 + 1, 0xA5)]

    asyncio.run(run_test())


def test_concurrent_scans_share_one_bus_sweep():
    """Two platforms scanning at startup must not double the bus traffic.

    Regression: light and select platforms both triggered a full scan
    concurrently; the duplicate-query guard then dropped probes with
    'Query for 0191 already in progress' warnings.
    """

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        # Connected, so the finished sweep is cached for the second caller
        driver_instance._connection = MagicMock(is_connected=True)
        seen = []

        async def fake_query(address_byte, opcode_byte, **kwargs):
            seen.append(address_byte)
            await asyncio.sleep(0)  # let the second scan start mid-sweep
            return 0xFF if address_byte == (5 * 2) + 1 else None

        driver_instance.send_dali_query = fake_query
        results = await asyncio.gather(
            driver_instance.scan_for_devices(),
            driver_instance.scan_for_devices(),
        )

        assert [r for r in results] == [[5], [5]]
        assert len(seen) == 64, f"expected one sweep, got {len(seen)} queries"

    asyncio.run(run_test())


def test_query_fade_time_no_response_returns_none():
    """A ballast that doesn't answer yields None, not a bogus code."""

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)

        async def fake_query(address_byte, opcode_byte, **kwargs):
            return None

        driver_instance.send_dali_query = fake_query

        assert await driver_instance.query_fade_time(3) is None

    asyncio.run(run_test())


def test_mismatched_response_does_not_resolve_pending_query():
    """A 0x0D response whose echoed frame differs from the pending query
    must not resolve it.

    Regression (phantom lights): a scan probe reply arriving after its
    timeout was blindly attributed to the single pending probe for the
    NEXT address, creating a phantom light at real_address + 1. The
    gateway echoes the original DALI frame in every 0x0D response, so
    only an exact echo match may resolve a pending query.
    """

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        pending = bytes([0x19, 0x91])  # probe for address 12
        future = asyncio.get_running_loop().create_future()
        driver_instance._pending_dali_queries[pending] = future

        # Late reply to the PREVIOUS probe (address 11, echo 0x17 0x91)
        late_reply = bytes([0x0D, 16, 8, 0x17, 0x91, 0xFF])
        event = driver_instance._handle_dali_response(late_reply)

        assert not future.done(), "mismatched echo must not resolve the query"
        assert driver_instance._pending_dali_queries == {pending: future}
        # The stray answer surfaces as an unsolicited response event
        assert isinstance(event, driver.DaliQueryResponseEvent)

    asyncio.run(run_test())


def test_matching_response_resolves_pending_query():
    """An exact echo match resolves the pending query with the answer."""

    async def run_test():
        driver_instance = FoxtronDaliDriver("host", 1234)
        pending = bytes([0x19, 0x91])
        future = asyncio.get_running_loop().create_future()
        driver_instance._pending_dali_queries[pending] = future

        reply = bytes([0x0D, 16, 8, 0x19, 0x91, 0xFF])
        driver_instance._handle_dali_response(reply)

        assert future.done() and future.result() == 0xFF
        assert driver_instance._pending_dali_queries == {}

    asyncio.run(run_test())


def test_light_broadcast_helpers_removed():
    """broadcast_on/broadcast_off were removed with the broadcast services."""
    assert not hasattr(FoxtronDaliDriver, "broadcast_on")
    assert not hasattr(FoxtronDaliDriver, "broadcast_off")


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
