# Foxtron DALInet/DALI2net Proprietary Communication Protocol

This document provides a comprehensive technical breakdown of the proprietary ASCII-based communication protocol used by the Foxtron DALInet (single DALI bus gateway) and DALI2net (dual DALI bus gateway). The protocol operates over TCP/IP for these Ethernet-based devices but is derived from the RS232 protocol used in DALI232/DALI232e converters. The information is synthesized from the official Foxtron documentation, including "DALI232-komunikacni-protokol.pdf" (Czech, v1.10), "DALI232-communication-protocol.pdf" (English, v1.7), and the user manuals for DALInet, DALI2net, and DALI4SW.

## 1. Core Concepts

### 1.1. Transport & Connection

The protocol is layered on top of standard TCP/IP, with the gateway acting as the server.

* **Protocol:** TCP/IP (persistent connection recommended).
* **Gateway Role:** TCP Server.
* **Default IP Address:** `192.168.1.241` (configurable via web interface). The DALInet and DALI2net gateways are configurable through the web server on its IP address (i.e. `http://192.168.1.241/INDEX.HTM`) and more detail settings on `http://192.168.1.241/AINDEX.HTM`.
* **Port Configuration:**
    * **DALInet (Single Bus):** DALI Bus 1 on port `23`.
    * **DALI2net (Dual Bus):** DALI Bus 1 on port `23`; DALI Bus 2 on port `24`. Each bus requires a separate TCP connection.
* **Connection Handling:** The gateway closes the TCP connection after approximately 30 seconds of inactivity. To maintain a persistent connection, a keep-alive message must be sent periodically (e.g., a `Type 0x06` query for firmware version every 20-25 seconds).

### 1.2. Message Framing

Every message is an ASCII string encapsulated by specific control characters.

* **Start of Header (SOH):** Every message begins with the binary `SOH` character (`0x01`).
* **End of Transmission Block (ETB):** Every message terminates with the binary `ETB` character (`0x17`).

**Structure:** `[SOH (binary)]` + `ASCII-Encoded Payload` + `ASCII-Encoded Checksum` + `[ETB (binary)]`

### 1.3. Payload Encoding

All binary data (payload and checksum) is encoded into a human-readable ASCII-hexadecimal format (uppercase `0-9`, `A-F`). Each byte of data is converted into a two-character ASCII string.

* **Example:** The binary byte `0xB4` is transmitted as the two ASCII characters `'B'` and `'4'`.

### 1.4. Checksum Calculation

A 1-byte checksum is required for all messages.

* **Algorithm:** The checksum is the bitwise NOT of the 8-bit sum (modulo 256) of all raw binary data bytes in the payload.
* **Formula:** `Checksum = ~ (SUM(payload_bytes) % 256)`
* **Example Calculation:**
    * **Payload Bytes (Binary):** `[0x0B, 0x00, 0x10, 0xFF, 0x90, 0x00]`
    * **Sum:** `11 + 0 + 16 + 255 + 144 + 0 = 426`
    * **Modulo 256:** `426 % 256 = 170` (`0xAA`)
    * **Bitwise NOT:** `~0xAA = 0x55`
    * **Checksum Byte:** `0x55` (Encoded as ASCII `"55"`)

## 2. Message Catalog

The first byte of the decoded binary payload determines the message type.

### Master -> Converter (Commands)

| Type (Hex) | Message Name | Payload Structure (Binary Bytes) | Description |
| :--- | :--- | :--- | :--- |
| `0x0B` | Send w/ Differentiation | `[1:Cmd][1:Prio][1:Len][1-8:DALI Msg][1:Param]` | **Recommended send command.** Allows the gateway to distinguish between a direct response to this command (Type 0x0D/0x0E) and a spontaneous event on the bus (Type 0x03/0x04). This is crucial for reliable query operations. `Param` Bit 0=Send Twice, Bit 1=Sequence Flag. |
| `0x01` | Send Message | `[1:Cmd][1:Prio][1:Len][1-8:DALI Msg]` | Legacy send command. **Discouraged.** Responses to queries sent with this command are indistinguishable from spontaneous bus events, making response handling ambiguous. |
| `0x06` | Config Query | `[1:Cmd][1:ItemNumber]` | Reads an internal parameter from the gateway. |
| `0x08` | Change Config | `[1:Cmd][1:ItemNumber][2:Data]` | Writes a value to a gateway parameter. |
| `0x0A` | Sequence End | `[1:Cmd][1:Info]` | Service message to terminate a command sequence. `Info` should be `0x00`. |
| `0x0C` | Continuous Send | `[1:Cmd][1:Prio][1:Len][1-8:DALI Msg]` | Service message to send commands without standard DALI timing. Use with caution. |
| `0xFE` | Firmware Overwrite | `[1:Cmd][Var:Data]` | Service message for firmware updates. |

### Converter -> Master (Responses & Events)

| Type (Hex) | Message Name | Payload Structure (Binary Bytes) | Description |
| :--- | :--- | :--- | :--- |
| `0x0D` | Receive w/ Answer (Diff.) | `[1:Cmd][1:Len][1:AnsLen][1-8:DALI Msg][0-1:DALI Ans]` | **Confirmation for a `Type 0x0B` query.** Contains the DALI response. `AnsLen=0` indicates collision. |
| `0x0E` | Receive w/o Answer (Diff.) | `[1:Cmd][1:Len][1-8:DALI Msg]` | **Confirmation for a `Type 0x0B` command** that expected no DALI response. |
| `0x03` | Receive w/ Answer (Spont.) | `[1:Cmd][1:Len][1:AnsLen][1-8:DALI Msg][0-1:DALI Ans]` | Reports an event from another master on the bus that received a DALI reply. |
| `0x04` | Receive w/o Answer (Spont.) | `[1:Cmd][1:Len][0-8:Data]` | **Primary mechanism for button events.** Reports a DALI frame from another master that did not get a reply. `Len=0` indicates a bus framing error. |
| `0x05` | Special Event | `[1:Cmd][1:TypeCode]` | Reports gateway/bus status. See codes in Section 4. |
| `0x07` | Config Response | `[1:Cmd][1:ItemNumber][2:Data]` | The response to a `Type 0x06` query. |
| `0x09` | Config Ack | `[1:Cmd][1:ItemNumber][2:Data][1:StatusCode]` | Confirms a `Type 0x08` command. Status: `0`=OK, `1`=Read-only, `2`=Out of range. |
| `0xFF` | Firmware Ack | `[1:Cmd][1:ErrorCode]` | Service message confirming firmware data line. |

## 3. DALI-2 Input Notification Events (Buttons)

The DALI4SW button and other DALI-2 input devices send standard "Input Notification" frames (24-bit). The gateway forwards these as `Type 0x04` messages.

**Frame Format:** `YAAAAAAA IIIIIIII EEEEEEEE`
*   `YAAAAAAA`: The addressing byte, which defines the source of the event.
    *   `0AAAAAAS`: **Short Address.** `S=0`. `AAAAAA` is the 6-bit short address (0-63).
    *   `1000GGGS`: **Group Address.** `S=0`. `GGG` is the 4-bit group address (0-15).
    *   `11111111`: **Broadcast.**
*   `IIIIIIII`: The 8-bit instance number (e.g., 0-3 for the DALI4SW, corresponding to SW1-SW4).
*   `EEEEEEEE`: The 8-bit event code, as defined by the DALI-2 standard (IEC 62386-301).

| Event Name | Event Code (Hex) | Description |
| :--- | :--- | :--- |
| `Button pressed` | `0x00` | The button has just been pressed down. |
| `Button released` | `0x01` | The button has just been released. |
| `Short press` | `0x02` | Press/release cycle shorter than "Short timer". |
| `Double press` | `0x03` | Two short presses within "Double timer". |
| `Long press start` | `0x04` | Held longer than "Short timer". |
| `Long press repeat` | `0x05` | Sent every 200ms during a long press. |
| `Long press stop` | `0x06` | Released after a long press. |
| `Button stuck` | `0x07` | Held longer than "Stuck timer"; repeats stop. |
| `Button free` | `0x08` | Released after being stuck. |

## 4. Special Gateway Events

The gateway reports its own status or critical bus events using `Type 0x05` messages.

| Event Name | Type Code (Hex) | Description |
| :--- | :--- | :--- |
| `Valid DALI Power` | `0x00` | The DALI bus power has been restored and is stable. |
| `DALI Power Loss` | `0x01` | The DALI bus power is missing or a short-circuit is detected. |
| `Mains Voltage on Bus`| `0x02` | Dangerous mains voltage has been detected on the DALI bus lines. |
| `Defective Power Supply`| `0x03` | The internal DALI power supply is defective. |
| `Message Buffer Full` | `0x04` | The gateway's internal message buffer is full; commands may be lost. |
| `Checksum Error` | `0x05` | The gateway received a command with an invalid checksum. |
| `Invalid Command` | `0x06` | The gateway received a command with an invalid structure or type. |

## 5. Gateway Configuration Area

Parameters accessible via `Type 0x06` (Read) and `Type 0x08` (Write).

| Item | Description | Read | Write | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `1` | Serial number | Yes | No | Unique device identifier. |
| `2` | Firmware version | Yes | No | Format: `upper_byte.lower_byte` (e.g., `0x010A` -> "1.10"). |
| `3` | DALI bus power status | Yes | No | `0`=OK, `1`=Loss/Short, `2`=Mains Voltage, `3`=Defective. |
| `4` | Messages in send buffer | Yes | Yes | Read: Current count (0-16). Write `0` to clear. |
| `5` | Hardware version | Yes | No | Format: `upper_byte.lower_byte`. |
| `6` | Suppress checksum check | Yes | Yes | `0`=Enabled (default), `1`=Disabled. |
| `255`| Enter bootloader | No | Yes | Write `0x424C` ('BL') to enter firmware update mode. |

## 6. Examples

### 6.1. Sending a DALI Command (QUERY STATUS to Address 1)
* **DALI Frame:** `[0x03, 0x90]` (Address is `(1*2)+1=3`)
* **Foxtron Payload (Binary):** `[0x0B, 0x00, 0x10, 0x03, 0x90, 0x00]`
* **Checksum:** `0x51`
* **Full Frame:** `b'\x010B001003900051\x17'`

### 6.2. Decoding a DALI4SW Button Event
* **Received Frame (ASCII):** `0418A1E0024A`
* **Decoded Payload (Binary):** `[0x04, 0x18, 0xA1, 0xE0, 0x02]`
* **Checksum:** `0x4A` (Valid)
* **DALI Frame (24 bits):** `A1E002` -> Address `0x50` (Short Address 80), Instance `0` (SW1), Event `0x02` (Short Press).

### 6.3. Query Firmware Version
* **Send Frame:** `b'\x010602F7\x17'` (Type `0x06`, Item `2`, Checksum `0xF7`)
* **Example Response (Type 0x07):** `b'\x0107020406ED\x17'` -> Item `2`, Value `0x0406` (Version 4.6).

### 6.4. DALI Type 8 (Color Temperature) Sequence
Setting a color temperature of 3000K (Tc value 333, or `0x014D`) requires a multi-step sequence:
1.  **Set DTR1 (High Byte):** Send `[Target Address, 0xC3, 0x01]` (Set DTR1 to `0x01`).
2.  **Set DTR0 (Low Byte):** Send `[Target Address, 0xA3, 0x4D]` (Set DTR0 to `0x4D`).
3.  **Activate Color Temp:** Send `[Target Address, 0xE7]` (SET TEMP (DTR1, DTR0)).

## 4. Additional Implementation Notes

### 4.1 TCP Connection Idle Timeout

By default, the DALInet/DALI2net gateway will close a TCP connection after approximately 30 seconds of inactivity.
This timeout value is **configurable** via the advanced settings page of the gateway's web interface:

```
http://192.168.1.241/AINDEX.HTM
```

The timeout parameter is listed in seconds. If your application requires a persistent connection, either:
- Set this timeout to a higher value in the web interface, **or**
- Periodically send a benign query (e.g., Type `0x06` "Config Query" for firmware version) at an interval shorter than the timeout.

### 4.2 Sequence Flag Best Practice

When sending a sequence of commands with the Sequence flag (bit 1 in the `Param` byte of Type `0x0B`), the last command
in the sequence should **have the Sequence flag cleared (0)**. This automatically terminates the sequence and avoids
needing to send an explicit `Sequence End` (Type `0x0A`). The explicit 0x0A message is only required if the last command
was mistakenly sent with the Sequence flag set.

### 4.3 Out-of-Order Responses

The gateway may send spontaneous bus messages (e.g., Type `0x03` or `0x04`) interleaved with responses to your
commands, if a bus event occurs at the same time. Your implementation should handle receiving an unrelated message
immediately before the expected response.

### 4.4 Telnet Port Caveat

Although the ASCII protocol uses TCP port `23` by default (the traditional Telnet port), the gateway does **not**
implement Telnet option negotiation. If using a Telnet client for testing, ensure it operates in raw mode to avoid
misinterpretation of protocol bytes (such as `0xFF`).
