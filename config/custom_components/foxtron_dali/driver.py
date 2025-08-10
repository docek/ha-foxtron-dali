"""
This module provides a high-level asynchronous driver for communicating
with Foxtron DALI to Ethernet gateways (DALInet, DALI2net) using their
proprietary ASCII/TCP protocol.

It handles:
- Low-level TCP connection management, including automatic reconnection.
- Protocol framing (SOH, ETB, checksums).
- Parsing of incoming messages into structured DaliEvent objects.
- Sending DALI commands and queries.
- A keep-alive mechanism to maintain the TCP connection.
- Discovery of DALI control gear (lights) and input devices (buttons).
"""

import asyncio
import binascii
import logging
from typing import Awaitable, Callable, Dict, List, Optional

# --- Basic Logging Setup ---
_LOGGER = logging.getLogger(__name__)

# --- Protocol Constants ---
SOH = b"\x01"  # Start of Heading
ETB = b"\x17"  # End of Transmission Block
KEEP_ALIVE_INTERVAL = 20  # Seconds to send keep-alive to prevent TCP timeout

# --- Foxtron Message Types (Protocol Command Byte) ---
# See protocol_spec.md for a detailed description of each message type.

# Received from Gateway (Converter -> Master)
MSG_TYPE_DALI_EVENT_WITH_ANSWER = 0x03  # Spontaneous event from another master w/ DALI answer
MSG_TYPE_DALI_EVENT_NO_ANSWER = 0x04    # Spontaneous event (e.g., button press) w/o DALI answer
MSG_TYPE_SPECIAL_GATEWAY_EVENT = 0x05   # Gateway status message (e.g., power loss)
MSG_TYPE_CONFIG_RESPONSE = 0x07         # Response to a config query (Type 0x06)
MSG_TYPE_DALI_RESPONSE_WITH_ANSWER = 0x0D # Differentiated response to our query w/ DALI answer
MSG_TYPE_CONFIRMATION_NO_ANSWER = 0x0E    # Differentiated confirmation for our command

# Sent to Gateway (Master -> Converter)
MSG_TYPE_QUERY_CONFIG_ITEM = 0x06       # Query a configuration value from the gateway
MSG_TYPE_SEND_DALI_COMMAND = 0x0B         # Send a DALI frame (recommended method)


# --- DALI-2 Input Notification Event Codes (IEC 62386-301) ---
# These codes are received in the third byte of a 24-bit Input Notification frame.
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
DALI_CMD_QUERY_DEVICE_TYPE = 0xFC

# --- DALI Addressing ---
DALI_BROADCAST = 0xFF

# --- Mappings for Readable Logs ---
# These dictionaries provide human-readable names for logging purposes.
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
    """Base class for a parsed event from the DALI bus.

    Attributes:
        raw_payload (bytes): The raw binary data payload of the event.
        description (str): A human-readable description of the event.
    """

    def __init__(self, raw_payload: bytes, description: str = "Generic DALI Event"):
        """Initializes a DaliEvent."""
        self.raw_payload = raw_payload
        self.description = description

    def __repr__(self):
        """Return a string representation of the event."""
        return f"{self.__class__.__name__}(desc='{self.description}', raw='{self.raw_payload.hex().upper()}')"


class DaliCommandEvent(DaliEvent):
    """Represents a standard 16-bit DALI command observed on the bus.

    Attributes:
        address_byte (int): The first byte of the DALI command (addressing).
        opcode_byte (int): The second byte of the DALI command (opcode).
    """

    def __init__(self, raw_payload: bytes, address_byte: int, opcode_byte: int):
        """Initializes a DaliCommandEvent."""
        super().__init__(raw_payload, "DALI Command")
        self.address_byte = address_byte
        self.opcode_byte = opcode_byte

    def __repr__(self):
        """Return a string representation of the command event."""
        return (
            f"DaliCommandEvent(address=0x{self.address_byte:02X}, "
            f"opcode=0x{self.opcode_byte:02X})"
        )


class DaliInputNotificationEvent(DaliEvent):
    """Represents a 24-bit DALI-2 Input Notification event (e.g., from a button).

    Parses the complex addressing scheme defined in IEC 62386-301.

    Attributes:
        instance_number (int): The instance number of the input device.
        event_code (int): The DALI-2 event code (e.g., button pressed, released).
        address_type (str): The type of addressing used ('Short', 'Group', 'Broadcast').
        address (Optional[int]): The short or group address, if applicable.
    """

    def __init__(self, raw_payload: bytes):
        """Initializes and parses a DaliInputNotificationEvent."""
        super().__init__(raw_payload, "DALI-2 Input Notification")
        # Unpack the 24-bit (3-byte) payload
        addressing_byte = raw_payload[0]
        self.instance_number = raw_payload[1]
        self.event_code = raw_payload[2]

        self.address_type: str
        self.address: Optional[int]

        # Decode the addressing byte according to DALI-2 spec
        if (addressing_byte >> 7) == 0 and (addressing_byte & 0x01) == 0:
            # Bit 7 is 0 and LSB is 0: Short Address
            # Per IEC 62386-301 the 6 MSBs (bits 6-1) encode the address
            self.address_type = "Short"
            self.address = addressing_byte >> 1
        elif (addressing_byte >> 6) == 0b10 and (addressing_byte & 0x01) == 0:
            # Bits 7-6 are 10 and LSB is 0: Group Address
            # Bits 4-1 contain the group number
            self.address_type = "Group"
            self.address = (addressing_byte >> 1) & 0x0F
        elif addressing_byte == 0xFF:
            # All bits 1: Broadcast
            self.address_type = "Broadcast"
            self.address = None
        else:
            # Should not happen with compliant devices
            self.address_type = "Unknown"
            self.address = None

    def __repr__(self):
        """Return a string representation of the input notification event."""
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
    """Represents a Type 5 special message from the gateway itself.

    These events indicate the status of the gateway or the DALI bus power.

    Attributes:
        event_code (int): The specific code for the gateway event.
    """

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
        """Initializes a SpecialGatewayEvent."""
        description = self.EVENT_MAP.get(event_code, "Unknown Special Event")
        super().__init__(raw_payload, description)
        self.event_code = event_code

    def __repr__(self):
        """Return a string representation of the special gateway event."""
        return f"SpecialGatewayEvent(code={self.event_code}, desc='{self.description}')"


class ConfigResponseEvent(DaliEvent):
    """Represents a Type 7 configuration response from the gateway.

    This is sent in response to a Type 0x06 query.

    Attributes:
        item_number (int): The configuration item number that was queried.
        value (int): The 16-bit value of the configuration item.
    """

    def __init__(self, raw_payload: bytes, item_number: int, value: int):
        """Initializes a ConfigResponseEvent."""
        super().__init__(raw_payload, f"Config Response for Item {item_number}")
        self.item_number = item_number
        self.value = value

    def __repr__(self):
        """Return a string representation of the config response event."""
        return f"ConfigResponseEvent(item={self.item_number}, value={self.value})"


class DaliQueryResponseEvent(DaliEvent):
    """Represents a DALI backward frame (response) to a query.

    Attributes:
        address (int): The short address of the device that responded.
        value (int): The 8-bit response value.
    """

    def __init__(self, raw_payload: bytes, address: int, value: int):
        """Initializes a DaliQueryResponseEvent."""
        super().__init__(raw_payload, f"DALI Query Response for Address {address}")
        self.address = address
        self.value = value

    def __repr__(self):
        """Return a string representation of the query response event."""
        return f"DaliQueryResponseEvent(address={self.address}, value={self.value})"


# --- Low-Level Connection and Parsing ---


class FoxtronMessage:
    """A helper class for building and parsing Foxtron protocol frames.

    This class handles the checksum calculation and the ASCII hex encoding/decoding.
    """

    @staticmethod
    def calculate_checksum(data_payload: bytes) -> int:
        """Calculates the 1-byte checksum for a given data payload.

        The checksum is the bitwise NOT of the modulo-256 sum of the payload bytes.
        """
        return (~sum(data_payload)) & 0xFF

    @staticmethod
    def build_frame(data_payload: bytes) -> bytes:
        """Builds a complete, sendable Foxtron frame.

        Args:
            data_payload: The raw binary payload to send.

        Returns:
            A bytes object ready to be sent over the TCP connection, including
            SOH, ASCII-hex encoded payload and checksum, and ETB.
        """
        checksum = FoxtronMessage.calculate_checksum(data_payload)
        full_payload = data_payload + bytes([checksum])
        # Convert the binary payload and checksum to an ASCII hex string (e.g., b'\xDE\xAD' -> b'DEAD')
        hex_ascii_payload = binascii.hexlify(full_payload).upper()
        return SOH + hex_ascii_payload + ETB


class FoxtronConnection:
    """Manages the low-level asyncio TCP connection, framing, and keep-alive.

    This class is responsible for:
    - Establishing and maintaining the TCP connection.
    - Automatically reconnecting with exponential backoff if the connection is lost.
    - Running a read loop to receive data and identify complete frames (SOH...ETB).
    - Running a keep-alive loop to prevent the gateway from closing the connection.
    - Calling back to the main driver when a complete message is received or when
      the connection is lost.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_message_callback: Callable[[bytes], Awaitable[None]],
        on_disconnect_callback: Callable[[], Awaitable[None]],
    ):
        """Initializes the FoxtronConnection."""
        self._host = host
        self._port = port
        self._on_message_callback = on_message_callback
        self._on_disconnect_callback = on_disconnect_callback
        # Pre-build the keep-alive frame (a query for firmware version)
        keep_alive_payload = bytes([MSG_TYPE_QUERY_CONFIG_ITEM, 0x02])
        self._keep_alive_frame = FoxtronMessage.build_frame(keep_alive_payload)
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._keep_alive_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._reconnect_delay = 1  # Initial reconnect delay in seconds
        self._disconnect_lock = asyncio.Lock()

    async def connect(self):
        """Establishes a connection to the gateway and starts background tasks."""
        if self._is_connected:
            return
        _LOGGER.info(f"Connecting to Foxtron gateway at {self._host}:{self._port}")
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port
            )
            self._is_connected = True
            self._reconnect_delay = 1  # Reset reconnect delay on successful connection
            _LOGGER.info("Connection established.")
            # Start the background tasks for reading and sending keep-alives
            self._receive_task = asyncio.create_task(self._read_loop())
            self._keep_alive_task = asyncio.create_task(self._keep_alive_loop())
        except (ConnectionRefusedError, OSError) as e:
            _LOGGER.error(f"Failed to connect to {self._host}:{self._port}: {e}")
            await self._handle_disconnect()

    async def disconnect(self):
        """Gracefully disconnects from the gateway and cleans up tasks."""
        if not self._is_connected:
            return
        _LOGGER.info("Disconnecting from gateway.")
        self._is_connected = False
        # Cancel and await the background tasks
        for task in [self._keep_alive_task, self._receive_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        # Close the stream writer
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except ConnectionResetError:
                pass  # Ignore reset error during intentional disconnect
        self._reader, self._writer, self._receive_task, self._keep_alive_task = (
            None,
            None,
            None,
            None,
        )

    async def send_frame(self, frame: bytes):
        """Sends a pre-built frame to the gateway.

        Args:
            frame: The complete binary frame to send (including SOH and ETB).

        Raises:
            ConnectionError: If the connection is not active.
        """
        if not self._is_connected or not self._writer:
            raise ConnectionError("Cannot send frame, not connected.")
        _LOGGER.debug(f"Sending frame: {frame!r}")
        self._writer.write(frame)
        await self._writer.drain()

    async def _keep_alive_loop(self):
        """Periodically sends a keep-alive frame to maintain the connection."""
        while self._is_connected:
            try:
                await asyncio.sleep(KEEP_ALIVE_INTERVAL)
                _LOGGER.debug("Sending keep-alive frame.")
                await self.send_frame(self._keep_alive_frame)
            except asyncio.CancelledError:
                break  # Task was cancelled, exit loop
            except Exception as e:
                _LOGGER.error(f"Error in keep-alive loop: {e}")
                await self._handle_disconnect()
                break
        _LOGGER.debug("Keep-alive loop terminated.")

    async def _read_loop(self):
        """Continuously reads from the TCP socket and processes incoming frames."""
        buffer = b""
        while self._is_connected and self._reader:
            try:
                # Read a chunk of data
                data = await self._reader.read(1024)
                if not data:
                    _LOGGER.warning("Connection closed by peer.")
                    await self._handle_disconnect()
                    break

                buffer += data
                # Process all complete frames (SOH...ETB) in the buffer
                while ETB in buffer:
                    try:
                        soh_index = buffer.index(SOH)
                        etb_index = buffer.index(ETB)
                    except ValueError:
                        # No complete frame found, wait for more data
                        break

                    # Handle corrupted buffer where ETB might appear before SOH
                    if etb_index < soh_index:
                        _LOGGER.warning(
                            f"Discarding corrupted buffer part: {buffer[:soh_index]!r}"
                        )
                        buffer = buffer[soh_index:]
                        continue

                    # Extract the ASCII-hex content of the frame
                    frame_content = buffer[soh_index + 1 : etb_index]
                    # Pass the frame content to the main driver for parsing
                    asyncio.create_task(self._on_message_callback(frame_content))
                    # Remove the processed frame from the buffer
                    buffer = buffer[etb_index + 1 :]
            except asyncio.CancelledError:
                break  # Task was cancelled, exit loop
            except Exception as e:
                _LOGGER.error(f"Read loop error: {e}")
                await self._handle_disconnect()
                break
        _LOGGER.debug("Read loop terminated.")

    async def _handle_disconnect(self):
        """Handles the disconnection logic, including initiating a reconnect."""
        async with self._disconnect_lock:
            if not self._is_connected:
                return  # Already handling disconnect
            await self.disconnect()
            await self._on_disconnect_callback()
            _LOGGER.info(
                f"Will attempt to reconnect in {self._reconnect_delay} seconds."
            )
            await asyncio.sleep(self._reconnect_delay)
            # Implement exponential backoff for reconnect attempts
            self._reconnect_delay = min(60, self._reconnect_delay * 2)
            asyncio.create_task(self.connect())


# --- Main Driver Class ---
class FoxtronDaliDriver:
    """High-level async driver for the Foxtron DALI2net gateway.

    This class orchestrates the connection, message parsing, and command sending.
    It provides a simplified public API for interacting with the DALI bus.
    """

    def __init__(
        self, host: str, port: int = 23, known_buttons: Optional[List[int]] = None
    ):
        """Initializes the FoxtronDaliDriver.

        Args:
            host: The IP address of the Foxtron gateway.
            port: The TCP port for the specific DALI bus (23 or 24).
            known_buttons: An optional list of known button short addresses to
                           prevent them from being logged as "newly discovered".
        """
        self._connection = FoxtronConnection(
            host, port, self._parse_and_queue_message, self._clear_pending_futures
        )
        self._event_queue: asyncio.Queue[DaliEvent] = asyncio.Queue()
        self._pending_config_queries: Dict[int, asyncio.Future] = {}
        self._pending_dali_queries: Dict[bytes, asyncio.Future] = {}
        self._query_lock = asyncio.Lock()

        # This set holds all buttons the integration knows about.
        self._known_buttons: set[int] = set(known_buttons or [])

        # This set will hold addresses of NEW buttons seen on the bus.
        self._newly_discovered_buttons: set[int] = set()

    async def connect(self):
        """Connects to the gateway."""
        await self._connection.connect()

    async def disconnect(self):
        """Disconnects from the gateway."""
        await self._connection.disconnect()

    async def get_event(self) -> DaliEvent:
        """Retrieves the next event from the incoming event queue.

        This method will block until an event is available.

        Returns:
            A DaliEvent object representing the received event.
        """
        return await self._event_queue.get()

    async def _clear_pending_futures(self):
        """Clears all pending futures when a disconnection occurs."""
        for future in self._pending_config_queries.values():
            if not future.done():
                future.set_exception(ConnectionError("Gateway disconnected"))
        self._pending_config_queries.clear()
        for future in self._pending_dali_queries.values():
            if not future.done():
                future.set_exception(ConnectionError("Gateway disconnected"))
        self._pending_dali_queries.clear()

    async def _parse_and_queue_message(self, frame_content: bytes):
        """Parses a raw frame from the connection and queues it as a DaliEvent.

        This method is the callback passed to FoxtronConnection.

        Args:
            frame_content: The ASCII-hex content of the received frame.
        """
        try:
            # Convert ASCII hex to binary
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

        # Validate the checksum
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

        # Route the payload to the appropriate handler based on message type
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
            # If a new button is discovered, add it to the discovery set
            if isinstance(event, DaliInputNotificationEvent):
                if event.address is not None and event.address not in self._known_buttons:
                    _LOGGER.info(
                        f"New button discovered at address {event.address}. Adding to discovery cache."
                    )
                    self._newly_discovered_buttons.add(event.address)

            # Add the parsed event to the queue for the application to process
            await self._event_queue.put(event)

    def _handle_confirmation(self, data_payload: bytes) -> None:
        """Handles a Type 0x0E confirmation message."""
        _LOGGER.debug("Received confirmation for our sent command.")

    def _handle_dali_response(self, data_payload: bytes) -> Optional[DaliEvent]:
        """Handles a Type 0x0D DALI response message.

        This resolves the future associated with a pending DALI query.
        """
        # The Foxtron protocol includes the command we sent in the response,
        # which is used to match it to the correct pending future.
        cmd_len_bits = data_payload[1]
        cmd_len_bytes = (cmd_len_bits + 7) // 8
        dali_cmd_sent = data_payload[3 : 3 + cmd_len_bytes]

        ans_len_bits = data_payload[2]
        ans_len_bytes = (ans_len_bits + 7) // 8
        dali_answer = data_payload[
            3 + cmd_len_bytes : 3 + cmd_len_bytes + ans_len_bytes
        ]

        # Workaround for a gateway firmware quirk where the sent command is not echoed back
        # when only one query is in flight.
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

        # Standard handling: find the matching future and set its result
        if dali_cmd_sent in self._pending_dali_queries:
            future = self._pending_dali_queries.pop(dali_cmd_sent)
            if not future.done():
                future.set_result(dali_answer[0] if dali_answer else None)
        elif dali_answer:
            # This is an unsolicited response, likely from another DALI master.
            # We create an event but don't know the source address.
            _LOGGER.debug(
                f"Received unsolicited DALI query response with value: {dali_answer.hex().upper()}"
            )
            return DaliQueryResponseEvent(data_payload, -1, dali_answer[0])
        else:
            _LOGGER.debug("Received unsolicited DALI response with no answer data.")
        return None

    def _handle_dali_event(self, data_payload: bytes) -> Optional[DaliEvent]:
        """Handles Type 0x03 and 0x04 spontaneous DALI events."""
        dali_len_bits = data_payload[1]
        dali_len_bytes = (dali_len_bits + 7) // 8
        dali_payload = data_payload[2 : 2 + dali_len_bytes]

        if dali_len_bits == 16 and len(dali_payload) == 2:
            return DaliCommandEvent(dali_payload, dali_payload[0], dali_payload[1])
        elif dali_len_bits == 24 and len(dali_payload) == 3:
            return DaliInputNotificationEvent(dali_payload)
        else:
            # Generic event for non-standard frame lengths
            return DaliEvent(dali_payload, f"DALI Event ({dali_len_bits}-bit)")

    def _handle_special_gateway_event(self, data_payload: bytes) -> Optional[DaliEvent]:
        """Handles a Type 0x05 special gateway event."""
        return SpecialGatewayEvent(data_payload, data_payload[1])

    def _handle_config_response(self, data_payload: bytes) -> Optional[DaliEvent]:
        """Handles a Type 0x07 configuration response."""
        item_number = data_payload[1]
        value = int.from_bytes(data_payload[2:4], "big")
        # Resolve the future for the pending config query
        if item_number in self._pending_config_queries:
            future = self._pending_config_queries.pop(item_number)
            if not future.done():
                future.set_result(value)
        # Also return an event object for logging/monitoring
        return ConfigResponseEvent(data_payload, item_number, value)

    async def _send_dali_frame(self, dali_command: bytes, params: int = 0x00):
        """Constructs and sends a Type 0x0B message to the gateway."""
        length_in_bits = len(dali_command) * 8
        if not 8 <= length_in_bits <= 64:
            _LOGGER.error(f"Invalid DALI command length: {length_in_bits} bits")
            return

        # Construct the binary payload for the Foxtron frame
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
        """Sends a standard 16-bit DALI command.

        Args:
            address_byte: The DALI addressing byte.
            opcode_byte: The DALI command opcode.
            send_twice: If True, instructs the gateway to send the command twice,
                        as required by the DALI standard for many commands.
        """
        params = 0x01 if send_twice else 0x00
        dali_command = bytes([address_byte, opcode_byte])
        await self._send_dali_frame(dali_command, params)
        _LOGGER.debug(
            f"Sent DALI command: Address=0x{address_byte:02X}, Opcode=0x{opcode_byte:02X}"
        )

    async def send_dali_query(
        self, address_byte: int, opcode_byte: int, timeout: float = 0.5
    ) -> Optional[int]:
        """Sends a DALI query and waits for a response.

        Args:
            address_byte: The DALI addressing byte for the query.
            opcode_byte: The DALI query opcode.
            timeout: The maximum time to wait for a response in seconds.

        Returns:
            The 8-bit integer response from the DALI device, or None if no
            response was received within the timeout.
        """
        dali_command = bytes([address_byte, opcode_byte])
        if dali_command in self._pending_dali_queries:
            _LOGGER.warning(f"Query for {dali_command.hex()} already in progress.")
            return None

        async with self._query_lock:
            # Create a future to await the response
            future = asyncio.get_running_loop().create_future()
            self._pending_dali_queries[dali_command] = future

            # Send the query frame
            await self._send_dali_frame(dali_command, params=0x00)
            _LOGGER.debug(
                f"Sent DALI Query: Address=0x{address_byte:02X}, Opcode=0x{opcode_byte:02X}"
            )

            # Wait for the future to be resolved by the response handler
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except (asyncio.TimeoutError, ConnectionError) as e:
                _LOGGER.debug(f"No response for query {dali_command.hex()}: {e}")
                # Clean up the pending query if it timed out
                self._pending_dali_queries.pop(dali_command, None)
                return None

    # -------------------------------------------------------------------
    # --- Main Public API for DALI Operations ---
    # -------------------------------------------------------------------

    async def set_fade_time(self, fade_code: int):
        """Sets the DALI fade time for all devices on the bus.

        Args:
            fade_code: A DALI fade code (0-15).
        """
        if not 0 <= fade_code <= 15:
            _LOGGER.error(f"Invalid fade code: {fade_code}. Must be 0-15.")
            return

        fade_time_map = {
            0: 0, 1: 0.7, 2: 1.0, 3: 1.4, 4: 2.0, 5: 2.8, 6: 4.0, 7: 5.7,
            8: 8.0, 9: 11.3, 10: 16.0, 11: 22.6, 12: 32.0, 13: 45.3, 14: 64.0, 15: 90.5,
        }
        approx_time = fade_time_map.get(fade_code, "Unknown")
        _LOGGER.debug(f"Setting fade time to code {fade_code} (~{approx_time}s)")

        # Per DALI spec, fade time is set by loading a value into DTR0
        # and then sending the SET FADE TIME command.
        await self.send_dali_command(DALI_BROADCAST, DALI_CMD_DTR0, send_twice=False)
        await asyncio.sleep(0.1)  # Small delay for gateway processing
        await self.send_dali_command(
            DALI_BROADCAST, DALI_CMD_SET_FADE_TIME, send_twice=False
        )

    async def broadcast_off(self):
        """Turns off all lights on the DALI bus via broadcast."""
        _LOGGER.debug("Broadcasting OFF command to all devices")
        await self.send_dali_command(DALI_BROADCAST, DALI_CMD_OFF, send_twice=False)

    async def broadcast_on(self):
        """Turns on all lights on the DALI bus to their maximum level via broadcast."""
        _LOGGER.debug("Broadcasting RECALL_MAX_LEVEL command to all devices")
        await self.send_dali_command(DALI_BROADCAST, DALI_CMD_RECALL_MAX_LEVEL, send_twice=False)

    async def set_device_level(self, short_address: int, level: int):
        """Sets the brightness level of a single DALI device.

        Args:
            short_address: The short address of the light (0-63).
            level: The DALI brightness level (0-254). 0 is off.
        """
        if not 0 <= short_address <= 63:
            _LOGGER.error(f"Invalid short address: {short_address}. Must be 0-63.")
            return
        if not 0 <= level <= 254:
            _LOGGER.error(f"Invalid DALI level: {level}. Must be 0-254.")
            return

        # Direct Arc Power Control (DAPC) command
        address_byte = short_address * 2
        opcode_byte = level
        await self.send_dali_command(address_byte, opcode_byte, send_twice=False)

    async def scan_for_devices(self) -> List[int]:
        """Scans the DALI bus for control gear (lights).

        Returns:
            A list of short addresses (0-63) of all discovered lights.
        """
        _LOGGER.info("Starting DALI bus scan for control gear (lights)...")
        found_devices = []
        for addr in range(64):
            # Address for a query is (short_address * 2) + 1
            address_byte = (addr * 2) + 1
            opcode_byte = DALI_CMD_QUERY_CONTROL_GEAR_PRESENT
            response = await self.send_dali_query(address_byte, opcode_byte)
            if response is not None:  # YES response means a device is present
                _LOGGER.debug(f"Found control gear (light) at short address {addr}!")
                found_devices.append(addr)
            await asyncio.sleep(0.1)  # Avoid flooding the bus
        return found_devices

    async def scan_for_input_devices(self) -> List[int]:
        """Scans the DALI bus for input devices (e.g., buttons).

        Returns:
            A list of short addresses of discovered input devices.
        """

        _LOGGER.info("Starting DALI bus scan for input devices (buttons)...")
        found_devices: List[int] = []

        for addr in range(64):
            address_byte = (addr * 2) + 1

            # Skip control gear that respond to QUERY CONTROL GEAR PRESENT
            gear_present = await self.send_dali_query(
                address_byte, DALI_CMD_QUERY_CONTROL_GEAR_PRESENT
            )
            if gear_present is not None:
                await asyncio.sleep(0.1)
                continue

            response = await self.send_dali_query(address_byte, DALI_CMD_QUERY_DEVICE_TYPE)
            if response is not None:
                _LOGGER.debug(
                    f"Found input device at short address {addr} (device type 0x{response:02X})"
                )
                found_devices.append(addr)
                if addr not in self._known_buttons:
                    self._newly_discovered_buttons.add(addr)

            await asyncio.sleep(0.1)

        return found_devices

    async def query_actual_level(self, short_address: int) -> Optional[int]:
        """Queries the current brightness level of a light.

        Args:
            short_address: The short address of the light (0-63).

        Returns:
            The current DALI level (0-254), or None if no response.
        """
        if not 0 <= short_address <= 63:
            _LOGGER.error(f"Invalid short address: {short_address}. Must be 0-63.")
            return None
        address_byte = (short_address * 2) + 1
        opcode_byte = DALI_CMD_QUERY_ACTUAL_LEVEL
        return await self.send_dali_query(address_byte, opcode_byte)

    async def query_config_item(
        self, item_number: int, timeout: float = 5.0
    ) -> Optional[int]:
        """Queries a configuration value from the gateway itself.

        Args:
            item_number: The number of the configuration item to query.
            timeout: The maximum time to wait for a response.

        Returns:
            The 16-bit integer value of the config item, or None on timeout.
        """
        if item_number in self._pending_config_queries:
            _LOGGER.warning(f"Query for item {item_number} already in progress.")
            return await self._pending_config_queries[item_number]

        async with self._query_lock:
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
        """Queries the firmware version of the gateway.

        Returns:
            A string representation of the firmware version (e.g., "4.6"),
            or None if the query fails.
        """
        raw_version = await self.query_config_item(2)
        if raw_version is not None:
            # The version is returned as a 16-bit integer.
            # The upper byte is the major version, lower byte is the minor.
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
