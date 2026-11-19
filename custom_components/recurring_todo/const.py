"""Constants for the Recurring To-do integration."""

DOMAIN = "recurring_todo"

CONF_NOTIFY_SERVICES = "notify_services"
CONF_LATER_MINUTES = "later_minutes"

DEFAULT_LATER_MINUTES = 60

# Task fields
ATTR_TASK_ID = "task_id"
ATTR_NAME = "name"
ATTR_FREQUENCY = "frequency"
ATTR_INTERVAL = "interval"
ATTR_WEEKDAYS = "weekdays"
ATTR_DAY_OF_MONTH = "day_of_month"
ATTR_TIME = "time"
ATTR_START_DATE = "start_date"
ATTR_NOTIFY = "notify"
ATTR_NOTIFY_SERVICES = "notify_services"
ATTR_DESCRIPTION = "description"

FREQ_DAILY = "daily"
FREQ_WEEKLY = "weekly"
FREQ_MONTHLY = "monthly"
FREQUENCIES = [FREQ_DAILY, FREQ_WEEKLY, FREQ_MONTHLY]

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Notification action identifiers: RTODO_<ACTION>_<item uid>
ACTION_PREFIX = "RTODO"
ACTION_DONE = "DONE"
ACTION_LATER = "LATER"
ACTION_SKIP = "SKIP"

NOTIFICATION_ACTION_EVENT = "mobile_app_notification_action"
EVENT_ACTION_HANDLED = f"{DOMAIN}_action"

SERVICE_ADD_TASK = "add_task"
SERVICE_REMOVE_TASK = "remove_task"
SERVICE_RUN_TASK = "run_task"
