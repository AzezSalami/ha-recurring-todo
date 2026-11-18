"""Tests for the WebSocket API behind the sidebar panel, and the options-flow edit step."""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import DOMAIN, ENTITY
from .test_integration import items


async def _ws(client, msg):
    await client.send_json_auto_id(msg)
    return await client.receive_json()


async def test_ws_create_edit_run_delete(hass: HomeAssistant, entry, hass_ws_client):
    client = await hass_ws_client(hass)

    r = await _ws(client, {"type": f"{DOMAIN}/lists"})
    assert r["success"]
    assert r["result"] == [
        {"entry_id": entry.entry_id, "title": "Chores", "entity_id": ENTITY, "task_count": 0}
    ]

    # Create
    r = await _ws(client, {
        "type": f"{DOMAIN}/task/create", "entry_id": entry.entry_id,
        "task": {"name": "Plants", "frequency": "weekly", "weekdays": ["fri"], "time": "09:00"},
    })
    assert r["success"], r
    task = r["result"]
    assert task["next_due"].startswith("2026-11-20T09:00")
    tid = task["task_id"]

    # Validation errors
    r = await _ws(client, {
        "type": f"{DOMAIN}/task/create", "entry_id": entry.entry_id,
        "task": {"name": "plants", "frequency": "daily"},
    })
    assert not r["success"] and r["error"]["code"] == "already_exists"
    r = await _ws(client, {
        "type": f"{DOMAIN}/task/create", "entry_id": entry.entry_id,
        "task": {"name": "Other", "frequency": "weekly"},
    })
    assert r["error"]["code"] == "weekdays_required"

    # Run now puts it on the list
    r = await _ws(client, {"type": f"{DOMAIN}/task/run", "entry_id": entry.entry_id, "task_id": tid})
    assert r["success"]
    assert [i["summary"] for i in await items(hass)] == ["Plants"]

    # Edit: switch to monthly, rename; the open item follows the new name
    r = await _ws(client, {
        "type": f"{DOMAIN}/task/update", "entry_id": entry.entry_id, "task_id": tid,
        "task": {"name": "Water plants", "frequency": "monthly", "day_of_month": 25,
                 "time": "18:30", "description": "Kitchen too", "notify": False},
    })
    assert r["success"], r
    upd = r["result"]
    assert upd["task_id"] == tid
    assert upd["weekdays"] == [] and upd["day_of_month"] == 25
    assert upd["next_due"].startswith("2026-11-25T18:30")
    its = await items(hass)
    assert its[0]["summary"] == "Water plants" and its[0]["description"] == "Kitchen too"

    # Renaming to its own name (different case) is fine; to another task's name is not
    r = await _ws(client, {
        "type": f"{DOMAIN}/task/update", "entry_id": entry.entry_id, "task_id": tid,
        "task": {"name": "WATER PLANTS", "frequency": "daily"},
    })
    assert r["success"]
    await _ws(client, {
        "type": f"{DOMAIN}/task/create", "entry_id": entry.entry_id,
        "task": {"name": "Bins", "frequency": "daily"},
    })
    r = await _ws(client, {
        "type": f"{DOMAIN}/task/update", "entry_id": entry.entry_id, "task_id": tid,
        "task": {"name": "bins", "frequency": "daily"},
    })
    assert r["error"]["code"] == "already_exists"

    r = await _ws(client, {"type": f"{DOMAIN}/tasks", "entry_id": entry.entry_id})
    assert sorted(t["name"] for t in r["result"]) == ["Bins", "WATER PLANTS"]

    # Delete
    r = await _ws(client, {"type": f"{DOMAIN}/task/delete", "entry_id": entry.entry_id, "task_id": tid})
    assert r["success"]
    r = await _ws(client, {"type": f"{DOMAIN}/tasks", "entry_id": entry.entry_id})
    assert [t["name"] for t in r["result"]] == ["Bins"]
    r = await _ws(client, {"type": f"{DOMAIN}/task/delete", "entry_id": entry.entry_id, "task_id": tid})
    assert r["error"]["code"] == "not_found"
    r = await _ws(client, {"type": f"{DOMAIN}/tasks", "entry_id": "nope"})
    assert r["error"]["code"] == "not_found"


async def test_options_flow_edit(hass: HomeAssistant, entry):
    manager = entry.runtime_data
    task = await manager.async_add_task(
        {"name": "Filter", "frequency": "monthly", "interval": 3, "day_of_month": 15, "time": "10:00:00"}
    )
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "edit_task"})
    assert r["step_id"] == "edit_task"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"task": task["task_id"]})
    assert r["step_id"] == "edit_task_details"
    r = await hass.config_entries.options.async_configure(
        r["flow_id"],
        {"name": "Filter", "frequency": "weekly", "interval": 1, "time": "10:00:00", "notify": True},
    )
    assert r["errors"] == {"weekdays": "weekdays_required"}
    r = await hass.config_entries.options.async_configure(
        r["flow_id"],
        {"name": "Filter", "frequency": "weekly", "interval": 1, "weekdays": ["sat"],
         "time": "10:00:00", "notify": True},
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    t = manager.tasks[task["task_id"]]
    assert t["frequency"] == "weekly" and "day_of_month" not in t
    assert t["next_due"].startswith("2026-11-21T10:00")
    assert hass.states.get(ENTITY).attributes["recurring_tasks"][0]["frequency"] == "weekly"
