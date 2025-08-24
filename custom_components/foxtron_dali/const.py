"""Constants for the Foxtron DALI Gateway integration."""

# --- Integration Details ---
DOMAIN = "foxtron_dali"
PLATFORMS = ["light", "event"]

# --- Signals ---
# Signal to dispatch a new DALI event to entities.
# The event object itself will be passed in the signal.
SIGNAL_DALI_EVENT = f"{DOMAIN}_event"

# --- File paths ---
# Default file used for importing and exporting light configurations
LIGHT_CONFIG_FILE = "foxtron_dali_lights.json"
