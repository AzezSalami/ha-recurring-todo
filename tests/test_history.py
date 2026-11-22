"""History of reactions to notifications."""

from datetime import timedelta

from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import MockUser

from .conftest import DOMAIN, ENTITY
from .test_integration import items


async def _ws(client, msg):
    await client.send_json_auto_id(msg)
    return await client.receive_json()


async def _press(hass, call, button, user=None, **extra):
    """Press button 0 (Done), 1 (Later) or 2 (Not doing) of a sent notification."""
    action = call.data["data"]["actions"][button]["action"]
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": action, **extra},
        context=Context(user_id=user.id if user else None),
    )
    await hass.async_block_till_done()


async def test_presses_are_recorded(hass: HomeAssistant, entry, notify_calls, freezer, hass_ws_client):
    anna = MockUser(name="Anna").add_to_hass(hass)
    manager = entry.runtime_data
    events = []
    hass.bus.async_listen(f"{DOMAIN}_action", events.append)

    task = await manager.async_add_task({"name": "Plants", "frequency": "daily"})
    await manager.async_run_task(task["task_id"])
    await hass.async_block_till_done()

    freezer.tick(timedelta(minutes=12))
    await _press(hass, notify_calls[0], 1, anna, sourceDeviceName="Anna's iPhone")  # Later
    freezer.tick(timedelta(minutes=31))
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    reminder = [c for c in notify_calls if c.data["message"] == "Plants"][-1]
    freezer.tick(timedelta(minutes=2))
    await _press(hass, reminder, 0)  # Done, no user known
    await _press(hass, reminder, 0)  # pressed again on another phone: ignored

    h = manager.history
    assert [x["action"] for x in h] == ["later", "done"]
    assert h[0]["user"] == "Anna" and h[0]["user_id"] == anna.id
    assert h[0]["device"] == "Anna's iPhone"
    assert h[0]["response_seconds"] == 12 * 60
    assert h[0]["item"] == "Plants" and h[0]["task_id"] == task["task_id"]
    assert h[1]["user"] is None and h[1]["device"] is None
    assert h[1]["response_seconds"] == 2 * 60  # measured from the reminder, not the first one

    # The event carries who pressed it, and keeps the user's context for the Logbook
    assert [e.data["action"] for e in events] == ["later", "done"]
    assert events[0].data["user"] == "Anna" and events[0].data["entity_id"] == ENTITY
    assert events[0].context.user_id == anna.id

    # Survives a restart
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert [x["action"] for x in entry.runtime_data.history] == ["later", "done"]

    client = await hass_ws_client(hass)
    r = await _ws(client, {"type": f"{DOMAIN}/history", "entry_id": entry.entry_id})
    assert [x["action"] for x in r["result"]] == ["done", "later"]  # newest first
    r = await _ws(client, {"type": f"{DOMAIN}/history", "entry_id": entry.entry_id, "limit": 1})
    assert [x["action"] for x in r["result"]] == ["done"]

    r = await _ws(client, {"type": f"{DOMAIN}/history/clear", "entry_id": entry.entry_id})
    assert r["success"]
    assert entry.runtime_data.history == []


async def test_skip_and_already_removed(hass: HomeAssistant, entry, notify_calls):
    manager = entry.runtime_data
    t1 = await manager.async_add_task({"name": "Bins", "frequency": "daily"})
    t2 = await manager.async_add_task({"name": "Dishes", "frequency": "daily"})
    await manager.async_run_task(t1["task_id"])
    await manager.async_run_task(t2["task_id"])
    await hass.async_block_till_done()
    bins, dishes = notify_calls[0], notify_calls[1]

    await _press(hass, bins, 2)  # Not doing
    uid = next(i["uid"] for i in await items(hass) if i["summary"] == "Dishes")
    await hass.services.async_call("todo", "remove_item", {"entity_id": ENTITY, "item": uid}, blocking=True)
    await _press(hass, dishes, 0)  # Done, but it's already gone

    h = manager.history
    assert [(x["item"], x["action"], x["note"]) for x in h] == [
        ("Bins", "skip", None),
        ("Dishes", "done", "not_on_list"),
    ]


async def test_subscription_and_admin_only_clear(hass: HomeAssistant, entry, notify_calls, hass_ws_client, hass_admin_user):
    client = await hass_ws_client(hass)
    r = await _ws(client, {"type": f"{DOMAIN}/subscribe", "entry_id": entry.entry_id})
    assert r["success"]
    sub_id = r["id"]
    task = await entry.runtime_data.async_add_task({"name": "Plants", "frequency": "daily"})
    msg = await client.receive_json()
    assert msg["id"] == sub_id and msg["type"] == "event" and msg["event"] == {"changed": True}

    hass_admin_user.groups = []  # make the test user a normal user
    r = await _ws(client, {"type": f"{DOMAIN}/history/clear", "entry_id": entry.entry_id})
    assert not r["success"] and r["error"]["code"] == "unauthorized"
    assert task["task_id"] in entry.runtime_data.tasks


async def test_logbook_text(hass: HomeAssistant, entry):
    from custom_components.recurring_todo.logbook import async_describe_events

    described = {}
    async_describe_events(hass, lambda domain, event_type, fn: described.setdefault(event_type, fn))
    fn = described[f"{DOMAIN}_action"]

    class E:
        data = {"entry_id": entry.entry_id, "entity_id": ENTITY, "action": "skip",
                "item": "Bins", "user": "Anna", "device": "Pixel 8"}

    out = fn(E())
    assert out["name"] == "Anna"
    assert out["message"] == "chose not to do “Bins” on Pixel 8"
    assert out["entity_id"] == ENTITY
    E.data = {"entry_id": entry.entry_id, "action": "done", "item": "Bins"}
    assert fn(E())["name"] == "Chores"
