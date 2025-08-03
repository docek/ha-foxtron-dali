
import asyncio
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback

from .const import DOMAIN
from .driver import FoxtronDaliDriver

_LOGGER = logging.getLogger(__name__)


class FoxtronDaliConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
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


class FoxtronDaliOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle an options flow for Foxtron DALI."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.discovered_buttons: Dict[int, str] = {}

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        """Manage the options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["discover_buttons", "set_fade_time"],
        )

    async def async_step_discover_buttons(
        self, user_input: Optional[Dict[str, Any]] = None
    ):
        """Handle the button discovery step."""
        if user_input is not None:
            # In the next step, we will add the selected buttons.
            # For now, this just finishes the flow.
            return self.async_create_entry(title="", data={})

        driver: FoxtronDaliDriver = self.hass.data[DOMAIN][self.config_entry.entry_id]
        newly_discovered = driver.get_newly_discovered_buttons()

        for addr in newly_discovered:
            self.discovered_buttons[addr] = f"DALI Button {addr}"

        return self.async_show_form(
            step_id="discover_buttons",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "buttons",
                        description="Press new buttons to see them here",
                    ): cv.multi_select(self.discovered_buttons),
                }
            ),
            description_placeholders={
                "discovered_count": len(self.discovered_buttons)
            },
        )

    async def async_step_set_fade_time(
        self, user_input: Optional[Dict[str, Any]] = None
    ):
        """Handle the fade time setting step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

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
