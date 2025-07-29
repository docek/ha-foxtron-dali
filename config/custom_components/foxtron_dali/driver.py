# foxtron_sdk.py

import asyncio
import binascii
import logging
from typing import Awaitable, Callable, Dict, List, Optional

# --- Basic Logging Setup ---
_LOGGER = logging.getLogger(__name__)

# --- Protocol Constants ---
SOH = b"\x01"  # Start of Heading
ETB = b"\x17"  # End of Transmission Block
KEEP_ALIVE_INTERVAL = 20  # Seconds

# --- Foxtron Message Types ---
# Received from Gateway
MSG_TYPE_DALI_EVENT_WITH_ANSWER = 0x03
MSG_TYPE_DALI_EVENT_NO_ANSWER = 0x04
MSG_TYPE_SPECIAL_GATEWAY_EVENT = 0x05
MSG_TYPE_CONFIG_RESPONSE = 0x07
MSG_TYPE_DALI_RESPONSE_WITH_ANSWER = 0x0D
MSG_TYPE_CONFIRMATION_NO_ANSWER = 0x0E

# Sent to Gateway
MSG_TYPE_QUERY_CONFIG_ITEM = 0x06
MSG_TYPE_SEND_DALI_COMMAND = 0x0B

# --- DALI-2 Input Notification Event Codes (from DALI4SW manual) ---
EVENT_BUTTON_PRESSED = 0x00
EVENT_BUTTON_RELEASED = 0x01
EVENT_SHORT_PRESS = 0x02
EVENT_DOUBLE_PRESS = 0x03
EVENT_LONG_PRESS_START = 0x04
EVENT_LONG_PRESS_REPEAT = 0x05
EVENT_LONG_PRESS_STOP = 0x06
EVENT_BUTTON_STUCK = 0x07
EVENT_BUTTON_FREE = 0x08

# --- DALI Standard Command Opcodes (IEC 62386-102) ---
DALI_CMD_OFF = 0x00
DALI_CMD_RECALL_MAX_LEVEL = 0x05
DALI_CMD_SET_FADE_TIME = 0x2F
DALI_CMD_QUERY_CONTROL_GEAR_PRESENT = 0x90
DALI_CMD_QUERY_ACTUAL_LEVEL = 0xA0
DALI_CMD_DTR0 = 0xA3  # Set Data Transfer Register 0

# --- DALI Addressing ---
DALI_BROADCAST = 0xFF

# --- Mappings for Readable Logs ---
MESSAGE_TYPE_NAMES = {
    MSG_TYPE_DALI_EVENT_WITH_ANSWER: "DALI Event w/ Answer (Spontaneous)",
    MSG_TYPE_DALI_EVENT_NO_ANSWER: "DALI Event w/o Answer (Spontaneous)",
    MSG_TYPE_SPECIAL_GATEWAY_EVENT: "Special Gateway Event",
    MSG_TYPE_CONFIG_RESPONSE: "Config Response",
    MSG_TYPE_DALI_RESPONSE_WITH_ANSWER: "DALI Response w/ Answer (Differentiated)",
    MSG_TYPE_CONFIRMATION_NO_ANSWER: "Confirmation w/o Answer (Differentiated)",
    MSG_TYPE_QUERY_CONFIG_ITEM: "Query Config Item",
    MSG_TYPE_SEND_DALI_COMMAND: "Send DALI Command",
}

EVENT_CODE_NAMES = {
    EVENT_BUTTON_PRESSED: "Button Pressed",
    EVENT_BUTTON_RELEASED: "Button Released",
    EVENT_SHORT_PRESS: "Short Press",
    EVENT_DOUBLE_PRESS: "Double Press",
    EVENT_LONG_PRESS_START: "Long Press Start",
    EVENT_LONG_PRESS_REPEAT: "Long Press Repeat",
    EVENT_LONG_PRESS_STOP: "Long Press Stop",
    EVENT_BUTTON_STUCK: "Button Stuck",
    EVENT_BUTTON_FREE: "Button Free",
}


# --- DALI Event Data Classes ---
class DaliEvent:
    """Base class for a parsed event from the DALI bus."""

    def __init__(self, raw_payload: bytes, description: str = "Generic DALI Event"):
        self.raw_payload = raw_payload
        self.description = description

    def __repr__(self):
        return f"{self.__class__.__name__}(desc='{self.description}', raw='{self.raw_payload.hex().upper()}')"


class DaliCommandEvent(DaliEvent):
    """Represents a standard 16-bit DALI command observed on the bus."""

    def __init__(self, raw_payload: bytes, address_byte: int, opcode_byte: int):
        super().__init__(raw_payload, "DALI Command")
        self.address_byte = address_byte
        self.opcode_byte = opcode_byte

    def __repr__(self):
        return (
            f"DaliCommandEvent(address=0x{self.address_byte:02X}, "
            f"opcode=0x{self.opcode_byte:02X})"
        )


class DaliInputNotificationEvent(DaliEvent):
    """Represents a 24-bit DALI-2 Input Notification event."""

    def __init__(self, raw_payload: bytes):
        super().__init__(raw_payload, "DALI-2 Input Notification")
        addressing_byte = raw_payload[0]
        self.instance_number = raw_payload[1]
        self.event_code = raw_payload[2]

        self.address_type: str
        self.address: Optional[int]

        if (addressing_byte >> 7) == 0:
            self.address_type = "Short"
            self.address = addressing_byte & 0x3F
        elif (addressing_byte >> 6) == 0b10:
            self.address_type = "Group"
            self.address = addressing_byte & 0x0F
        elif addressing_byte == 0xFF:
            self.address_type = "Broadcast"
            self.address = None
        else:
            self.address_type = "Unknown"
            self.address = None

    def __repr__(self):
        event_name = EVENT_CODE_NAMES.get(self.event_code, "Unknown")
        if self.address is not None:
            addr_str = f"{self.address_type} Address={self.address}"
        else:
            addr_str = self.address_type

        return (
            f"DaliInputNotificationEvent({addr_str}, "
            f"Instance={self.instance_number}, EventCode={self.event_code} ({event_name}))"
        )


class SpecialGatewayEvent(DaliEvent):
    """Represents a Type 5 special message from the gateway itself."""

    EVENT_MAP = {
        0: "Valid DALI Power",
        1: "DALI Power Loss",
        2: "Mains Voltage on Bus",
        3: "Defective Power Supply",
        4: "Message Buffer Full",
        5: "Checksum Error",
        6: "Invalid Command",
    }

    def __init__(self, raw_payload: bytes, event_code: int):
        description = self.EVENT_MAP.get(event_code, "Unknown Special Event")
        super().__init__(raw_payload, description)
        self.event_code = event_code

    def __repr__(self):
        return f"SpecialGatewayEvent(code={self.event_code}, desc='{self.description}')"


class ConfigResponseEvent(DaliEvent):
    """Represents a Type 7 configuration response from the gateway."""

    def __init__(self, raw_payload: bytes, item_number: int, value: int):
        super().__init__(raw_payload, f"Config Response for Item {item_number}")
        self.item_number = item_number
        self.value = value

    def __repr__(self):
        return f"ConfigResponseEvent(item={self.item_number}, value={self.value})"


class DaliQueryResponseEvent(DaliEvent):
    """Represents a DALI backward frame (response) to a query."""

    def __init__(self, raw_payload: bytes, address: int, value: int):
        super().__init__(raw_payload, f"DALI Query Response for Address {address}")
        self.address = address
        self.value = value

    def __repr__(self):
        return f"DaliQueryResponseEvent(address={self.address}, value={self.value})"


# --- Low-Level Connection and Parsing ---


class FoxtronMessage:
    """Helper class for building and parsing Foxtron protocol frames."""

    @staticmethod
    def calculate_checksum(data_payload: bytes) -> int:
        return (~sum(data_payload)) & 0xFF

    @staticmethod
    def build_frame(data_payload: bytes) -> bytes:
        checksum = FoxtronMessage.calculate_checksum(data_payload)
        full_payload = data_payload + bytes([checksum])
        hex_ascii_payload = binascii.hexlify(full_payload).upper()
        return SOH + hex_ascii_payload + ETB


class FoxtronConnection:
    """Manages the low-level asyncio TCP connection, framing, and keep-alive."""

    def __init__(
        self,
        host: str,
        port: int,
        on_message_callback: Callable[[bytes], Awaitable[None]],
        on_disconnect_callback: Callable[[], Awaitable[None]],
    ):
        self._host = host
        self._port = port
        self._on_message_callback = on_message_callback
        self._on_disconnect_callback = on_disconnect_callback
        keep_alive_payload = bytes([MSG_TYPE_QUERY_CONFIG_ITEM, 0x02])
        self._keep_alive_frame = FoxtronMessage.build_frame(keep_alive_payload)
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._keep_alive_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._reconnect_delay = 1
        self._disconnect_lock = asyncio.Lock()

    async def connect(self):
        if self._is_connected:
            return
        _LOGGER.info(f"Connecting to Foxtron gateway at {self._host}:{self._port}")
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port
            )
            self._is_connected = True
            self._reconnect_delay = 1
            _LOGGER.info("Connection established.")
            self._receive_task = asyncio.create_task(self._read_loop())
            self._keep_alive_task = asyncio.create_task(self._keep_alive_loop())
        except (ConnectionRefusedError, OSError) as e:
            _LOGGER.error(f"Failed to connect to {self._host}:{self._port}: {e}")
            await self._handle_disconnect()

    async def disconnect(self):
        if not self._is_connected:
            return
        _LOGGER.info("Disconnecting from gateway.")
        self._is_connected = False
        for task in [self._keep_alive_task, self._receive_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except ConnectionResetError:
                pass
        self._reader, self._writer, self._receive_task, self._keep_alive_task = (
            None,
            None,
            None,
            None,
        )

    async def send_frame(self, frame: bytes):
        if not self._is_connected or not self._writer:
            raise ConnectionError("Cannot send frame, not connected.")
        _LOGGER.debug(f"Sending frame: {frame!r}")
        self._writer.write(frame)
        await self._writer.drain()

    async def _keep_alive_loop(self):
        while self._is_connected:
            try:
                await asyncio.sleep(KEEP_ALIVE_INTERVAL)
                _LOGGER.debug("Sending keep-alive frame.")
                await self.send_frame(self._keep_alive_frame)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Error in keep-alive loop: {e}")
                await self._handle_disconnect()
                break
        _LOGGER.debug("Keep-alive loop terminated.")

    async def _read_loop(self):
        buffer = b""
        while self._is_connected and self._reader:
            try:
                data = await self._reader.read(1024)
                if not data:
                    _LOGGER.warning("Connection closed by peer.")
                    await self._handle_disconnect()
                    break
                buffer += data
                while ETB in buffer:
                    try:
                        soh_index = buffer.index(SOH)
                        etb_index = buffer.index(ETB)
                    except ValueError:
                        break
                    if etb_index < soh_index:
                        _LOGGER.warning(
                            f"Discarding corrupted buffer part: {buffer[:soh_index]!r}"
                        )
                        buffer = buffer[soh_index:]
                        continue
                    frame_content = buffer[soh_index + 1 : etb_index]
                    asyncio.create_task(self._on_message_callback(frame_content))
                    buffer = buffer[etb_index + 1 :]
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Read loop error: {e}")
                await self._handle_disconnect()
                break
        _LOGGER.debug("Read loop terminated.")

    async def _handle_disconnect(self):
        async with self._disconnect_lock:
            if not self._is_connected:
                return
            await self.disconnect()
            await self._on_disconnect_callback()
            _LOGGER.info(
                f"Will attempt to reconnect in {self._reconnect_delay} seconds."
            )
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(60, self._reconnect_delay * 2)
            asyncio.create_task(self.connect())


# --- Main Driver Class ---
class FoxtronDaliDriver:
    """High-level async driver for the Foxtron DALI2net gateway."""

    def __init__(
        self, host: str, port: int = 23, known_buttons: Optional[List[int]] = None
    ):
        self._connection = FoxtronConnection(
            host, port, self._parse_and_queue_message, self._clear_pending_futures
        )
        self._event_queue: asyncio.Queue[DaliEvent] = asyncio.Queue()
        self._pending_config_queries: Dict[int, asyncio.Future] = {}
        self._pending_dali_queries: Dict[bytes, asyncio.Future] = {}

        # This set now holds all buttons the integration knows about.
        self._known_buttons: set[int] = set(known_buttons or [])

        # This set will hold addresses of NEW buttons seen on the bus.
        self._newly_discovered_buttons: set[int] = set()

    async def connect(self):
        await self._connection.connect()

    async def disconnect(self):
        await self._connection.disconnect()

    async def get_event(self) -> DaliEvent:
        return await self._event_queue.get()

    async def _clear_pending_futures(self):
        for future in self._pending_config_queries.values():
            if not future.done():
                future.set_exception(ConnectionError("Gateway disconnected"))
        self._pending_config_queries.clear()
        for future in self._pending_dali_queries.values():
            if not future.done():
                future.set_exception(ConnectionError("Gateway disconnected"))
        self._pending_dali_queries.clear()

    async def _parse_and_queue_message(self, frame_content: bytes):
        try:
            frame_bytes = binascii.unhexlify(frame_content)
        except binascii.Error:
            _LOGGER.warning(f"Invalid hex content in frame: {frame_content!r}")
            return

        if len(frame_bytes) < 2:
            _LOGGER.warning(f"Frame too short: {frame_bytes.hex().upper()}")
            return

        data_payload = frame_bytes[:-1]
        received_checksum = frame_bytes[-1]
        expected_checksum = FoxtronMessage.calculate_checksum(data_payload)

        if received_checksum != expected_checksum:
            _LOGGER.error(
                f"Checksum mismatch! Payload: {data_payload.hex().upper()}, "
                f"Rcvd: {received_checksum:02X}, Exp: {expected_checksum:02X}"
            )
            return

        msg_type = data_payload[0]
        msg_type_name = MESSAGE_TYPE_NAMES.get(msg_type, "Unknown")
        _LOGGER.debug(
            f"Parsing message type 0x{msg_type:02X} ({msg_type_name}) with payload {data_payload.hex().upper()}"
        )

        event: Optional[DaliEvent] = None
        if msg_type in (MSG_TYPE_DALI_EVENT_WITH_ANSWER, MSG_TYPE_DALI_EVENT_NO_ANSWER):
            event = self._handle_dali_event(data_payload)
        elif msg_type == MSG_TYPE_SPECIAL_GATEWAY_EVENT:
            event = self._handle_special_gateway_event(data_payload)
        elif msg_type == MSG_TYPE_CONFIG_RESPONSE:
            event = self._handle_config_response(data_payload)
        elif msg_type == MSG_TYPE_DALI_RESPONSE_WITH_ANSWER:
            event = self._handle_dali_response(data_payload)
        elif msg_type == MSG_TYPE_CONFIRMATION_NO_ANSWER:
            self._handle_confirmation(data_payload)
        else:
            _LOGGER.warning(f"No handler for message type 0x{msg_type:02X}")

        if event:
            if isinstance(event, DaliInputNotificationEvent):
                # Check if this is a new, unknown button
                if event.address not in self._known_buttons:
                    _LOGGER.info(
                        f"New button discovered at address {event.address}. Adding to discovery cache."
                    )
                    self._newly_discovered_buttons.add(event.address)

            # Still queue the event for real-time automations
            await self._event_queue.put(event)

    def _handle_confirmation(self, data_payload: bytes) -> None:
        _LOGGER.debug("Received confirmation for our sent command.")

    def _handle_dali_response(self, data_payload: bytes) -> Optional[DaliEvent]:
        cmd_len_bits = data_payload[1]
        cmd_len_bytes = (cmd_len_bits + 7) // 8
        dali_cmd_sent = data_payload[3 : 3 + cmd_len_bytes]
        ans_len_bits = data_payload[2]
        ans_len_bytes = (ans_len_bits + 7) // 8
        dali_answer = data_payload[
            3 + cmd_len_bytes : 3 + cmd_len_bytes + ans_len_bytes
        ]

        if len(self._pending_dali_queries) == 1:
            cmd_key, future = next(iter(self._pending_dali_queries.items()))
            self._pending_dali_queries.pop(cmd_key)
            if not future.done():
                result = dali_answer[0] if dali_answer else None
                _LOGGER.debug(
                    f"Resolving pending query for {cmd_key.hex()} with {result} via workaround."
                )
                future.set_result(result)
            return None

        if dali_cmd_sent in self._pending_dali_queries:
            future = self._pending_dali_queries.pop(dali_cmd_sent)
            if not future.done():
                future.set_result(dali_answer[0] if dali_answer else None)
        elif dali_answer:
            return DaliQueryResponseEvent(data_payload, dali_answer[0], dali_answer[1])
        else:
            _LOGGER.debug("Received unsolicited DALI response with no answer data.")
        return None

    def _handle_dali_event(self, data_payload: bytes) -> Optional[DaliEvent]:
        dali_len_bits = data_payload[1]
        dali_len_bytes = (dali_len_bits + 7) // 8
        dali_payload = data_payload[2 : 2 + dali_len_bytes]

        if dali_len_bits == 16 and len(dali_payload) == 2:
            return DaliCommandEvent(dali_payload, dali_payload[0], dali_payload[1])
        elif dali_len_bits == 24 and len(dali_payload) == 3:
            return DaliInputNotificationEvent(dali_payload)
        else:
            return DaliEvent(dali_payload, f"DALI Event ({dali_len_bits}-bit)")

    def _handle_special_gateway_event(self, data_payload: bytes) -> Optional[DaliEvent]:
        return SpecialGatewayEvent(data_payload, data_payload[1])

    def _handle_config_response(self, data_payload: bytes) -> Optional[DaliEvent]:
        item_number = data_payload[1]
        value = int.from_bytes(data_payload[2:4], "big")
        if item_number in self._pending_config_queries:
            future = self._pending_config_queries.pop(item_number)
            if not future.done():
                future.set_result(value)
        return ConfigResponseEvent(data_payload, item_number, value)

    async def _send_dali_frame(self, dali_command: bytes, params: int = 0x00):
        length_in_bits = len(dali_command) * 8
        if not 8 <= length_in_bits <= 64:
            _LOGGER.error(f"Invalid DALI command length: {length_in_bits} bits")
            return

        payload = (
            bytes([MSG_TYPE_SEND_DALI_COMMAND, 0x00, length_in_bits])
            + dali_command
            + bytes([params])
        )
        frame = FoxtronMessage.build_frame(payload)
        await self._connection.send_frame(frame)

    async def send_dali_command(
        self, address_byte: int, opcode_byte: int, send_twice: bool = True
    ):
        """Sends a standard 16-bit DALI command."""
        params = 0x01 if send_twice else 0x00
        dali_command = bytes([address_byte, opcode_byte])
        await self._send_dali_frame(dali_command, params)
        _LOGGER.debug(
            f"Sent DALI command: Address=0x{address_byte:02X}, Opcode=0x{opcode_byte:02X}"
        )

    async def send_dali_query(
        self, address_byte: int, opcode_byte: int, timeout: float = 0.5
    ) -> Optional[int]:
        dali_command = bytes([address_byte, opcode_byte])
        if dali_command in self._pending_dali_queries:
            _LOGGER.warning(f"Query for {dali_command.hex()} already in progress.")
            return None
        future = asyncio.get_running_loop().create_future()
        self._pending_dali_queries[dali_command] = future
        await self._send_dali_frame(dali_command, params=0x00)
        _LOGGER.debug(
            f"Sent DALI Query: Address=0x{address_byte:02X}, Opcode=0x{opcode_byte:02X}"
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except (asyncio.TimeoutError, ConnectionError) as e:
            _LOGGER.debug(f"No response for query {dali_command.hex()}: {e}")
            self._pending_dali_queries.pop(dali_command, None)
            return None

    # Main public API

    async def set_fade_level(self, fade_code: int):
        if not 0 <= fade_code <= 15:
            _LOGGER.error(f"Invalid fade code: {fade_code}. Must be 0-15.")
            return

        # Map from DALI fade code to approximate time in seconds for logging
        fade_time_map = {
            0: 0,
            1: 0.7,
            2: 1.0,
            3: 1.4,
            4: 2.0,
            5: 2.8,
            6: 4.0,
            7: 5.7,
            8: 8.0,
            9: 11.3,
            10: 16.0,
            11: 22.6,
            12: 32.0,
            13: 45.3,
            14: 64.0,
            15: 90.5,
        }
        approx_time = fade_time_map.get(fade_code, "Unknown")

        _LOGGER.debug(f"Setting fade time to code {fade_code} (~{approx_time}s)")
        # DALI command: SET FADE TIME (opcode 0x2F) to all devices (address 0xFF)
        # The fade time value is stored in DTR0, so we send a DTR0 command first.
        await self.send_dali_command(
            DALI_CMD_DTR0, fade_code, send_twice=False
        )  # DTR0 = fade_code
        await asyncio.sleep(0.1)  # Give gateway a moment
        await self.send_dali_command(
            DALI_BROADCAST, DALI_CMD_SET_FADE_TIME, send_twice=False
        )  # SET FADE TIME

    async def set_device_level(self, short_address: int, level: int):
        if not 0 <= short_address <= 63:
            _LOGGER.error(f"Invalid short address: {short_address}. Must be 0-63.")
            return
        if not 0 <= level <= 254:
            _LOGGER.error(f"Invalid DALI level: {level}. Must be 0-254.")
            return

        address_byte = short_address * 2
        opcode_byte = level
        await self.send_dali_command(address_byte, opcode_byte, send_twice=False)

    async def scan_for_devices(self) -> List[int]:
        _LOGGER.info("Starting DALI bus scan for control gear (lights)...")
        found_devices = []
        for addr in range(64):
            address_byte = (addr * 2) + 1
            opcode_byte = DALI_CMD_QUERY_CONTROL_GEAR_PRESENT
            response = await self.send_dali_query(address_byte, opcode_byte)
            if response is not None:
                _LOGGER.debug(f"Found control gear (light) at short address {addr}!")
                found_devices.append(addr)
            await asyncio.sleep(0.1)
        return found_devices

    async def query_actual_level(self, short_address: int) -> Optional[int]:
        if not 0 <= short_address <= 63:
            _LOGGER.error(f"Invalid short address: {short_address}. Must be 0-63.")
            return None
        address_byte = (short_address * 2) + 1
        opcode_byte = DALI_CMD_QUERY_ACTUAL_LEVEL
        return await self.send_dali_query(address_byte, opcode_byte)

    async def query_config_item(
        self, item_number: int, timeout: float = 5.0
    ) -> Optional[int]:
        if item_number in self._pending_config_queries:
            _LOGGER.warning(f"Query for item {item_number} already in progress.")
            return await self._pending_config_queries[item_number]
        future = asyncio.get_running_loop().create_future()
        self._pending_config_queries[item_number] = future
        payload = bytes([MSG_TYPE_QUERY_CONFIG_ITEM, item_number])
        frame = FoxtronMessage.build_frame(payload)
        await self._connection.send_frame(frame)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except (asyncio.TimeoutError, ConnectionError) as e:
            _LOGGER.error(f"Failed to get config response for item {item_number}: {e}")
            self._pending_config_queries.pop(item_number, None)
            return None

    async def query_firmware_version(self) -> Optional[str]:
        raw_version = await self.query_config_item(2)
        if raw_version is not None:
            return f"{raw_version >> 8}.{raw_version & 0xFF}"
        return None

    def get_newly_discovered_buttons(self) -> List[int]:
        """
        Returns a list of button addresses seen on the bus that are not yet
        part of the known devices list.
        """
        return sorted(list(self._newly_discovered_buttons))

    def clear_newly_discovered_buttons(self):
        """Clears the cache of newly discovered buttons."""
        self._newly_discovered_buttons.clear()

    def add_known_button(self, address: int):
        """
        Adds a button address to the set of known devices.
        This should be called by the integration after the user has configured it.
        """
        self._known_buttons.add(address)
        # Remove it from the 'new' set if it's there
        self._newly_discovered_buttons.discard(address)