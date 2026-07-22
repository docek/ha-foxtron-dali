"""Constants for the Foxtron DALI Gateway integration."""

# --- Integration Details ---
DOMAIN = "foxtron_dali"

# --- Event and Trigger Constants ---
EVENT_BUTTON_ACTION = f"{DOMAIN}_button_action"

# --- Dispatcher Signals ---
# Ask all light platforms to rescan their bus and add new lights
SIGNAL_RESCAN = f"{DOMAIN}_rescan"
# Optimistic state update after a broadcast_on/off service call (bool: is_on)
SIGNAL_BROADCAST_STATE = f"{DOMAIN}_broadcast_state"

# Types of button presses
PRESS_TYPES = [
    "short_press",
    "double_press",
    "triple_press",
    "long_press_start",
    "long_press_repeat",
    "long_press_stop",
]

# Switch flaps
FLAPS = ["upper", "lower"]

# Fixed discovery pairing duration (seconds)
DISCOVERY_DURATION_SECONDS = 300

# Safety: Maximum duration for a long press before auto-cancelling (seconds)
MAX_LONG_PRESS_DURATION = 30.0

# How long async_setup_entry waits for the initial gateway connection
# before raising ConfigEntryNotReady (Home Assistant then retries setup)
CONNECT_TIMEOUT_SECONDS = 10.0
