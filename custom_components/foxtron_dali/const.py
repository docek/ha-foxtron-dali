"""Constants for the Foxtron DALI Gateway integration."""

# --- Integration Details ---
DOMAIN = "foxtron_dali"
PLATFORMS = ["light", "event"]

# --- Signals ---
# Signal to dispatch a new DALI event to entities.
# The event object itself will be passed in the signal.
SIGNAL_DALI_EVENT = f"{DOMAIN}_event"

# --- File paths ---
# Template used for importing and exporting light configurations.
# The host and port uniquely identify a DALI bus instance.
LIGHT_CONFIG_FILE_TEMPLATE = "foxtron_dali_lights_{host}_{port}.json"


def light_config_filename(host: str, port: int) -> str:
    """Return the default light configuration filename for a DALI bus."""
    # Replace separators that could appear in IPv6 or hostnames to keep the
    # filename filesystem friendly.
    safe_host = host.replace(":", "_")
    return LIGHT_CONFIG_FILE_TEMPLATE.format(host=safe_host, port=port)
