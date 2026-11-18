"""Recurring To-do: a to-do list with recurring tasks and actionable notifications."""

from __future__ import annotations

from pathlib import Path

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import websocket
from .const import DOMAIN
from .manager import RecurringTodoManager

PLATFORMS = [Platform.TODO]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PANEL_URL_PATH = "recurring-todo"
PANEL_COMPONENT = "recurring-todo-panel"
STATIC_URL = f"/{DOMAIN}_static"
FRONTEND_DIR = Path(__file__).parent / "frontend"

type RecurringTodoConfigEntry = ConfigEntry[RecurringTodoManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the WebSocket API used by the sidebar panel."""
    websocket.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RecurringTodoConfigEntry) -> bool:
    """Set up a Recurring To-do list from a config entry."""
    manager = RecurringTodoManager(hass, entry)
    await manager.async_load()
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await manager.async_start()
    entry.async_on_unload(manager.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await _async_register_panel(hass)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when settings change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: RecurringTodoConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    others = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED
    ]
    if unloaded and not others:
        _async_remove_panel(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete stored data when the list is removed."""
    from homeassistant.helpers.storage import Store

    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Add the "Recurring To-do" sidebar panel (once, for all lists)."""
    if "frontend" not in hass.config.components or hass.http is None:
        return  # e.g. a minimal setup without the web UI
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("panel"):
        return
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    if not data.get("static"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
        )
        data["static"] = True

    version = (await async_get_integration(hass, DOMAIN)).version
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        module_url=f"{STATIC_URL}/{PANEL_COMPONENT}.js?v={version}",
        sidebar_title="Recurring To-do",
        sidebar_icon="mdi:calendar-sync",
        require_admin=False,
    )
    data["panel"] = True


def _async_remove_panel(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN, {})
    if not data.get("panel"):
        return
    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    data["panel"] = False
