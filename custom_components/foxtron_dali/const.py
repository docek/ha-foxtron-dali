"""Constants for the Foxtron DALI Gateway integration."""

# --- Integration Details ---
DOMAIN = "foxtron_dali"
PLATFORMS = ["light", "event"]

# --- Event and Trigger Constants ---
SIGNAL_DALI_EVENT = f"{DOMAIN}_event"
EVENT_BUTTON_ACTION = f"{DOMAIN}_button_action"

CONF_UPPER_INSTANCE = "upper_instance"
CONF_LOWER_INSTANCE = "lower_instance"
CONF_ADDRESS = "address"

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

# Safety: Maximum duration for a long press before auto-cancelling (seconds)
MAX_LONG_PRESS_DURATION = 30.0
