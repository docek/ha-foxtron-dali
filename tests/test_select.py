import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT, EntityCategory

sys.path.append(str(Path(__file__).resolve().parents[1]))

import custom_components.foxtron_dali.helpers as helpers_module
import custom_components.foxtron_dali.select as select_module
from custom_components.foxtron_dali.select import (
    DEFAULT_FADE_PROFILE_OPTION,
    FADE_PROFILE_SECONDS,
    DaliFadeProfileSelect,
)
from custom_components.foxtron_dali.light import DaliLight
from custom_components.foxtron_dali.const import DOMAIN


def _make_entry(entry_id: str = "bus1"):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {CONF_HOST: "1.2.3.4", CONF_PORT: 23}
    entry.async_on_unload = MagicMock()
    return entry


def _make_select(address: int = 5, last_state=None) -> DaliFadeProfileSelect:
    driver = MagicMock()
    driver.fade_profile_seconds = {}
    select = DaliFadeProfileSelect(driver, address, _make_entry())
    select.async_write_ha_state = MagicMock()
    if last_state is None:
        select.async_get_last_state = AsyncMock(return_value=None)
    else:
        state = MagicMock()
        state.state = last_state
        select.async_get_last_state = AsyncMock(return_value=state)
    return select


def test_select_identity_matches_light_device():
    """The fade profile select lives on the same device as the light.

    The unique_id keeps the historical "_fade_time" suffix so the
    registry entry survived the repurpose from fade time to profile."""
    select = _make_select(address=5)
    light = DaliLight(MagicMock(), address=5, entry=_make_entry())

    assert select.unique_id == "1.2.3.4_23_5_fade_time"
    assert select.entity_category == EntityCategory.CONFIG
    assert select.device_info["identifiers"] == light.device_info["identifiers"]


def test_options_and_default():
    """A small human-scale option set with a sane default."""
    select = _make_select()
    assert select.options == list(FADE_PROFILE_SECONDS)
    assert DEFAULT_FADE_PROFILE_OPTION in FADE_PROFILE_SECONDS
    assert FADE_PROFILE_SECONDS["No fade"] == 0.0
    assert FADE_PROFILE_SECONDS[DEFAULT_FADE_PROFILE_OPTION] == 2.0


@pytest.mark.asyncio
async def test_restore_adopts_last_valid_option():
    """A previously selected profile is restored and published."""
    select = _make_select(address=5, last_state="0.7 s")

    await select.async_added_to_hass()

    assert select.current_option == "0.7 s"
    assert select._driver.fade_profile_seconds[5] == 0.7


@pytest.mark.asyncio
async def test_restore_without_state_uses_default():
    """First boot (or invalid restore) falls back to the 2.0 s default.

    The pre-0.13 select stored fade codes as e.g. "2.0 s" too, but any
    stale label outside the new option set must also map to the default."""
    for last_state in (None, "90.5 s", "unavailable"):
        select = _make_select(address=5, last_state=last_state)

        await select.async_added_to_hass()

        assert select.current_option == DEFAULT_FADE_PROFILE_OPTION
        assert select._driver.fade_profile_seconds[5] == 2.0


@pytest.mark.asyncio
async def test_select_option_publishes_without_bus_traffic():
    """The profile is an integration-side setting: no NVM write, no query."""
    select = _make_select(address=5)

    await select.async_select_option("2.8 s")

    assert select.current_option == "2.8 s"
    assert select._driver.fade_profile_seconds[5] == 2.8
    select._driver.set_fade_time.assert_not_called()
    select._driver.query_fade_time.assert_not_called()


@pytest.mark.asyncio
async def test_setup_creates_selects_for_scanned_and_registry(monkeypatch):
    """One fade profile select per known light address (scan + registry)."""
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
