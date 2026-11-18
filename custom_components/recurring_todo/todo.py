"""To-do list entity for Recurring To-do."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DAY_OF_MONTH,
    ATTR_DESCRIPTION,
    ATTR_FREQUENCY,
    ATTR_INTERVAL,
    ATTR_NAME,
    ATTR_NOTIFY,
    ATTR_START_DATE,
    ATTR_TIME,
    ATTR_WEEKDAYS,
    DOMAIN,
    FREQUENCIES,
    SERVICE_ADD_TASK,
    SERVICE_REMOVE_TASK,
    SERVICE_RUN_TASK,
    WEEKDAYS,
)
from .manager import STATUS_DONE, STATUS_OPEN, RecurringTodoManager

ADD_TASK_SCHEMA = {
    vol.Required(ATTR_NAME): cv.string,
    vol.Required(ATTR_FREQUENCY): vol.In(FREQUENCIES),
    vol.Optional(ATTR_INTERVAL, default=1): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional(ATTR_WEEKDAYS): vol.All(cv.ensure_list, [vol.In(WEEKDAYS)]),
    vol.Optional(ATTR_DAY_OF_MONTH): vol.All(vol.Coerce(int), vol.Range(min=1, max=31)),
    vol.Optional(ATTR_TIME, default="09:00:00"): cv.time,
    vol.Optional(ATTR_START_DATE): cv.date,
    vol.Optional(ATTR_NOTIFY, default=True): cv.boolean,
    vol.Optional(ATTR_DESCRIPTION): cv.string,
}
TASK_KEY_SCHEMA = {vol.Required(ATTR_NAME): cv.string}


def _parse_due(value: str | None) -> date | datetime | None:
    if not value:
        return None
    if "T" in value or " " in value:
        return dt_util.as_local(datetime.fromisoformat(value))
    return date.fromisoformat(value)


def task_to_service_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Normalise task data coming from a service call or the UI."""
    task = {k: v for k, v in data.items() if v is not None and v != ""}
    if "time" in task and not isinstance(task["time"], str):
        task["time"] = task["time"].strftime("%H:%M:%S")
    if "start_date" in task and isinstance(task["start_date"], date):
        task["start_date"] = task["start_date"].isoformat()
    if "day_of_month" in task:
        task["day_of_month"] = int(task["day_of_month"])
    if "interval" in task:
        task["interval"] = int(task["interval"])
    return task


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the to-do list entity."""
    manager: RecurringTodoManager = entry.runtime_data
    async_add_entities([RecurringTodoListEntity(manager, entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_ADD_TASK, ADD_TASK_SCHEMA, "async_service_add_task",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_REMOVE_TASK, TASK_KEY_SCHEMA, "async_service_remove_task"
    )
    platform.async_register_entity_service(
        SERVICE_RUN_TASK, TASK_KEY_SCHEMA, "async_service_run_task"
    )


class RecurringTodoListEntity(TodoListEntity):
    """A to-do list that also owns recurring tasks."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:calendar-sync"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, manager: RecurringTodoManager, entry: ConfigEntry) -> None:
        self._manager = manager
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "entry_type": "service",
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._manager.async_add_listener(self.async_write_ha_state)
        )

    @property
    def todo_items(self) -> list[TodoItem]:
        return [
            TodoItem(
                summary=i["summary"],
                uid=i["uid"],
                status=TodoItemStatus(i["status"]),
                due=_parse_due(i.get("due")),
                description=i.get("description"),
            )
            for i in self._manager.items
        ]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "recurring_tasks": [
                {
                    "name": t["name"],
                    "frequency": t["frequency"],
                    "interval": t.get("interval", 1),
                    "weekdays": t.get("weekdays"),
                    "day_of_month": t.get("day_of_month"),
                    "time": t.get("time"),
                    "next_due": t.get("next_due"),
                    "notify": t.get("notify", True),
                }
                for t in self._manager.tasks.values()
            ]
        }

    # ----------------------------------------------------- todo list methods
    @staticmethod
    def _due_str(item: TodoItem) -> str | None:
        if item.due is None:
            return None
        return item.due.isoformat()

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self._manager.async_create_item(
            item.summary or "",
            due=self._due_str(item),
            description=item.description,
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        status = STATUS_DONE if item.status == TodoItemStatus.COMPLETED else STATUS_OPEN
        await self._manager.async_update_item(
            item.uid,
            summary=item.summary,
            status=status,
            due=self._due_str(item),
            description=item.description,
        )

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        await self._manager.async_delete_items(uids)

    async def async_move_todo_item(
        self, uid: str, previous_uid: str | None = None
    ) -> None:
        await self._manager.async_move_item(uid, previous_uid)

    # -------------------------------------------------------------- services
    async def async_service_add_task(self, **kwargs: Any) -> dict[str, Any]:
        task = task_to_service_dict(kwargs)
        if self._manager.find_task(task["name"]):
            raise ServiceValidationError(
                f"A recurring task named '{task['name']}' already exists"
            )
        created = await self._manager.async_add_task(task)
        return {"task_id": created["task_id"], "next_due": created["next_due"]}

    async def async_service_remove_task(self, name: str) -> None:
        try:
            await self._manager.async_remove_task(name)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def async_service_run_task(self, name: str) -> None:
        try:
            await self._manager.async_run_task(name)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
