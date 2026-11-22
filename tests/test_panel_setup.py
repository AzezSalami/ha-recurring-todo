"""The sidebar panel is added once, even when several lists start at the same time."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import DOMAIN


def _entry(hass, title):
    e = MockConfigEntry(
        domain=DOMAIN, title=title, data={},
        options={"notify_services": [], "later_minutes": 30},
    )
    e.add_to_hass(hass)
    return e


async def test_two_lists_start_together(hass: HomeAssistant):
    panels: dict[str, str] = {}

    async def fake_register(hass_, *, frontend_url_path, **kwargs):
        await asyncio.sleep(0)  # give the other list a chance to run, like the real awaits do
        if frontend_url_path in panels:
            raise ValueError(f"Overwriting panel {frontend_url_path} owned by custom")
        panels[frontend_url_path] = kwargs["module_url"]

    hass.config.components.add("frontend")
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    a, b = _entry(hass, "Chores"), _entry(hass, "Garden")
    with (
        patch("homeassistant.components.panel_custom.async_register_panel", side_effect=fake_register) as reg,
        patch("homeassistant.components.frontend.async_remove_panel", side_effect=lambda h, p, **kw: panels.pop(p)),
    ):
        results = await asyncio.gather(
            hass.config_entries.async_setup(a.entry_id),
            hass.config_entries.async_setup(b.entry_id),
        )
        await hass.async_block_till_done()
        assert results == [True, True]
        assert a.state is ConfigEntryState.LOADED and b.state is ConfigEntryState.LOADED
        assert reg.call_count == 1
        assert hass.http.async_register_static_paths.call_count == 1
        assert list(panels) == ["recurring-todo"]

        # Panel stays while one list is loaded, goes with the last one, comes back on reload.
        await hass.config_entries.async_unload(a.entry_id)
        assert list(panels) == ["recurring-todo"]
        await hass.config_entries.async_unload(b.entry_id)
        assert panels == {}
        assert await hass.config_entries.async_setup(a.entry_id)
        await hass.async_block_till_done()
        assert list(panels) == ["recurring-todo"]
        assert hass.http.async_register_static_paths.call_count == 1


async def test_panel_error_does_not_block_the_list(hass: HomeAssistant):
    hass.config.components.add("frontend")
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    e = _entry(hass, "Chores")
    with patch(
        "homeassistant.components.panel_custom.async_register_panel",
        side_effect=ValueError("Overwriting panel recurring-todo owned by custom"),
    ):
        assert await hass.config_entries.async_setup(e.entry_id)
        await hass.async_block_till_done()
    assert e.state is ConfigEntryState.LOADED
    assert hass.states.get("todo.chores") is not None
