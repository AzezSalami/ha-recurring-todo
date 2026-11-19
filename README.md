# Recurring To-do for Home Assistant

A to-do list for Home Assistant whose recurring tasks (daily / weekly / monthly) are added automatically and sent to your phone with three buttons:

| Button | What happens |
|---|---|
| **Done** | The item is marked completed. |
| **Later** | The item stays on the list (or is put back if you deleted it), its due time moves forward, and you get the notification again after the "Later" delay (default 60 min). |
| **Not doing** | The item is removed from the list. |

The list is a normal Home Assistant to-do list, so it shows up in the **To-do lists** sidebar and you can add one-off items, tick things off, and use `todo.*` actions as usual.

## Install with HACS

1. HACS → ⋮ (top right) → **Custom repositories**.
2. Paste `https://github.com/AzezSalami/ha-recurring-todo`, choose type **Integration**, then click **Add**.
3. Search for **Recurring To-do** in HACS, open it and click **Download**.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration → Recurring To-do**.
6. Give the list a name (e.g. *Chores*), pick your phone(s) (`notify.mobile_app_…`) and the "Later" delay.

The buttons need the **Home Assistant Companion app** (Android or iOS). Other notify services get the message without buttons.

## Adding recurring tasks

### From the Recurring To-do page (easiest)
After installing, a **Recurring To-do** page appears in the sidebar. It shows every recurring task with its schedule and when it's next due. From there you can:

- **Add task**: name, Daily / Weekly / Monthly, "every N", weekdays or day of month, time, optional start date and notes, and whether to send a notification. A line at the bottom shows the schedule in plain words before you save.
- **Edit** (pencil): change anything. If the task is on the list right now, that item gets the new name and notes too.
- **Delete** (bin): stops the task. Items already on the list stay there.
- **Run now**: puts the task on the list and notifies right away, without changing the schedule.

If you have more than one list, switch between them with the buttons at the top. The page works on phones too (Companion app → sidebar → Recurring To-do).

What the fields mean:
- **Every**: 1 = every day/week/month, 2 = every second one, 3 + Monthly = quarterly, 12 + Monthly = yearly
- **On** (weekly): pick one or more weekdays
- **On day** (monthly): 29–31 fall on the last day in shorter months
- **At**: when the item is added and the notification is sent
- **Starting from**: optional first date. For "every 2 weeks" or "every 3 months" it decides which weeks or months count.
- **Send to**: *List default* sends to the devices you picked when you set up the list. *Choose devices* sends this task only to the devices you tick, e.g. the cat-feeding reminder to one phone and the bins to everyone. Each card shows where its notifications go.

### From Settings
**Settings → Devices & Services → Recurring To-do → Configure** has the same options as plain forms: **Add**, **Edit**, **Remove** recurring tasks, and the notification settings.

### From automations / scripts

```yaml
action: recurring_todo.add_task
target:
  entity_id: todo.chores
data:
  name: Change water filter
  frequency: monthly
  interval: 3
  day_of_month: 1
  time: "09:00"
```

```yaml
action: recurring_todo.add_task
target:
  entity_id: todo.chores
data:
  name: Take out recycling
  frequency: weekly
  weekdays: [mon, thu]
  time: "19:00"
```

Add `notify_services` to send a task only to certain devices (leave it out to use the list's devices):

```yaml
action: recurring_todo.add_task
target:
  entity_id: todo.chores
data:
  name: Feed the cat
  frequency: daily
  time: "07:30"
  notify_services:
    - notify.mobile_app_annas_phone
```

Other actions:
- `recurring_todo.remove_task`: `name:` (stops a recurring task)
- `recurring_todo.run_task`: `name:` (add it to the list and notify right now, without changing the schedule)

## Details

- The list's attributes (`recurring_tasks`) show every task with its `next_due` and the devices it notifies (`notify_services`).
- When someone presses a button, the notification is cleared on every device it was sent to.
- If a task comes due while the previous one is still open, no duplicate is added. You just get reminded again.
- If Home Assistant was off when a task was due, the task is added as soon as Home Assistant starts again.
- Each button press fires a `recurring_todo_action` event (`action`: `done` / `later` / `skip`, `item`, `uid`), so you can build your own automations on top, e.g. a reward or a log.
- All data is stored locally in `.storage/recurring_todo.<entry_id>`.
