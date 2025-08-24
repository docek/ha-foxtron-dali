import asyncio
import logging
from typing import Any, Dict, Optional
import json

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    entity_registry as er,
)
from homeassistant.components import persistent_notification


from .const import DOMAIN, LIGHT_CONFIG_FILE
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
            menu_options=[
                "discover_buttons",
                "set_fade_time",
                "set_event_timing",
                "upload_config",
                "backup_config",
            ],
        )

    async def async_step_upload_config(
        self, user_input: Optional[Dict[str, Any]] = None
    ):
        """Handle the upload of the light configuration file."""
        errors = {}
        if user_input is not None:
            file_path = user_input["file_path"]
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        raise ValueError("Invalid JSON structure")
                    light_config: Dict[int, Dict[str, Any]] = {}
                    entity_reg = er.async_get(self.hass)
                    area_reg = ar.async_get(self.hass)
                    host = self.config_entry.data[CONF_HOST]
                    port = self.config_entry.data[CONF_PORT]
                    for addr_str, cfg in data.items():
                        if not isinstance(cfg, dict):
                            raise ValueError("Invalid JSON structure")
                        address = int(addr_str)
                        name = cfg.get("name", f"DALI Light {address}")
                        area = cfg.get("area", "")
                        unique_id = cfg.get("unique_id", f"{host}_{port}_{address}")
                        hidden_by = cfg.get("hidden_by")
                        disabled_by = cfg.get("disabled_by")
                        light_config[address] = {
                            "name": name,
                            "area": area,
                            "unique_id": unique_id,
                            "hidden_by": hidden_by,
                            "disabled_by": disabled_by,
                        }
                        if "hidden_by" in cfg:
                            light_config[address]["hidden_by"] = hidden_by
                        if "disabled_by" in cfg:
                            light_config[address]["disabled_by"] = disabled_by
                        entity_id = entity_reg.async_get_entity_id(
                            "light", DOMAIN, unique_id
                        )
                        if not entity_id:
                            default_uid = f"{host}_{port}_{address}"
                            entity_id = entity_reg.async_get_entity_id(
                                "light", DOMAIN, default_uid
                            )
                        if entity_id:
                            area_obj = area_reg.async_get_area_by_name(area)
                            if area and not area_obj:
                                area_obj = area_reg.async_get_or_create(area)
                            area_id = area_obj.id if area_obj else None
                            updates = {"name": name, "area_id": area_id}
                            if "hidden_by" in cfg:
                                updates["hidden_by"] = (
                                    er.RegistryEntryHider(hidden_by)
                                    if hidden_by is not None
                                    else None
                                )
                            if "disabled_by" in cfg:
                                updates["disabled_by"] = (
                                    er.RegistryEntryDisabler(disabled_by)
                                    if disabled_by is not None
                                    else None
                                )
                            entry = entity_reg.async_get(entity_id)
                            if entry and entry.unique_id != unique_id:
                                updates["new_unique_id"] = unique_id
                            entity_reg.async_update_entity(entity_id, **updates)

                    driver = self.hass.data.get(DOMAIN, {}).get(
                        self.config_entry.entry_id
                    )
                    discovered_addresses: set[int] = set()
                    if driver:
                        try:
                            discovered_addresses = set(await driver.scan_for_devices())
                        except Exception as err:  # pragma: no cover - best effort
                            _LOGGER.debug("Device scan failed: %s", err)

                    backup_addresses = set(light_config.keys())
                    missing = backup_addresses - discovered_addresses
                    new = discovered_addresses - backup_addresses
                    if missing or new:
                        parts = []
                        if missing:
                            parts.append(f"Missing lights: {sorted(missing)}")
                        if new:
                            parts.append(f"New lights: {sorted(new)}")
                        message = ". ".join(parts)
                        persistent_notification.async_create(
                            self.hass, message, title="Foxtron DALI import"
                        )

                    new_options = self.config_entry.options.copy()
                    new_options["light_config"] = light_config
                    return self.async_create_entry(title="", data=new_options)

            except FileNotFoundError:
                errors["base"] = "file_not_found"
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.error("Error processing config file: %s", e)
                errors["base"] = "invalid_json"

        return self.async_show_form(
            step_id="upload_config",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "file_path",
                        default=self.hass.config.path(LIGHT_CONFIG_FILE),
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_backup_config(
        self, user_input: Optional[Dict[str, Any]] = None
    ):
        """Handle backing up the light configuration to a file."""
        errors = {}
        if user_input is not None:
            file_path = user_input["file_path"]
            light_config = self.config_entry.options.get("light_config", {})
            driver: Optional[FoxtronDaliDriver] = self.hass.data.get(DOMAIN, {}).get(
                self.config_entry.entry_id
            )
            try:
                discovered_addresses = await driver.scan_for_devices() if driver else []
                all_addresses = sorted(
                    set(discovered_addresses) | set(light_config.keys())
                )
                if not all_addresses:
                    errors["base"] = "no_config"
                else:
                    entity_reg = er.async_get(self.hass)
                    area_reg = ar.async_get(self.hass)
                    data: Dict[int, Dict[str, Any]] = {}
                    host = self.config_entry.data[CONF_HOST]
                    port = self.config_entry.data[CONF_PORT]
                    for address in all_addresses:
                        cfg = light_config.get(address, {})
                        unique_id = cfg.get("unique_id", f"{host}_{port}_{address}")
                        entity_id = entity_reg.async_get_entity_id(
                            "light", DOMAIN, unique_id
                        )
                        name = cfg.get("name", f"DALI Light {address}")
                        area_name = cfg.get("area", "")
                        hidden_by = None
                        disabled_by = None
                        if entity_id:
                            entry = entity_reg.async_get(entity_id)
                            if entry:
                                if entry.area_id:
                                    area = area_reg.async_get_area(entry.area_id)
                                    if area:
                                        area_name = area.name

                                state = self.hass.states.get(entity_id)
                                if state:
                                    name = state.name
                                elif entry.name:
                                    name = entry.name
                                if entry.hidden_by is not None:
                                    hidden_by = entry.hidden_by.value
                                if entry.disabled_by is not None:
                                    disabled_by = entry.disabled_by.value

                        data[address] = {
                            "name": name,
                            "area": area_name,
                            "unique_id": unique_id,
                            "hidden_by": hidden_by,
                            "disabled_by": disabled_by,
                        }
                        if hidden_by is not None:
                            data[address]["hidden_by"] = hidden_by
                        if disabled_by is not None:
                            data[address]["disabled_by"] = disabled_by

                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    return self.async_create_entry(
                        title="", data=self.config_entry.options
                    )
            except OSError as err:
                _LOGGER.error("Error writing backup file: %s", err)
                errors["base"] = "write_failed"

        return self.async_show_form(
            step_id="backup_config",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "file_path",
                        default=self.hass.config.path(LIGHT_CONFIG_FILE),
                    ): str
                }
            ),
            errors=errors,
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
