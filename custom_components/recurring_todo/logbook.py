"""Show notification button presses in the Home Assistant Logbook."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_ACTION_HANDLED

VERBS = {
    "done": "marked “{item}” as done",
    "later": "asked to be reminded about “{item}” later",
    "skip": "chose not to do “{item}”",
}


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Describe Recurring To-do events for the Logbook."""

    @callback
    def describe(event: Event) -> dict[str, Any]:
        data = event.data
        entry = hass.config_entries.async_get_entry(data.get("entry_id", ""))
        message = VERBS.get(data.get("action"), "reacted to “{item}”").format(item=data.get("item", "?"))
        if data.get("device"):
            message += f" on {data['device']}"
        described = {
            LOGBOOK_ENTRY_NAME: data.get("user") or (entry.title if entry else "Recurring To-do"),
            LOGBOOK_ENTRY_MESSAGE: message,
        }
        if data.get("entity_id"):
            described[LOGBOOK_ENTRY_ENTITY_ID] = data["entity_id"]
        return described

    async_describe_event(DOMAIN, EVENT_ACTION_HANDLED, describe)
