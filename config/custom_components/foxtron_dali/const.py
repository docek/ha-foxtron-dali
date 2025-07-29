"""Constants for the Foxtron DALI Gateway integration."""

# --- Integration Details ---
DOMAIN = "foxtron_dali"
PLATFORMS = ["light", "event"]

# --- Signals ---
# Signal to dispatch a new DALI event to entities.
# The event object itself will be passed in the signal.
SIGNAL_DALI_EVENT = f"{DOMAIN}_event"

# --- Foxtron Protocol Constants ---

# Message types sent from the gateway to the client
MSG_TYPE_DALI_EVENT_1 = 0x03
MSG_TYPE_DALI_EVENT_2 = 0x04
MSG_TYPE_SPECIAL_GATEWAY_EVENT = 0x05
MSG_TYPE_CONFIG_RESPONSE = 0x07
MSG_TYPE_DALI_RESPONSE = 0x0D
MSG_TYPE_CONFIRMATION = 0x0E

# Message types sent from the client to the gateway
MSG_TYPE_QUERY_CONFIG_ITEM = 0x06
MSG_TYPE_SEND_DALI_COMMAND = 0x0B


# --- DALI Standard Command Opcodes (IEC 62386-102) ---
DALI_CMD_OFF = 0x00
DALI_CMD_RECALL_MAX_LEVEL = 0x05
DALI_CMD_SET_FADE_TIME = 0x2F
DALI_CMD_QUERY_CONTROL_GEAR_PRESENT = 0x90
DALI_CMD_QUERY_ACTUAL_LEVEL = 0xA0
DALI_CMD_DTR0 = 0xA3  # Set Data Transfer Register 0

# --- DALI Addressing ---
DALI_BROADCAST = 0xFF


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

# --- Mappings for Readable Logs ---
MESSAGE_TYPE_NAMES = {
    MSG_TYPE_DALI_EVENT_1: "DALI Event w/ Answer (Spontaneous)",
    MSG_TYPE_DALI_EVENT_2: "DALI Event w/o Answer (Spontaneous)",
    MSG_TYPE_SPECIAL_GATEWAY_EVENT: "Special Gateway Event",
    MSG_TYPE_CONFIG_RESPONSE: "Config Response",
    MSG_TYPE_DALI_RESPONSE: "DALI Response w/ Answer (Differentiated)",
    MSG_TYPE_CONFIRMATION: "Confirmation w/o Answer (Differentiated)",
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
