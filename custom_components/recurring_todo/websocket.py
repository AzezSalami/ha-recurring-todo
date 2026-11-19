"""WebSocket API used by the Recurring To-do sidebar panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import DOMAIN
from .manager import RecurringTodoManager, available_notify_services
from .todo import ADD_TASK_SCHEMA, task_to_service_dict

TASK_SCHEMA = vol.Schema(ADD_TASK_SCHEMA)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the WebSocket commands."""
    websocket_api.async_register_command(hass, ws_lists)
    websocket_api.async_register_command(hass, ws_notify_targets)
    websocket_api.async_register_command(hass, ws_tasks)
    websocket_api.async_register_command(hass, ws_create_task)
    websocket_api.async_register_command(hass, ws_update_task)
    websocket_api.async_register_command(hass, ws_delete_task)
    websocket_api.async_register_command(hass, ws_run_task)


def _manager(hass: HomeAssistant, connection, msg: dict[str, Any]) -> RecurringTodoManager | None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], "not_found", "List not found")
        return None
    return entry.runtime_data


def _task_out(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task["task_id"],
        "name": task["name"],
        "frequency": task["frequency"],
        "interval": task.get("interval", 1),
        "weekdays": task.get("weekdays") or [],
        "day_of_month": task.get("day_of_month"),
        "time": task.get("time"),
        "start_date": task.get("start_date"),
        "description": task.get("description"),
        "notify": task.get("notify", True),
        # Empty = the list's default devices.
        "notify_services": task.get("notify_services") or [],
        "next_due": task.get("next_due"),
    }


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/lists"})
@callback
def ws_lists(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """List all loaded recurring to-do lists."""
    registry = er.async_get(hass)
    lists = [
        {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "entity_id": registry.async_get_entity_id("todo", DOMAIN, entry.entry_id),
            "task_count": len(entry.runtime_data.tasks),
            "notify_services": entry.runtime_data.notify_services,
        }
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    connection.send_result(msg["id"], sorted(lists, key=lambda x: x["title"].lower()))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/notify_targets"})
@callback
def ws_notify_targets(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Notify services that can receive notifications, with readable names."""
    phones = {}
    for entry in hass.config_entries.async_entries("mobile_app"):
        device = entry.data.get("device_name") or entry.title
        if device:
            phones[f"notify.mobile_app_{slugify(device)}"] = device
    targets = [
        {
            "service": svc,
            "name": phones.get(svc) or _pretty(svc),
            "phone": svc.startswith("notify.mobile_app_"),
        }
        for svc in available_notify_services(hass)
    ]
    connection.send_result(msg["id"], targets)


def _pretty(service: str) -> str:
    name = service.removeprefix("notify.").removeprefix("mobile_app_")
    return name.replace("_", " ").strip().capitalize() or service


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/tasks", vol.Required("entry_id"): str}
)
@callback
def ws_tasks(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """List the recurring tasks of one list."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    tasks = sorted(manager.tasks.values(), key=lambda t: (t.get("next_due") or "~", t["name"].lower()))
    connection.send_result(msg["id"], [_task_out(t) for t in tasks])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/task/create",
        vol.Required("entry_id"): str,
        vol.Required("task"): TASK_SCHEMA,
    }
)
@websocket_api.async_response
async def ws_create_task(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Add a recurring task."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    task = task_to_service_dict(msg["task"])
    if error := manager.validate_task(task):
        connection.send_error(msg["id"], error, error)
        return
    created = await manager.async_add_task(task)
    connection.send_result(msg["id"], _task_out(created))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/task/update",
        vol.Required("entry_id"): str,
        vol.Required("task_id"): str,
        vol.Required("task"): TASK_SCHEMA,
    }
)
@websocket_api.async_response
async def ws_update_task(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Change a recurring task."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    if msg["task_id"] not in manager.tasks:
        connection.send_error(msg["id"], "not_found", "Task not found")
        return
    task = task_to_service_dict(msg["task"])
    if error := manager.validate_task(task, exclude_id=msg["task_id"]):
        connection.send_error(msg["id"], error, error)
        return
    updated = await manager.async_update_task(msg["task_id"], task)
    connection.send_result(msg["id"], _task_out(updated))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/task/delete",
        vol.Required("entry_id"): str,
        vol.Required("task_id"): str,
    }
)
@websocket_api.async_response
async def ws_delete_task(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Remove a recurring task (items already on the list stay)."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    if msg["task_id"] not in manager.tasks:
        connection.send_error(msg["id"], "not_found", "Task not found")
        return
    await manager.async_remove_task(msg["task_id"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/task/run",
        vol.Required("entry_id"): str,
        vol.Required("task_id"): str,
    }
)
@websocket_api.async_response
async def ws_run_task(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Put the task on the list and notify now, without changing its schedule."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    if msg["task_id"] not in manager.tasks:
        connection.send_error(msg["id"], "not_found", "Task not found")
        return
    await manager.async_run_task(msg["task_id"])
    connection.send_result(msg["id"])
