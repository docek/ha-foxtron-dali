import asyncio
import logging

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

    async def async_step_user(self, user_input=None):
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
                # You might want to query the firmware version to be sure
                # that you are connected to the correct device.
                # firmware_version = await driver.query_firmware_version()
                await driver.disconnect()

                return self.async_create_entry(
                    title=f"DALI Bus ({user_input[CONF_HOST]}:{user_input[CONF_PORT]})",
                    data=user_input,
                )
            except (asyncio.TimeoutError, ConnectionRefusedError):
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