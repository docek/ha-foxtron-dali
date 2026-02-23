import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_BUTTON_ACTION, PRESS_TYPES, FLAPS

# Očekáváme trigger typ ve formátu např. "upper_short_press", "lower_double_press"
TRIGGER_TYPES = set()
for flap in FLAPS:
    for press in PRESS_TYPES:
        TRIGGER_TYPES.add(f"{flap}_{press}")

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    }
)

async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Return a list of triggers for a Foxtron DALI switch device."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)

    # Vracíme triggery pouze pro zařízení, která jsme zaregistrovali jako vypínače
    # (mají v identifikátoru formát domény a unikátního tuple)
    if not device:
        return []

    is_switch = False
    for identifier in device.identifiers:
        if identifier[0] == DOMAIN and "dali4sw" in identifier[1]:
            is_switch = True
            break
            
    if not is_switch:
        return []

    triggers = []
    for trigger_type in TRIGGER_TYPES:
        triggers.append(
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_TYPE: trigger_type,
            }
        )
    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> callback:
    """Attach a trigger."""
    # HA UI konfigurace obsahuje CONF_TYPE ("upper_short_press" atd.)
    trigger_type = config[CONF_TYPE]
    
    # Rozebereme na flap a press_type
    flap = "upper" if trigger_type.startswith("upper_") else "lower"
    press_type = trigger_type.replace("upper_", "").replace("lower_", "")

    # Chceme poslouchat HA sběrnici na naši custom událost EVENT_BUTTON_ACTION
    # a spárovat to s device_id a konkrétním flap + press_type
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_BUTTON_ACTION,
            event_trigger.CONF_EVENT_DATA: {
                CONF_DEVICE_ID: config[CONF_DEVICE_ID],
                "flap": flap,
                "press_type": press_type,
            },
        }
    )

    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
