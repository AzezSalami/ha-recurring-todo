"""Config and options flow for Recurring To-do."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

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
    CONF_LATER_MINUTES,
    CONF_NOTIFY_SERVICES,
    DEFAULT_LATER_MINUTES,
    DOMAIN,
    FREQ_WEEKLY,
    FREQUENCIES,
    WEEKDAYS,
)


def _notify_options(hass: HomeAssistant) -> list[str]:
    services = hass.services.async_services().get("notify", {})
    names = sorted(f"notify.{name}" for name in services if name not in ("send_message", "persistent_notification"))
    # Phones (mobile_app_*) first, they are the ones that support buttons.
    return sorted(names, key=lambda n: (not n.startswith("notify.mobile_app_"), n))


def _settings_schema(hass: HomeAssistant, defaults: dict[str, Any]) -> dict:
    return {
        vol.Optional(
            CONF_NOTIFY_SERVICES, default=defaults.get(CONF_NOTIFY_SERVICES, [])
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_notify_options(hass),
                multiple=True,
                custom_value=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(
            CONF_LATER_MINUTES,
            default=defaults.get(CONF_LATER_MINUTES, DEFAULT_LATER_MINUTES),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=10080, step=1, unit_of_measurement="min",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }


def _clean_settings(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_NOTIFY_SERVICES: list(user_input.get(CONF_NOTIFY_SERVICES, [])),
        CONF_LATER_MINUTES: int(user_input.get(CONF_LATER_MINUTES, DEFAULT_LATER_MINUTES)),
    }


def _task_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(ATTR_NAME): str,
            vol.Required(ATTR_FREQUENCY, default=FREQ_WEEKLY): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=FREQUENCIES, translation_key="frequency",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(ATTR_INTERVAL, default=1): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=365, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(ATTR_WEEKDAYS): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=WEEKDAYS, multiple=True, translation_key="weekday",
                )
            ),
            vol.Optional(ATTR_DAY_OF_MONTH): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=31, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(ATTR_TIME, default="09:00:00"): selector.TimeSelector(),
            vol.Optional(ATTR_START_DATE): selector.DateSelector(),
            vol.Optional(ATTR_DESCRIPTION): str,
            vol.Required(ATTR_NOTIFY, default=True): selector.BooleanSelector(),
        }
    )


_ERROR_FIELD = {
    "name_required": ATTR_NAME,
    "already_exists": ATTR_NAME,
    "weekdays_required": ATTR_WEEKDAYS,
}


def _task_options(manager) -> list[selector.SelectOptionDict]:
    return [
        selector.SelectOptionDict(value=t["task_id"], label=f"{t['name']} ({t['frequency']})")
        for t in sorted(manager.tasks.values(), key=lambda t: t["name"].lower())
    ]


class RecurringTodoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a new recurring to-do list."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            await self.async_set_unique_id(name.lower())
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=name, data={}, options=_clean_settings(user_input)
            )

        schema = vol.Schema(
            {vol.Required(CONF_NAME, default="Chores"): str, **_settings_schema(self.hass, {})}
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RecurringTodoOptionsFlow()


class RecurringTodoOptionsFlow(OptionsFlow):
    """Manage recurring tasks and settings from the UI."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_task", "edit_task", "remove_task", "settings"],
        )

    async def async_step_add_task(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        manager = self.config_entry.runtime_data
        errors: dict[str, str] = {}
        if user_input is not None:
            from .todo import task_to_service_dict

            task = task_to_service_dict(user_input)
            if error := manager.validate_task(task):
                errors[_ERROR_FIELD[error]] = error
            else:
                await manager.async_add_task(task)
                return self.async_create_entry(data=dict(self.config_entry.options))

        schema = _task_schema()
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(step_id="add_task", data_schema=schema, errors=errors)

    async def async_step_edit_task(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        manager = self.config_entry.runtime_data
        if not manager.tasks:
            return self.async_abort(reason="no_tasks")
        if user_input is not None:
            self._edit_id = user_input["task"]
            return await self.async_step_edit_task_details()

        schema = vol.Schema(
            {
                vol.Required("task"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=_task_options(manager))
                )
            }
        )
        return self.async_show_form(step_id="edit_task", data_schema=schema)

    async def async_step_edit_task_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        manager = self.config_entry.runtime_data
        task_id = getattr(self, "_edit_id", None)
        if task_id not in manager.tasks:
            return self.async_abort(reason="no_tasks")
        errors: dict[str, str] = {}
        if user_input is not None:
            from .todo import task_to_service_dict

            task = task_to_service_dict(user_input)
            if error := manager.validate_task(task, exclude_id=task_id):
                errors[_ERROR_FIELD[error]] = error
            else:
                await manager.async_update_task(task_id, task)
                return self.async_create_entry(data=dict(self.config_entry.options))

        current = manager.tasks[task_id]
        suggested = user_input or {
            k: v for k, v in current.items() if k not in ("task_id", "next_due") and v is not None
        }
        schema = self.add_suggested_values_to_schema(_task_schema(), suggested)
        return self.async_show_form(
            step_id="edit_task_details",
            data_schema=schema,
            errors=errors,
            description_placeholders={"name": current["name"]},
        )

    async def async_step_remove_task(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        manager = self.config_entry.runtime_data
        if not manager.tasks:
            return self.async_abort(reason="no_tasks")
        if user_input is not None:
            for task_id in user_input.get("tasks", []):
                await manager.async_remove_task(task_id)
            return self.async_create_entry(data=dict(self.config_entry.options))

        schema = vol.Schema(
            {
                vol.Required("tasks"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=_task_options(manager), multiple=True)
                )
            }
        )
        return self.async_show_form(step_id="remove_task", data_schema=schema)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_clean_settings(user_input))
        schema = vol.Schema(_settings_schema(self.hass, dict(self.config_entry.options)))
        return self.async_show_form(step_id="settings", data_schema=schema)
