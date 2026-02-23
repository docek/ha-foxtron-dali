import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_BUTTON_ACTION, MAX_LONG_PRESS_DURATION
from .driver import (
    DaliInputNotificationEvent,
    FoxtronDaliDriver,
    EVENT_BUTTON_PRESSED,
    EVENT_BUTTON_RELEASED,
    EVENT_BUTTON_STUCK,
    EVENT_BUTTON_FREE,
    format_button_id,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util
import datetime

_LOGGER = logging.getLogger(__name__)

# Default timing constants (in seconds)
DEFAULT_LONG_PRESS_THRESHOLD = 0.2
DEFAULT_LONG_PRESS_REPEAT = 0.2
DEFAULT_MULTI_PRESS_WINDOW = 0.3


@dataclass
class _ButtonState:
    """Holds temporary state for a button address."""

    press_count: int = 0
    finalize_task: asyncio.Task | None = None
    long_press_task: asyncio.Task | None = None
    long_press_started: bool = False
    last_event_data: dict = field(default_factory=dict)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DALI buttons from a config entry."""
    driver: FoxtronDaliDriver = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DaliButton(entry, driver)])


class DaliButton(EventEntity):
    """Representation of a DALI button event handler."""

    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, driver: FoxtronDaliDriver) -> None:
        """Initialize the button event handler."""
        self._driver = driver
        self._entry = entry
        self._log = _LOGGER.getChild(f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}")
        self._attr_name = "DALI Button Events"
        self._bus_id = f"{entry.data[CONF_HOST]}_{entry.data[CONF_PORT]}"
        self._attr_unique_id = f"{self._bus_id}_button_events"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"DALI Bus ({entry.data['host']})",
            "manufacturer": "Foxtron",
            "model": "DALI2net",
        }
        self._attr_event_types = [
            "button_pressed",
            "button_released",
            "short_press",
            "double_press",
            "triple_press",
            "long_press_start",
            "long_press_repeat",
            "long_press_stop",
        ]
        self._unsub: Callable[[], None] | None = None
        self._button_states: dict[str, _ButtonState] = {}
        options = entry.options
        self._long_press_threshold = options.get(
            "long_press_threshold", DEFAULT_LONG_PRESS_THRESHOLD
        )
        self._long_press_repeat = options.get(
            "long_press_repeat", DEFAULT_LONG_PRESS_REPEAT
        )
        self._multi_press_window = options.get(
            "multi_press_window", DEFAULT_MULTI_PRESS_WINDOW
        )

        # ====== Discovery Mode Properties ======
        self._discovery_active_until: datetime.datetime | None = None
        self._last_discovery_press: dict | None = None
        self._discovery_unsub: Callable | None = None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        self._unsub = self._driver.add_event_listener(self._handle_event)
        
        # Nasloucháme globálnímu signálu pro spuštění discovery z config flow
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_start_discovery", self._start_discovery
            )
        )

        # Při reconnectu driveru resetujeme stavy tlačítek
        self._driver.add_disconnect_callback(self._cancel_all_button_tasks)

    def _cancel_all_button_tasks(self) -> None:
        """Cancel all pending button tasks (called on TCP reconnect)."""
        for key, state in self._button_states.items():
            if state.long_press_task:
                state.long_press_task.cancel()
                state.long_press_task = None
            if state.finalize_task:
                state.finalize_task.cancel()
                state.finalize_task = None
            state.long_press_started = False
            state.press_count = 0
        self._log.info("All button states reset (TCP reconnect or cleanup).")

    async def _start_discovery(self, event) -> None:
        """Aktivuje párovací režim."""
        duration = event.data.get("duration", 60)
        self._discovery_active_until = dt_util.utcnow() + datetime.timedelta(seconds=duration)
        self._last_discovery_press = None
        self._log.info(f"DISCOVERY MODE AKTIVOVÁN na {duration} vteřin pro {self._bus_id}!")
        
        # Upozornění i vizuální (persistentní notifikace)
        self.hass.components.persistent_notification.async_create(
            f"Párovací režim pro DALI bránu {self._bus_id} běží. "
            f"Stiskněte Nahoře a hned poté Dolů na fyzickém tlačítku pro spárování.",
            title="DALI Párování Aktivní",
            notification_id=f"dali_discovery_{self._bus_id}",
        )

        if self._discovery_unsub:
            self._discovery_unsub()

        # Automatické vypnutí po expiraci
        self._discovery_unsub = async_track_point_in_time(
            self.hass, self._end_discovery, self._discovery_active_until
        )

    @callback
    def _end_discovery(self, *_) -> None:
        """Ukončí párovací režim."""
        self._discovery_active_until = None
        self._last_discovery_press = None
        self._log.info(f"DISCOVERY MODE pro {self._bus_id} UKONČEN.")
        self.hass.components.persistent_notification.async_dismiss(f"dali_discovery_{self._bus_id}")

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        if self._unsub:
            self._unsub()
        await super().async_will_remove_from_hass()

    def _trigger_event(
        self, event_type: str, event_attributes: dict | None = None
    ) -> None:
        """Fire both entity and Home Assistant bus events."""
        super()._trigger_event(event_type, event_attributes)
        
        if getattr(self.hass, "bus", None):
            attrs = dict(event_attributes or {})
            
            # --- Zpětná kompatibilita (starý event) ---
            attrs["press_type"] = event_type
            self.hass.bus.async_fire(f"{DOMAIN}_button_event", attrs)

            # --- Nativní Device Triggers ---
            # Zkusíme dohledat zaregistrovaný physical device podle unikátní identity (bus_id_address)
            # A vystavit nativní EVENT_BUTTON_ACTION pro zařízení (tím ho odchytí device_trigger.py)
            device_registry = dr.async_get(self.hass)
            address = attrs.get("address")
            instance_number = attrs.get("instance_number")
            
            # Najdeme device s identifikátorem `foxtron_dali_dali4sw_{bus_id}_{address}`
            device_identifier = (DOMAIN, f"dali4sw_{self._bus_id}_{address}")
            device = device_registry.async_get_device(identifiers={device_identifier})
            
            if device:
                # Najdeme, jestli se instance mapuje na upper nebo lower
                # Tohle závisí na informacích, co si uložíme do device v `hw_version` apod.,
                # nebo elegantně vyčteme z vazby v registrech.
                # Prozatím jako nejlepší způsob: uložíme si mapování přímo do konfigurace.
                upper_inst = None
                lower_inst = None
                if device.hw_version:
                    try:
                        upper_str, lower_str = device.hw_version.split(",")
                        upper_inst = int(upper_str)
                        lower_inst = int(lower_str)
                    except ValueError:
                        pass
                
                flap = None
                if instance_number == upper_inst:
                    flap = "upper"
                elif instance_number == lower_inst:
                    flap = "lower"
                
                # Pokud víme, o jakou klapku šlo, pustíme nativní device trigger
                if flap:
                    device_event_data = {
                        "device_id": device.id,
                        "flap": flap,
                        "press_type": event_type,
                    }
                    self.hass.bus.async_fire(EVENT_BUTTON_ACTION, device_event_data)
                    self._log.debug(f"Fired native device trigger: {flap}_{event_type}")

                    # Logbook záznam — zobrazí se v Activity tabu zařízení
                    self.hass.bus.async_fire(
                        "logbook_entry",
                        {
                            "name": device.name or f"DALI Switch {address}",
                            "message": f"{flap} {event_type}",
                            "domain": DOMAIN,
                            "entity_id": self.entity_id,
                            "device_id": device.id,
                        },
                    )

    async def _handle_discovery(self, data: dict, event_time: datetime.datetime):
        """Vyhodnotí, zda nedošlo ke korektní párovací sekvenci stisků."""
        if not self._discovery_active_until or event_time > self._discovery_active_until:
            return

        address = data["address"]
        instance = data["instance_number"]

        if not self._last_discovery_press:
            self._last_discovery_press = {
                "address": address,
                "instance": instance,
                "time": event_time
            }
            return

        # Už máme první stisk, zkontrolujeme druhý
        last = self._last_discovery_press
        
        # Musí to být stejná adresa, jiná instance, a do 5 vteřin po sobě
        time_diff = (event_time - last["time"]).total_seconds()
        
        if last["address"] == address and last["instance"] != instance and time_diff <= 5.0:
            upper_instance = last["instance"]
            lower_instance = instance
            
            self._log.info(f"DISCOVERY ÚSPĚŠNÁ! Pareme Adresu {address} -> Nahoru: {upper_instance}, Dolu: {lower_instance}")
            
            # ====== KDYŽ DOPOČÍTÁME PÁROVÁNÍ, VYTVOŘÍME HA DEVICE! ======
            device_registry = dr.async_get(self.hass)
            
            identifier = (DOMAIN, f"dali4sw_{self._bus_id}_{address}")
            
            new_device = device_registry.async_get_or_create(
                config_entry_id=self._entry.entry_id,
                identifiers={identifier},
                name=f"DALI Vypínač {address} ({self._bus_id})",
                manufacturer="Foxtron",
                model="DALI4sw",
                hw_version=f"{upper_instance},{lower_instance}",
                sw_version=f"↑ Inst {upper_instance}, ↓ Inst {lower_instance}",
                via_device=(DOMAIN, self._entry.entry_id)
            )

            # Pošleme notifikaci o spárování uživateli
            self.hass.components.persistent_notification.async_create(
                f"Nový vypínač (Adresa: {address}) byl **úspěšně zaregistrován** v integraci DALI.\n\n"
                f"* Nahoru (Upper): Instance {upper_instance}\n"
                f"* Dolů (Lower): Instance {lower_instance}\n\n"
                "Klikněte do Nastavení -> Zařízení a služby -> Devices, kde si tlačítko můžete přejmenovat a napojit na automatizace.",
                title="DALI Vypínač Zaregistrován ✅",
                notification_id=f"dali_found_{self._bus_id}_{address}",
            )
            
            # Restartujeme sekvenci, abychom nenahrávali blbosti
            self._last_discovery_press = None
        else:
            # Neplatná sekvence (třeba jina adresa, nebo moc pomalu). Přejedeme.
            self._last_discovery_press = {
                "address": address,
                "instance": instance,
                "time": event_time
            }

    async def _handle_event(self, event) -> None:
        """Process a single event from the DALI driver."""
        if not isinstance(event, DaliInputNotificationEvent):
            return

        if event.address is None:
            return

        # Akceptujeme: PRESSED, RELEASED, STUCK, FREE
        # Vše ostatní (Short Press, Double Press apod. z HW) ignorujeme,
        # protože gesta skládáme sami v softwaru.
        if event.event_code not in (
            EVENT_BUTTON_PRESSED,
            EVENT_BUTTON_RELEASED,
            EVENT_BUTTON_STUCK,
            EVENT_BUTTON_FREE,
        ):
            return

        key = format_button_id(event.address, event.instance_number)
        data = {
            "bus_id": self._bus_id,
            "address": event.address,
            "address_type": event.address_type,
            "instance_number": event.instance_number,
        }

        state = self._button_states.setdefault(key, _ButtonState())
        state.last_event_data = data

        if event.event_code == EVENT_BUTTON_PRESSED:
            # Párovací logika funguje pouze z prostých stisků
            await self._handle_discovery(data, dt_util.utcnow())
            
            self._trigger_event("button_pressed", data)

            if state.finalize_task:
                state.finalize_task.cancel()
                state.finalize_task = None

            state.long_press_task = self.hass.async_create_task(
                self._handle_long_press(key)
            )

        elif event.event_code in (EVENT_BUTTON_RELEASED, EVENT_BUTTON_STUCK, EVENT_BUTTON_FREE):
            # Button Stuck a Button Free zpracováváme stejně jako RELEASED —
            # ukončí long_press smyčku a vyhodnotí finální gesto.
            if event.event_code == EVENT_BUTTON_STUCK:
                self._log.warning(
                    "Button STUCK detected for %s — treating as release", key
                )
            elif event.event_code == EVENT_BUTTON_FREE:
                self._log.info(
                    "Button FREE after stuck for %s", key
                )

            self._trigger_event("button_released", data)

            if state.long_press_task:
                state.long_press_task.cancel()
                state.long_press_task = None

            if state.long_press_started:
                self._trigger_event("long_press_stop", data)
                state.long_press_started = False
                state.press_count = 0
            else:
                state.press_count += 1
                state.finalize_task = self.hass.async_create_task(
                    self._finalize_presses(key)
                )

    async def _handle_long_press(self, key: str) -> None:
        """Handle long press start and repeat events for a button."""
        state = self._button_states[key]
        try:
            await asyncio.sleep(self._long_press_threshold)
            state.long_press_started = True
            data = state.last_event_data
            self._trigger_event("long_press_start", data)

            # Safety timeout: maximální doba trvání long pressu.
            # Chrání před situací, kdy RELEASED event nedorazí (TCP ztráta, HW chyba).
            elapsed = self._long_press_threshold
            while elapsed < MAX_LONG_PRESS_DURATION:
                await asyncio.sleep(self._long_press_repeat)
                elapsed += self._long_press_repeat
                self._trigger_event("long_press_repeat", data)

            # Dosáhli jsme safety timeoutu — automatické ukončení
            self._log.warning(
                "Long press safety timeout (%ss) reached for %s — auto-releasing",
                MAX_LONG_PRESS_DURATION, key
            )
            self._trigger_event("long_press_stop", data)
            state.long_press_started = False
            state.press_count = 0
        except asyncio.CancelledError:
            return

    async def _finalize_presses(self, key: str) -> None:
        """Determine if the sequence was short, double or triple press."""
        state = self._button_states[key]
        try:
            await asyncio.sleep(self._multi_press_window)
        except asyncio.CancelledError:
            return

        count = state.press_count
        data = state.last_event_data
        event_map = {1: "short_press", 2: "double_press", 3: "triple_press"}
        if event_name := event_map.get(count):
            self._trigger_event(event_name, data)

        state.press_count = 0
        state.finalize_task = None
