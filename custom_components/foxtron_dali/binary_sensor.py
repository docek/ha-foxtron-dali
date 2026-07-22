"""Diagnostic binary sensors for the DALI bus (connection + bus power)."""

import logging

from homeassistant.components import persistent_notification
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .driver import (
    FoxtronDaliDriver,
    GW_EVENT_POWER_OK,
    GW_EVENT_POWER_LOSS,
    GW_EVENT_MAINS_ON_BUS,
    GW_EVENT_PSU_DEFECTIVE,
    SpecialGatewayEvent,
)

_LOGGER = logging.getLogger(__name__)

# Config item 3 reports the same power status codes as Type 0x05 events
CONFIG_ITEM_BUS_POWER = 3

POWER_CODES = (
    GW_EVENT_POWER_OK,
    GW_EVENT_POWER_LOSS,
    GW_EVENT_MAINS_ON_BUS,
    GW_EVENT_PSU_DEFECTIVE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DALI bus diagnostic sensors."""
    driver: FoxtronDaliDriver = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DaliBusConnectedSensor(entry, driver),
            DaliBusPowerSensor(entry, driver),
        ]
    )


class _DaliBusSensorBase(BinarySensorEntity):
    """Common plumbing for per-bus diagnostic sensors."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, driver: FoxtronDaliDriver) -> None:
        self._driver = driver
        host = entry.data[CONF_HOST]
        port = entry.data[CONF_PORT]
        self._bus_id = f"{host}_{port}"
        self._bus_label = f"{host}:{port}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})


class DaliBusConnectedSensor(_DaliBusSensorBase):
    """Reports whether the TCP connection to the gateway bus is up."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, entry: ConfigEntry, driver: FoxtronDaliDriver) -> None:
        super().__init__(entry, driver)
        self._attr_name = f"DALI Bus Connected ({self._bus_label})"
        self._attr_unique_id = f"{self._bus_id}_connected"

    @property
    def is_on(self) -> bool:
        """Return True while the gateway connection is established."""
        return self._driver.is_connected

    async def async_added_to_hass(self) -> None:
        """Track driver connection state changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self._driver.add_connect_callback(self._on_change))
        self.async_on_remove(self._driver.add_disconnect_callback(self._on_change))

    def _on_change(self) -> None:
        self.async_write_ha_state()


class DaliBusPowerSensor(_DaliBusSensorBase):
    """Reports the DALI bus power status as seen by the gateway.

    Sourced from Type 0x05 gateway events; the initial state is read from
    gateway config item 3. Failure states raise a persistent notification.
    """

    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(self, entry: ConfigEntry, driver: FoxtronDaliDriver) -> None:
        super().__init__(entry, driver)
        self._attr_name = f"DALI Bus Power ({self._bus_label})"
        self._attr_unique_id = f"{self._bus_id}_bus_power"
        self._power_ok: bool | None = None
        self._status: str | None = None

    @property
    def is_on(self) -> bool | None:
        """Return True when the DALI bus power is OK, None when unknown."""
        return self._power_ok

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the raw gateway status description."""
        return {"status": self._status}

    async def async_added_to_hass(self) -> None:
        """Subscribe to gateway events and read the initial power status."""
        await super().async_added_to_hass()
        self.async_on_remove(self._driver.add_event_listener(self._handle_event))
        self.async_on_remove(
            self._driver.add_disconnect_callback(self._handle_driver_disconnect)
        )
        self.async_on_remove(
            self._driver.add_connect_callback(self._handle_driver_connect)
        )
        self.hass.async_create_task(self._async_refresh_status())

    async def _async_refresh_status(self) -> None:
        """Read the current bus power status from the gateway."""
        value = await self._driver.query_config_item(CONFIG_ITEM_BUS_POWER, timeout=3)
        if value is not None and value in POWER_CODES:
            self._apply_power_code(value)
            self.async_write_ha_state()

    def _handle_driver_disconnect(self) -> None:
        """Power status is unknown while the gateway is unreachable."""
        self._attr_available = False
        self.async_write_ha_state()

    def _handle_driver_connect(self) -> None:
        """Restore availability and re-read the status after a reconnect."""
        self._attr_available = True
        self.async_write_ha_state()
        self.hass.async_create_task(self._async_refresh_status())

    def _handle_event(self, event) -> None:
        """Track Type 0x05 power-related gateway events."""
        if not isinstance(event, SpecialGatewayEvent):
            return
        if event.event_code not in POWER_CODES:
            return
        self._apply_power_code(event.event_code)
        self.async_write_ha_state()

    @callback
    def _apply_power_code(self, code: int) -> None:
        """Update state from a gateway power status code and notify."""
        self._power_ok = code == GW_EVENT_POWER_OK
        self._status = SpecialGatewayEvent.EVENT_MAP.get(code, f"Unknown ({code})")
        notification_id = f"dali_bus_power_{self._bus_id}"

        if self._power_ok:
            persistent_notification.async_dismiss(self.hass, notification_id)
        else:
            _LOGGER.warning(
                "DALI bus %s reports power problem: %s", self._bus_label, self._status
            )
            persistent_notification.async_create(
                self.hass,
                f"DALI sběrnice {self._bus_label} hlásí problém napájení: "
                f"**{self._status}**.\n\n"
                "Světla a tlačítka na této sběrnici nemusí reagovat, "
                "i když je brána dostupná.",
                title="Problém napájení DALI sběrnice",
                notification_id=notification_id,
            )
