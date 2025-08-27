import asyncio
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv


from .const import DOMAIN
from .driver import FoxtronDaliDriver, format_button_id, parse_button_id
from .event import (
    DEFAULT_LONG_PRESS_THRESHOLD,
    DEFAULT_LONG_PRESS_REPEAT,
    DEFAULT_MULTI_PRESS_WINDOW,
)

_LOGGER = logging.getLogger(__name__)


@config_entries.HANDLERS.register(DOMAIN)
class FoxtronDaliConfigFlow(config_entries.ConfigFlow):
    """Handle a config flow for Foxtron DALI."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "FoxtronDaliOptionsFlowHandler":
        """Get the options flow for this handler."""
        return FoxtronDaliOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            )
            self._abort_if_unique_id_configured()

            # Test the connection
            try:
                driver = FoxtronDaliDriver(
                    host=user_input[CONF_HOST], port=user_input[CONF_PORT]
                )
                await driver.connect()
                firmware_version = await driver.query_firmware_version()
                if firmware_version is None:
                    raise ConnectionError("Could not retrieve firmware version")

                _LOGGER.info(
                    f"Successfully connected to Foxtron gateway with firmware {firmware_version}"
                )
                await driver.disconnect()

                return self.async_create_entry(
                    title=f"DALI Bus ({user_input[CONF_HOST]}:{user_input[CONF_PORT]})",
                    data=user_input,
                )
            except (asyncio.TimeoutError, ConnectionRefusedError, ConnectionError):
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=23): int,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )


class FoxtronDaliOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Handle an options flow for Foxtron DALI."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        # Use a dictionary for the discovered buttons { "addr_str": "name_str" }
        self.discovered_buttons: Dict[str, str] = {}

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        """Manage the options."""
        # The user sees this menu first when they click "CONFIGURE"
        return self.async_show_menu(
            step_id="init",
            menu_options=["discover_buttons", "set_fade_time", "set_event_timing"],
        )

    async def async_step_set_event_timing(
        self, user_input: Optional[Dict[str, Any]] = None
    ):
        """Handle configuration of button event timing."""
        if user_input is not None:
            new_options = self.config_entry.options.copy()
            new_options["long_press_threshold"] = user_input["long_press_threshold"]
            new_options["long_press_repeat"] = user_input["long_press_repeat"]
            new_options["multi_press_window"] = user_input["multi_press_window"]
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="set_event_timing",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "long_press_threshold",
                        default=self.config_entry.options.get(
                            "long_press_threshold", DEFAULT_LONG_PRESS_THRESHOLD
                        ),
                    ): vol.Coerce(float),
                    vol.Required(
                        "long_press_repeat",
                        default=self.config_entry.options.get(
                            "long_press_repeat", DEFAULT_LONG_PRESS_REPEAT
                        ),
                    ): vol.Coerce(float),
                    vol.Required(
                        "multi_press_window",
                        default=self.config_entry.options.get(
                            "multi_press_window", DEFAULT_MULTI_PRESS_WINDOW
                        ),
                    ): vol.Coerce(float),
                }
            ),
        )

    async def async_step_discover_buttons(
        self, user_input: Optional[Dict[str, Any]] = None
    ):
        """Handle the button discovery and adoption step."""
        driver: FoxtronDaliDriver = self.hass.data[DOMAIN][self.config_entry.entry_id]

        # No active scan is performed here. Buttons are discovered passively
        # when they send DALI-2 input events. Users should press the buttons
        # they wish to add before refreshing this form.

        # This block runs when the user clicks SUBMIT on the form
        if user_input is not None:
            # Get the list of buttons already in the config
            existing_buttons = [
                btn if isinstance(btn, str) else format_button_id(*btn)
                for btn in self.config_entry.options.get("buttons", [])
            ]

            # Get the list of newly selected buttons from the form
            selected_buttons = user_input.get("buttons", [])

            # Combine the old and new lists and remove any duplicates
            all_buttons = sorted(set(existing_buttons + selected_buttons))

            # Tell the driver that these buttons are now known
            for button_id in selected_buttons:
                driver.add_known_button(button_id)

            # Clear the driver's cache of newly discovered buttons
            driver.clear_newly_discovered_buttons()

            # Create a new options dictionary with the updated button list
            new_options = self.config_entry.options.copy()
            new_options["buttons"] = all_buttons

            # Save the updated options to the config entry
            return self.async_create_entry(title="", data=new_options)

        # This block runs when the form is first shown
        # Get the list of buttons the driver has seen but are not yet configured
        newly_discovered = driver.get_newly_discovered_buttons()

        # Format them for the multi-select list: { "addr-inst": "Button Name" }
        self.discovered_buttons = {
            btn_id: f"DALI Button {addr} (inst {inst})"
            for btn_id in newly_discovered
            for addr, inst in [parse_button_id(btn_id)]
        }

        # If no new buttons have been seen, show an informational message.
        if not self.discovered_buttons:
            return self.async_show_form(
                step_id="discover_buttons",
                # An empty schema will just show the description and a submit button.
                data_schema=vol.Schema({}),
            )

        # If new buttons are found, show the form with the list of buttons.
        return self.async_show_form(
            step_id="discover_buttons",
            data_schema=vol.Schema(
                {
                    # Create a multi-select box. Default to nothing selected.
                    vol.Optional(
                        "buttons",
                        default=[],
                    ): cv.multi_select(self.discovered_buttons),
                }
            ),
        )

    async def async_step_set_fade_time(
        self, user_input: Optional[Dict[str, Any]] = None
    ):
        """Handle the fade time setting step."""
        if user_input is not None:
            # Get all current options and update the fade_time
            new_options = self.config_entry.options.copy()
            new_options["fade_time"] = user_input["fade_time"]
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="set_fade_time",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "fade_time",
                        default=self.config_entry.options.get("fade_time", 0),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=15)),
                }
            ),
        )
