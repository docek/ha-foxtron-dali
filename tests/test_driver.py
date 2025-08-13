import asyncio
import importlib.util
from pathlib import Path

# Load driver module without importing the package (which requires Home Assistant)
MODULE_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "foxtron_dali" / "driver.py"
_spec = importlib.util.spec_from_file_location("driver", MODULE_PATH)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)

FoxtronMessage = driver.FoxtronMessage
FoxtronDaliDriver = driver.FoxtronDaliDriver
DaliCommandEvent = driver.DaliCommandEvent
DaliInputNotificationEvent = driver.DaliInputNotificationEvent
format_button_id = driver.format_button_id
MSG_TYPE_DALI_EVENT_NO_ANSWER = driver.MSG_TYPE_DALI_EVENT_NO_ANSWER
EVENT_BUTTON_PRESSED = driver.EVENT_BUTTON_PRESSED


def test_calculate_checksum_and_build_frame():
    """Validate checksum calculation and frame construction."""
    payload = bytes([MSG_TYPE_DALI_EVENT_NO_ANSWER, 0x10, 0x01, 0x02])
    # Known checksum from manual calculation
    assert FoxtronMessage.calculate_checksum(payload) == 0xE8
    expected_frame = b"\x01" + b"04100102E8" + b"\x17"
    assert FoxtronMessage.build_frame(payload) == expected_frame


def test_parse_and_queue_message_events_and_button_cache():
    """Feed sample frames and ensure events are queued and buttons cached."""

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

        expected_button_id = format_button_id(5, 1)
        assert expected_button_id in driver_instance._newly_discovered_buttons

    asyncio.run(run_test())
