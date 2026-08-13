"""Per-light fade profile configuration (select entity).

The profile is the *full-range* fade duration used for delta-proportional
fading: the light entity scales it by the size of each brightness change
and writes the resulting DALI fade code to the ballast (see
DaliLight._fade_code_for). The value is an integration-side setting
persisted by HA restore state — it is NOT a mirror of the ballast NVM
and never touches the bus itself.
"""

import logging
from typing import Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import helpers
from .driver import FoxtronDaliDriver

_LOGGER = logging.getLogger(__name__)

# Full-range fade duration per option label
FADE_PROFILE_SECONDS = {
    "No fade": 0.0,
    "0.7 s": 0.7,
    "1.4 s": 1.4,
    "2.0 s": 2.0,
    "2.8 s": 2.8,
    "4.0 s": 4.0,
}
DEFAULT_FADE_PROFILE_OPTION = "2.0 s"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one fade profile select per known DALI light."""
    await helpers.async_setup_scanned_entities(
        hass,
        entry,
        async_add_entities,
        lambda driver, addr: DaliFadeProfileSelect(driver, addr, entry),
    )


class DaliFadeProfileSelect(SelectEntity, RestoreEntity):
    """Full-range fade duration of one DALI light."""

    _attr_has_entity_name = True
    _attr_name = "Fade profile"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(FADE_PROFILE_SECONDS)
    _attr_should_poll = False

    def __init__(
        self, driver: FoxtronDaliDriver, address: int, entry: ConfigEntry
    ) -> None:
        self._driver = driver
        self._address = address
        self._entry = entry
        self._attr_current_option: Optional[str] = None
        # Keep the historical "_fade_time" suffix: the registry entry,
        # entity_id and area assignment of the old select survive the
        # repurpose to a fade profile
        self._attr_unique_id = f"{helpers.bus_id(entry)}_{address}_fade_time"

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the same per-light device as the light entity."""
        return helpers.light_device_info(self._entry, self._address)

    def _publish(self, option: str) -> None:
        """Make the profile available to the light entity via the driver."""
        self._attr_current_option = option
        self._driver.fade_profile_seconds[self._address] = FADE_PROFILE_SECONDS[option]

    async def async_added_to_hass(self) -> None:
        """Restore the last selected profile, defaulting to 2.0 s."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        option = DEFAULT_FADE_PROFILE_OPTION
        if last is not None and last.state in FADE_PROFILE_SECONDS:
            option = last.state
        self._publish(option)
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Store the new profile (no bus traffic involved)."""
        self._publish(option)
        self.async_write_ha_state()
