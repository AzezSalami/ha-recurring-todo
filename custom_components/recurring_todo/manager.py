"""State, scheduling and notification handling for Recurring To-do."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_DONE,
    ACTION_LATER,
    ACTION_PREFIX,
    ACTION_SKIP,
    CONF_LATER_MINUTES,
    CONF_NOTIFY_SERVICES,
    DEFAULT_LATER_MINUTES,
    DOMAIN,
    EVENT_ACTION_HANDLED,
    FREQ_MONTHLY,
    FREQ_WEEKLY,
    NOTIFICATION_ACTION_EVENT,
)
from .recurrence import next_occurrence

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# How many button presses are kept per list (oldest are dropped first).
HISTORY_LIMIT = 1000

STATUS_OPEN = "needs_action"
STATUS_DONE = "completed"


def _new_id() -> str:
    return uuid.uuid4().hex


def available_notify_services(hass: HomeAssistant) -> list[str]:
    """Notify services that can be picked, phones (mobile_app_*) first."""
    services = hass.services.async_services().get("notify", {})
    names = sorted(
        f"notify.{name}"
        for name in services
        if name not in ("send_message", "persistent_notification")
    )
    return sorted(names, key=lambda n: (not n.startswith("notify.mobile_app_"), n))


def _clean_services(services: Any) -> list[str]:
    """Return notify services as unique "notify.<name>" strings."""
    if not services:
        return []
    if isinstance(services, str):
        services = [services]
    out: list[str] = []
    for svc in services:
        name = str(svc).strip()
        if not name:
            continue
        if not name.startswith("notify."):
            name = f"notify.{name}"
        if name not in out:
            out.append(name)
    return out


class RecurringTodoManager:
    """Holds the list items and recurring tasks for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.items: list[dict[str, Any]] = []
        self.tasks: dict[str, dict[str, Any]] = {}
        # uid -> item info for notifications that may still be on a phone
        self.notified: dict[str, dict[str, Any]] = {}
        # Button presses on notifications, oldest first.
        self.history: list[dict[str, Any]] = []
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------------ setup
    @property
    def tz(self):
        return dt_util.get_default_time_zone()

    @property
    def notify_services(self) -> list[str]:
        opts = {**self.entry.data, **self.entry.options}
        return list(opts.get(CONF_NOTIFY_SERVICES, []))

    def services_for_task(self, task: dict[str, Any] | None) -> list[str]:
        """Devices a task notifies: its own choice, or the list's default."""
        if task and task.get("notify_services"):
            return list(task["notify_services"])
        return self.notify_services

    def services_for_item(self, item: dict[str, Any]) -> list[str]:
        task = self.tasks.get(item.get("task_id") or "")
        if task is not None:
            return self.services_for_task(task)
        sent = self.notified.get(item["uid"])
        if sent and sent.get("services"):
            return list(sent["services"])  # task was removed meanwhile
        return self.notify_services

    @property
    def later_minutes(self) -> int:
        opts = {**self.entry.data, **self.entry.options}
        return int(opts.get(CONF_LATER_MINUTES, DEFAULT_LATER_MINUTES))

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.items = data.get("items", [])
        self.tasks = data.get("tasks", {})
        self.notified = data.get("notified", {})
        self.history = data.get("history", [])

    async def async_save(self) -> None:
        await self._store.async_save(
            {
                "items": self.items,
                "tasks": self.tasks,
                "notified": self.notified,
                "history": self.history,
            }
        )

    async def async_start(self) -> None:
        self._unsubs.append(
            async_track_time_change(self.hass, self._async_tick, second=0)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                NOTIFICATION_ACTION_EVENT, self._async_handle_action
            )
        )
        # Catch up on anything missed while Home Assistant was off.
        await self._async_tick(dt_util.now())

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(update)

        @callback
        def _remove() -> None:
            self._listeners.remove(update)

        return _remove

    async def _async_changed(self) -> None:
        await self.async_save()
        for update in list(self._listeners):
            update()

    # ------------------------------------------------------------------ items
    def get_item(self, uid: str) -> dict[str, Any] | None:
        return next((i for i in self.items if i["uid"] == uid), None)

    def open_item_for_task(self, task_id: str) -> dict[str, Any] | None:
        return next(
            (
                i
                for i in self.items
                if i.get("task_id") == task_id and i["status"] == STATUS_OPEN
            ),
            None,
        )

    async def async_create_item(
        self,
        summary: str,
        *,
        due: str | None = None,
        description: str | None = None,
        task_id: str | None = None,
        uid: str | None = None,
    ) -> dict[str, Any]:
        item = {
            "uid": uid or _new_id(),
            "summary": summary,
            "status": STATUS_OPEN,
            "due": due,
            "description": description,
            "task_id": task_id,
            "snooze_until": None,
        }
        self.items.append(item)
        await self._async_changed()
        return item

    async def async_update_item(self, uid: str, **changes: Any) -> None:
        item = self.get_item(uid)
        if item is None:
            raise ValueError(f"Item {uid} not found")
        item.update(changes)
        if item["status"] == STATUS_DONE:
            item["snooze_until"] = None
        await self._async_changed()

    async def async_delete_items(self, uids: list[str]) -> None:
        self.items = [i for i in self.items if i["uid"] not in uids]
        await self._async_changed()

    async def async_move_item(self, uid: str, previous_uid: str | None) -> None:
        item = self.get_item(uid)
        if item is None:
            raise ValueError(f"Item {uid} not found")
        self.items.remove(item)
        if previous_uid is None:
            self.items.insert(0, item)
        else:
            prev = self.get_item(previous_uid)
            if prev is None:
                self.items.insert(0, item)
            else:
                self.items.insert(self.items.index(prev) + 1, item)
        await self._async_changed()

    # ------------------------------------------------------------------ tasks
    def _normalize_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Fill defaults, normalise formats and compute next_due."""
        task = dict(task)
        task.setdefault("task_id", _new_id())
        task.setdefault("interval", 1)
        task.setdefault("notify", True)
        task.setdefault("time", "09:00:00")
        services = _clean_services(task.get("notify_services"))
        if services:
            task["notify_services"] = services
        else:
            task.pop("notify_services", None)  # use the list's default
        if task.get("frequency") != FREQ_WEEKLY:
            task.pop("weekdays", None)
        if task.get("frequency") != FREQ_MONTHLY:
            task.pop("day_of_month", None)
        if not task.get("start_date"):
            today = dt_util.now().date()
            start = today
            if task.get("frequency") == FREQ_MONTHLY:
                # Anchor on the month of the next matching day, so "every 3
                # months on the 15th" added on the 20th starts next month.
                dom = int(task.get("day_of_month") or today.day)
                start = today.replace(day=1)
                if today.day > dom:
                    start = (start + timedelta(days=32)).replace(day=1)
            task["start_date"] = start.isoformat()
        if len(str(task["time"])) == 5:  # HH:MM
            task["time"] = f"{task['time']}:00"
        task["time"] = str(task["time"])
        if isinstance(task["start_date"], date):
            task["start_date"] = task["start_date"].isoformat()
        nxt = next_occurrence(task, dt_util.now() - timedelta(seconds=1), self.tz)
        task["next_due"] = nxt.isoformat() if nxt else None
        return task

    def validate_task(
        self, task: dict[str, Any], *, exclude_id: str | None = None, strict: bool = True
    ) -> str | None:
        """Return an error key for invalid task data, or None if it is fine.

        strict also requires weekdays for weekly tasks (the UI always asks for them;
        the add_task service falls back to the start date's weekday).
        """
        name = str(task.get("name") or "").strip()
        if not name:
            return "name_required"
        other = self.find_task(name)
        if other is not None and other["task_id"] != exclude_id:
            return "already_exists"
        if strict and task.get("frequency") == FREQ_WEEKLY and not task.get("weekdays"):
            return "weekdays_required"
        return None

    async def async_add_task(self, task: dict[str, Any]) -> dict[str, Any]:
        task = self._normalize_task(task)
        self.tasks[task["task_id"]] = task
        await self._async_changed()
        return task

    async def async_update_task(self, task_id: str, task: dict[str, Any]) -> dict[str, Any]:
        """Replace a task's settings (keeps its id) and reschedule it."""
        if task_id not in self.tasks:
            raise ValueError(f"Recurring task '{task_id}' not found")
        task = self._normalize_task({**task, "task_id": task_id})
        self.tasks[task_id] = task
        # Keep the open list item (if any) in line with the new name / notes.
        item = self.open_item_for_task(task_id)
        if item is not None:
            item["summary"] = task["name"]
            item["description"] = task.get("description")
            if item["uid"] in self.notified:
                self.notified[item["uid"]].update(
                    summary=task["name"], description=task.get("description")
                )
        await self._async_changed()
        return task

    def find_task(self, key: str) -> dict[str, Any] | None:
        if key in self.tasks:
            return self.tasks[key]
        key_l = key.strip().lower()
        return next(
            (t for t in self.tasks.values() if t["name"].strip().lower() == key_l),
            None,
        )

    async def async_remove_task(self, key: str) -> None:
        task = self.find_task(key)
        if task is None:
            raise ValueError(f"Recurring task '{key}' not found")
        del self.tasks[task["task_id"]]
        await self._async_changed()

    async def async_run_task(self, key: str) -> None:
        task = self.find_task(key)
        if task is None:
            raise ValueError(f"Recurring task '{key}' not found")
        await self._async_fire_task(task, dt_util.now())
        await self._async_changed()

    async def _async_fire_task(self, task: dict[str, Any], due: datetime) -> None:
        item = self.open_item_for_task(task["task_id"])
        if item is None:
            item = {
                "uid": _new_id(),
                "summary": task["name"],
                "status": STATUS_OPEN,
                "due": due.isoformat(),
                "description": task.get("description"),
                "task_id": task["task_id"],
                "snooze_until": None,
            }
            self.items.append(item)
        else:
            item["due"] = due.isoformat()
        if task.get("notify", True):
            await self.async_notify(item)

    # ------------------------------------------------------------- scheduler
    async def _async_tick(self, now: datetime) -> None:
        changed = False
        for task in list(self.tasks.values()):
            nd = task.get("next_due")
            if not nd:
                continue
            due = datetime.fromisoformat(nd)
            if due <= now:
                await self._async_fire_task(task, due)
                nxt = next_occurrence(task, now, self.tz)
                task["next_due"] = nxt.isoformat() if nxt else None
                changed = True

        for item in self.items:
            snooze = item.get("snooze_until")
            if snooze and datetime.fromisoformat(snooze) <= now:
                item["snooze_until"] = None
                changed = True
                if item["status"] == STATUS_OPEN:
                    await self.async_notify(item)

        if changed:
            await self._async_changed()

    # ---------------------------------------------------------- notifications
    async def async_notify(self, item: dict[str, Any]) -> None:
        uid = item["uid"]
        payload = {
            "title": self.entry.title,
            "message": item["summary"],
            "data": {
                "tag": f"{ACTION_PREFIX}_{uid}",
                "group": DOMAIN,
                "actions": [
                    {"action": f"{ACTION_PREFIX}_{ACTION_DONE}_{uid}", "title": "Done"},
                    {"action": f"{ACTION_PREFIX}_{ACTION_LATER}_{uid}", "title": "Later"},
                    {"action": f"{ACTION_PREFIX}_{ACTION_SKIP}_{uid}", "title": "Not doing"},
                ],
            },
        }
        services = self.services_for_item(item)
        self.notified[uid] = {
            "summary": item["summary"],
            "description": item.get("description"),
            "task_id": item.get("task_id"),
            "services": services,
            "sent_at": dt_util.now().isoformat(),
        }
        while len(self.notified) > 200:
            self.notified.pop(next(iter(self.notified)))
        for service in services:
            name = service.removeprefix("notify.")
            try:
                await self.hass.services.async_call(
                    "notify", name, payload, blocking=False
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to send notification via notify.%s", name)

    async def _async_clear_notification(
        self, uid: str, services: list[str] | None = None
    ) -> None:
        # Clear it on every phone it went to, plus the list's default ones.
        targets = _clean_services([*(services or []), *self.notify_services])
        for service in targets:
            name = service.removeprefix("notify.")
            if not name.startswith("mobile_app_"):
                continue
            try:
                await self.hass.services.async_call(
                    "notify",
                    name,
                    {
                        "message": "clear_notification",
                        "data": {"tag": f"{ACTION_PREFIX}_{uid}"},
                    },
                    blocking=False,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not clear notification on %s", name)

    async def _async_handle_action(self, event: Event) -> None:
        action = str(event.data.get("action", ""))
        parts = action.split("_", 2)
        if len(parts) != 3 or parts[0] != ACTION_PREFIX:
            return
        _, act, uid = parts
        if act not in (ACTION_DONE, ACTION_LATER, ACTION_SKIP):
            return
        item = self.get_item(uid)
        sent = self.notified.get(uid)
        if item is None and sent is None:
            # Not ours (another list) or long gone.
            return
        sent_to = list((sent or {}).get("services") or [])
        sent_at = (sent or {}).get("sent_at")
        summary = item["summary"] if item else sent["summary"]
        task_id = item.get("task_id") if item else sent.get("task_id")

        if item is None and act != ACTION_LATER:
            # Already removed from the list: just tidy up the phones.
            self.notified.pop(uid, None)
            await self._async_record(event, act, uid, summary, task_id, sent_at, note="not_on_list")
            await self._async_changed()
            await self._async_clear_notification(uid, sent_to)
            return
        if item is not None and act == ACTION_DONE and item["status"] == STATUS_DONE and sent is None:
            # Done pressed again (e.g. on a second phone): nothing changes.
            await self._async_clear_notification(uid, sent_to)
            return

        if item is None:
            # Removed from the list meanwhile: "Later" puts it back.
            item = {
                "uid": uid,
                "summary": sent["summary"],
                "status": STATUS_OPEN,
                "due": None,
                "description": sent.get("description"),
                "task_id": sent.get("task_id"),
                "snooze_until": None,
            }
            self.items.append(item)

        if act == ACTION_DONE:
            item["status"] = STATUS_DONE
            item["snooze_until"] = None
            self.notified.pop(uid, None)
        elif act == ACTION_SKIP:
            self.items.remove(item)
            self.notified.pop(uid, None)
        else:  # ACTION_LATER
            later = dt_util.now() + timedelta(minutes=self.later_minutes)
            item["status"] = STATUS_OPEN  # put it back on the list
            item["due"] = later.isoformat()
            item["snooze_until"] = later.isoformat()

        record = await self._async_record(event, act, uid, summary, task_id, sent_at)
        await self._async_changed()
        await self._async_clear_notification(uid, sent_to)
        self.hass.bus.async_fire(
            EVENT_ACTION_HANDLED,
            {
                "entry_id": self.entry.entry_id,
                "entity_id": self.entity_id,
                "action": act.lower(),
                "item": summary,
                "uid": uid,
                "task_id": task_id,
                "user_id": record["user_id"],
                "user": record["user"],
                "device": record["device"],
            },
            context=event.context,
        )

    # ---------------------------------------------------------------- history
    @property
    def entity_id(self) -> str | None:
        from homeassistant.helpers import entity_registry as er

        return er.async_get(self.hass).async_get_entity_id("todo", DOMAIN, self.entry.entry_id)

    async def _async_record(
        self,
        event: Event,
        act: str,
        uid: str,
        summary: str,
        task_id: str | None,
        sent_at: str | None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Add a button press to the history (does not save)."""
        user_id = event.context.user_id
        user_name = None
        if user_id:
            user = await self.hass.auth.async_get_user(user_id)
            user_name = user.name if user else None
        data = event.data
        # iOS sends the device name with the action; Android does not.
        device = data.get("sourceDeviceName") or data.get("device_name") or None
        now = dt_util.now()
        delay = None
        if sent_at:
            try:
                delay = max(0, round((now - datetime.fromisoformat(sent_at)).total_seconds()))
            except ValueError:
                delay = None
        record = {
            "time": now.isoformat(),
            "action": act.lower(),
            "item": summary,
            "task_id": task_id,
            "uid": uid,
            "user_id": user_id,
            "user": user_name,
            "device": device,
            "sent_at": sent_at,
            "response_seconds": delay,
            "note": note,
        }
        self.history.append(record)
        if len(self.history) > HISTORY_LIMIT:
            del self.history[: len(self.history) - HISTORY_LIMIT]
        return record

    async def async_clear_history(self) -> None:
        self.history = []
        await self._async_changed()
