from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT

import custom_components.foxtron_dali as foxtron_dali


def _make_hass():
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.data = {}
    return hass


def _make_entry(entry_id: str, host: str, options: dict):
    entry = MagicMock(entry_id=entry_id, options=options)
    entry.data = {CONF_HOST: host, CONF_PORT: 23}
    entry.async_unload = AsyncMock(return_value=True)
    return entry


async def _run_setup(hass, entry, driver, has_service=False):
    registered = []
    with (
        patch("custom_components.foxtron_dali.FoxtronDaliDriver", return_value=driver),
        patch("custom_components.foxtron_dali.dr.async_get") as mock_dr,
        patch.object(hass.services, "has_service", return_value=has_service),
        patch.object(
            hass.services,
            "async_register",
            side_effect=lambda domain, name, *a, **kw: registered.append(name),
        ),
    ):
        device_registry = MagicMock()
        mock_dr.return_value = device_registry
        device_registry.async_get_or_create = MagicMock()

        assert await foxtron_dali.async_setup_entry(hass, entry)
    return registered


@pytest.mark.asyncio
async def test_setup_entry_copies_existing_options():
    """A new entry inherits options from existing entries."""
    hass = _make_hass()
    existing = _make_entry("1", "1.1.1.1", {"long_press_threshold": 0.3})
    new = _make_entry("2", "2.2.2.2", {})
    hass.config_entries.async_entries.return_value = [existing, new]

    await _run_setup(hass, new, AsyncMock())

    hass.config_entries.async_update_entry.assert_any_call(
        new, options={"long_press_threshold": 0.3}
    )


@pytest.mark.asyncio
async def test_setup_never_writes_fade_time():
    """Fade time lives in ballast NVM; setup must not overwrite it."""
    hass = _make_hass()
    entry = _make_entry("1", "1.1.1.1", {"fade_rate_restored": True})
    hass.config_entries.async_entries.return_value = [entry]
    driver = AsyncMock()

    await _run_setup(hass, entry, driver)

    driver.set_fade_time.assert_not_called()


@pytest.mark.asyncio
async def test_setup_restores_fade_rate_once():
    """First setup after upgrade broadcasts fade rate 7 (DALI default) once.

    Earlier releases wrote an invalid fade rate to all ballasts on every
    start; the one-shot remediation restores the default and records a
    flag in entry options so it never runs again.
    """
    hass = _make_hass()
    entry = _make_entry("1", "1.1.1.1", {})
    hass.config_entries.async_entries.return_value = [entry]
    driver = AsyncMock()

    await _run_setup(hass, entry, driver)

    driver.set_fade_rate.assert_awaited_once_with(7)
    hass.config_entries.async_update_entry.assert_any_call(
        entry, options={"fade_rate_restored": True}
    )


@pytest.mark.asyncio
async def test_setup_skips_fade_rate_restore_when_flagged():
    """The remediation flag prevents repeated NVM writes on every start."""
    hass = _make_hass()
    entry = _make_entry("1", "1.1.1.1", {"fade_rate_restored": True})
    hass.config_entries.async_entries.return_value = [entry]
    driver = AsyncMock()

    await _run_setup(hass, entry, driver)

    driver.set_fade_rate.assert_not_called()


@pytest.mark.asyncio
async def test_device_removal_allowed_except_bus_device():
    """Users may delete per-light/switch devices, never the bus device.

    Without async_remove_config_entry_device HA offers no delete button
    at all, so orphaned devices (e.g. former phantoms, gear removed from
    the bus) could not be cleaned up.
    """
    entry = _make_entry("bus1", "1.1.1.1", {})

    bus_device = MagicMock()
    bus_device.identifiers = {("foxtron_dali", "bus1")}
    assert (
        await foxtron_dali.async_remove_config_entry_device(
            MagicMock(), entry, bus_device
        )
        is False
    )

    light_device = MagicMock()
    light_device.identifiers = {("foxtron_dali", "1.1.1.1_23_light_5")}
    assert (
        await foxtron_dali.async_remove_config_entry_device(
            MagicMock(), entry, light_device
        )
        is True
    )


@pytest.mark.asyncio
async def test_registered_services():
    """Only scan_for_lights and remove_paired_switch are registered."""
    hass = _make_hass()
    entry = _make_entry("1", "1.1.1.1", {"fade_rate_restored": True})
    hass.config_entries.async_entries.return_value = [entry]

    registered = await _run_setup(hass, entry, AsyncMock())

    assert sorted(registered) == ["remove_paired_switch", "scan_for_lights"]


@pytest.mark.asyncio
async def test_options_copy_excludes_remediation_flag():
    """A new bus entry must run its own fade-rate remediation.

    Regression risk: copying fade_rate_restored from an existing entry
    would skip the one-shot remediation on a newly added gateway whose
    ballasts may still hold the invalid pre-0.7.3 fade rate.
    """
    hass = _make_hass()
    existing = _make_entry(
        "1", "1.1.1.1", {"long_press_threshold": 0.3, "fade_rate_restored": True}
    )
    new = _make_entry("2", "2.2.2.2", {})
    hass.config_entries.async_entries.return_value = [existing, new]
    driver = AsyncMock()

    await _run_setup(hass, new, driver)

    hass.config_entries.async_update_entry.assert_any_call(
        new, options={"long_press_threshold": 0.3}
    )
    driver.set_fade_rate.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_unload_entry_disconnects_and_removes_services():
    """Unload pops the driver, disconnects it, and (as the last entry)
    deregisters the global services."""
    hass = _make_hass()
    entry = _make_entry("e1", "1.1.1.1", {})
    driver = AsyncMock()
    hass.data = {"foxtron_dali": {"e1": driver}}
    hass.config_entries.async_forward_entry_unload = AsyncMock(return_value=True)
    removed = []
    hass.services.async_remove = lambda domain, name: removed.append(name)

    assert await foxtron_dali.async_unload_entry(hass, entry)

    driver.disconnect.assert_awaited_once()
    assert "foxtron_dali" not in hass.data
    assert sorted(removed) == ["remove_paired_switch", "scan_for_lights"]


@pytest.mark.asyncio
async def test_unload_entry_keeps_services_while_other_entries_remain():
    """Services survive unloading one of several entries."""
    hass = _make_hass()
    entry = _make_entry("e1", "1.1.1.1", {})
    driver, other = AsyncMock(), AsyncMock()
    hass.data = {"foxtron_dali": {"e1": driver, "e2": other}}
    hass.config_entries.async_forward_entry_unload = AsyncMock(return_value=True)
    removed = []
    hass.services.async_remove = lambda domain, name: removed.append(name)

    assert await foxtron_dali.async_unload_entry(hass, entry)

    driver.disconnect.assert_awaited_once()
    other.disconnect.assert_not_called()
    assert removed == []
