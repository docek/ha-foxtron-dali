import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT, EntityCategory
from homeassistant.exceptions import HomeAssistantError

sys.path.append(str(Path(__file__).resolve().parents[1]))

import custom_components.foxtron_dali.helpers as helpers_module
import custom_components.foxtron_dali.select as select_module
from custom_components.foxtron_dali.select import (
    DaliFadeTimeSelect,
    OPTION_BY_CODE,
)
from custom_components.foxtron_dali.light import DaliLight
from custom_components.foxtron_dali.const import DOMAIN


def _make_entry(entry_id: str = "bus1"):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
    entry.async_on_unload = MagicMock()
    return entry


def _make_select(address: int = 5, fade_code=None) -> DaliFadeTimeSelect:
    driver = MagicMock()
    driver.set_fade_time = AsyncMock()
    driver.query_fade_time = AsyncMock(return_value=fade_code)
    select = DaliFadeTimeSelect(driver, address, _make_entry())
    select.async_write_ha_state = MagicMock()
    return select


def test_select_identity_matches_light_device():
    """The fade select lives on the same per-light device as the light."""
    select = _make_select(address=5)
    light = DaliLight(MagicMock(), address=5, entry=_make_entry())

    assert select.unique_id == "1.2.3.4_23_5_fade_time"
    assert select.entity_category == EntityCategory.CONFIG
    assert select.device_info["identifiers"] == light.device_info["identifiers"]


def test_options_cover_all_fade_codes():
    """All 16 DALI fade codes are selectable, labels are human readable."""
    select = _make_select()
    assert len(select.options) == 16
    assert select.options == [OPTION_BY_CODE[code] for code in range(16)]
    # Sanity: labels carry seconds, not raw codes
    assert OPTION_BY_CODE[1] == "0.7 s"
    assert OPTION_BY_CODE[15] == "90.5 s"


@pytest.mark.asyncio
async def test_update_reads_fade_time_from_hardware():
    """The ballast NVM is the source of truth; HA only mirrors it."""
    select = _make_select(address=5, fade_code=4)

    await select.async_update()

    select._driver.query_fade_time.assert_awaited_once_with(5)
    assert select.current_option == OPTION_BY_CODE[4]


@pytest.mark.asyncio
async def test_update_without_response_keeps_unknown():
    """No reply from the ballast leaves the option unknown."""
    select = _make_select(fade_code=None)
    await select.async_update()
    assert select.current_option is None


@pytest.mark.asyncio
async def test_select_option_writes_and_verifies():
    """Selecting an option writes to the ballast and verifies by readback."""
    select = _make_select(address=5, fade_code=4)

    await select.async_select_option(OPTION_BY_CODE[4])

    select._driver.set_fade_time.assert_awaited_once_with(4, short_address=5)
    assert select.current_option == OPTION_BY_CODE[4]


@pytest.mark.asyncio
async def test_select_option_mismatch_raises_and_shows_reality():
    """A readback mismatch raises and the entity shows the actual value."""
    select = _make_select(address=5, fade_code=2)  # ballast reports 2

    with pytest.raises(HomeAssistantError):
        await select.async_select_option(OPTION_BY_CODE[4])

    assert select.current_option == OPTION_BY_CODE[2]


@pytest.mark.asyncio
async def test_availability_follows_driver_connection():
    """Selects go unavailable on disconnect and recover on reconnect."""
    select = _make_select(fade_code=4)
    select.hass = MagicMock()

    select._handle_driver_disconnect()
    assert select.available is False

    select._handle_driver_connect()
    assert select.available is True


@pytest.mark.asyncio
async def test_setup_creates_selects_for_scanned_and_registry(monkeypatch):
    """One fade select per known light address (scan + registry seed)."""
    driver = MagicMock()
    driver.scan_for_devices = AsyncMock(return_value=[1, 2])
    entry = _make_entry("e1")

    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": driver}}
    tasks = []
    hass.async_create_task = lambda coro: (
        tasks.append(asyncio.ensure_future(coro)) or tasks[-1]
    )

    added = []
    monkeypatch.setattr(
        helpers_module, "async_dispatcher_connect", lambda h, s, t: MagicMock()
    )
    monkeypatch.setattr(helpers_module, "registry_light_addresses", lambda h, e: {1, 5})

    await select_module.async_setup_entry(hass, entry, lambda e: added.extend(e))
    await tasks[0]

    assert sorted(s._address for s in added) == [1, 2, 5]
    # Reuses the cached scan from the light platform, no forced rescan
    driver.scan_for_devices.assert_awaited_with(refresh=False)


@pytest.mark.asyncio
async def test_reconnect_does_not_reread_fade_time():
    """Fade time is static NVM data — re-reading 138 selects after every
    TCP blip doubled the reconnect query storm for no information."""
    select = _make_select(fade_code=4)
    tasks = []
    select.hass = MagicMock()
    select.hass.async_create_task = lambda coro: (
        tasks.append(asyncio.ensure_future(coro)) or tasks[-1]
    )

    select._handle_driver_disconnect()
    assert select.available is False
    select._handle_driver_connect()
    assert select.available is True

    await asyncio.sleep(0)
    for t in tasks:
        t.cancel()
    assert not tasks, "no fade re-read on reconnect"
