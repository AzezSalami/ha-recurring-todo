from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import DOMAIN, ENTITY


async def items(hass):
    r = await hass.services.async_call(
        "todo", "get_items", {"entity_id": ENTITY}, blocking=True, return_response=True
    )
    return r[ENTITY]["items"]


async def test_config_flow(hass: HomeAssistant):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] is FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"name": "House", "notify_services": [], "later_minutes": 45}
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["options"]["later_minutes"] == 45
    await hass.async_block_till_done()
    assert hass.states.get("todo.house") is not None


async def test_full_cycle(hass, entry, notify_calls, freezer):
    resp = await hass.services.async_call(
        DOMAIN, "add_task",
        {"entity_id": ENTITY, "name": "Water plants", "frequency": "weekly",
         "weekdays": ["fri"], "time": "09:00"},
        blocking=True, return_response=True,
    )
    assert resp[ENTITY]["next_due"].startswith("2026-11-20T09:00")
    assert await items(hass) == []

    freezer.move_to("2026-11-20 09:00:00+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    its = await items(hass)
    assert [i["summary"] for i in its] == ["Water plants"]
    assert len(notify_calls) == 1
    actions = notify_calls[0].data["data"]["actions"]
    assert [a["title"] for a in actions] == ["Done", "Later", "Not doing"]

    # Later -> stays open, re-notified after 30 min
    hass.bus.async_fire("mobile_app_notification_action", {"action": actions[1]["action"]})
    await hass.async_block_till_done()
    assert (await items(hass))[0]["status"] == "needs_action"
    n_before = len(notify_calls)
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    msgs = [c.data["message"] for c in notify_calls[n_before:]]
    assert "Water plants" in msgs

    # Done
    last = [c for c in notify_calls if c.data["message"] == "Water plants"][-1]
    hass.bus.async_fire("mobile_app_notification_action", {"action": last.data["data"]["actions"][0]["action"]})
    await hass.async_block_till_done()
    assert (await items(hass))[0]["status"] == "completed"

    # Next week adds a fresh item
    freezer.move_to("2026-11-27 09:00:00+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    its = await items(hass)
    assert len(its) == 2 and its[1]["status"] == "needs_action"

    # Not doing -> removed
    last = [c for c in notify_calls if c.data["message"] == "Water plants"][-1]
    hass.bus.async_fire("mobile_app_notification_action", {"action": last.data["data"]["actions"][2]["action"]})
    await hass.async_block_till_done()
    its = await items(hass)
    assert len(its) == 1 and its[0]["status"] == "completed"


async def test_later_readds_deleted_item(hass, entry, notify_calls, freezer):
    await hass.services.async_call(
        DOMAIN, "add_task",
        {"entity_id": ENTITY, "name": "Vacuum", "frequency": "daily", "time": "09:00"},
        blocking=True, return_response=True,
    )
    await hass.services.async_call(DOMAIN, "run_task", {"entity_id": ENTITY, "name": "Vacuum"}, blocking=True)
    await hass.async_block_till_done()
    uid = (await items(hass))[0]["uid"]
    await hass.services.async_call("todo", "remove_item", {"entity_id": ENTITY, "item": uid}, blocking=True)
    assert await items(hass) == []
    hass.bus.async_fire("mobile_app_notification_action", {"action": f"RTODO_LATER_{uid}"})
    await hass.async_block_till_done()
    assert [i["summary"] for i in await items(hass)] == ["Vacuum"]


async def test_persistence_and_catch_up(hass, entry, notify_calls, freezer):
    await hass.services.async_call(
        DOMAIN, "add_task",
        {"entity_id": ENTITY, "name": "Bins", "frequency": "monthly", "day_of_month": 1, "time": "09:00"},
        blocking=True, return_response=True,
    )
    await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to("2026-12-03 12:00:00+02:00")  # was "off" over Dec 1st
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert [i["summary"] for i in await items(hass)] == ["Bins"]
    attrs = hass.states.get(ENTITY).attributes["recurring_tasks"]
    assert attrs[0]["next_due"].startswith("2027-01-01T09:00")


async def test_options_flow_add_remove(hass, entry):
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.MENU
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "add_task"})
    r = await hass.config_entries.options.async_configure(
        r["flow_id"],
        {"name": "Filter", "frequency": "monthly", "interval": 3, "day_of_month": 15,
         "time": "10:00:00", "notify": True},
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    attrs = hass.states.get(ENTITY).attributes["recurring_tasks"]
    assert attrs[0]["name"] == "Filter" and attrs[0]["next_due"].startswith("2026-12-15T10:00")

    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "add_task"})
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {"name": "Weekly", "frequency": "weekly", "interval": 1, "time": "10:00:00", "notify": True})
    assert r["errors"] == {"weekdays": "weekdays_required"}

    tid = entry.runtime_data.find_task("Filter")["task_id"]
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "remove_task"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"tasks": [tid]})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes["recurring_tasks"] == []


async def test_ui_add_and_complete_item(hass, entry):
    await hass.services.async_call("todo", "add_item", {"entity_id": ENTITY, "item": "Milk", "due_date": "2026-11-21"}, blocking=True)
    await hass.services.async_call("todo", "update_item", {"entity_id": ENTITY, "item": "Milk", "status": "completed"}, blocking=True)
    its = await items(hass)
    assert its[0]["status"] == "completed" and its[0]["due"] == "2026-11-21"
