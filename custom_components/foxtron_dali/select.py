"""Per-light fade time configuration (select entity).

The fade time lives in the ballast's NVM — Home Assistant only mirrors
it. The entity reads the value from hardware on startup and reconnect,
and writes only on an explicit user change (verified by readback).
It never pushes state on its own: no NVM wear on restarts, and values
set by external commissioning tools are preserved.
"""

import logging
from typing import Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import helpers
from .driver import FADE_TIME_SECONDS, FoxtronDaliDriver

_LOGGER = logging.getLogger(__name__)

# Human readable label per DALI fade code (0-15)
OPTION_BY_CODE = {
    code: ("No fade" if seconds == 0 else f"{seconds} s")
    for code, seconds in FADE_TIME_SECONDS.items()
}
CODE_BY_OPTION = {option: code for code, option in OPTION_BY_CODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one fade time select per known DALI light."""
    await helpers.async_setup_scanned_entities(
        hass,
        entry,
        async_add_entities,
        lambda driver, addr: DaliFadeTimeSelect(driver, addr, entry),
    )


class DaliFadeTimeSelect(helpers.ConnectionAwareEntity, SelectEntity):
    """Fade time of one DALI light, mirrored from the ballast NVM."""

    _attr_has_entity_name = True
    _attr_name = "Fade time"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [OPTION_BY_CODE[code] for code in range(16)]
    # Fade time is static NVM data: read once at add, never on reconnect
    # (re-reading 138 selects after every TCP blip doubled the query storm)
    _refresh_on_reconnect = False

    def __init__(
        self, driver: FoxtronDaliDriver, address: int, entry: ConfigEntry
    ) -> None:
        self._driver = driver
        self._address = address
        self._entry = entry
        self._attr_current_option: Optional[str] = None
        self._attr_unique_id = f"{helpers.bus_id(entry)}_{address}_fade_time"

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the same per-light device as the light entity."""
        return helpers.light_device_info(self._entry, self._address)

    async def async_update(self) -> None:
        """Mirror the fade time currently stored in the ballast."""
        code = await self._driver.query_fade_time(self._address)
        self._attr_current_option = None if code is None else OPTION_BY_CODE.get(code)

    async def async_select_option(self, option: str) -> None:
        """Write the fade time to the ballast NVM and verify by readback."""
        code = CODE_BY_OPTION[option]
        await self._driver.set_fade_time(code, short_address=self._address)

        readback = await self._driver.query_fade_time(self._address)
        self._attr_current_option = (
            None if readback is None else OPTION_BY_CODE.get(readback)
        )
        self.async_write_ha_state()
        if readback != code:
            raise HomeAssistantError(
                f"DALI light {self._address} reports fade time code {readback} "
                f"after writing {code}; the ballast may not support it"
            )
