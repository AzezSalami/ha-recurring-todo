from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

pytest_plugins = ["pytest_homeassistant_custom_component"]

DOMAIN = "recurring_todo"
ENTITY = "todo.chores"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def notify_calls(hass: HomeAssistant) -> list[ServiceCall]:
    return async_mock_service(hass, "notify", "mobile_app_phone")


@pytest.fixture
async def entry(hass: HomeAssistant, notify_calls, freezer: FrozenDateTimeFactory):
    await hass.config.async_set_time_zone("Europe/Helsinki")
    freezer.move_to("2026-11-20 08:59:00+02:00")  # Friday
    e = MockConfigEntry(
        domain=DOMAIN,
        title="Chores",
        data={},
        options={"notify_services": ["notify.mobile_app_phone"], "later_minutes": 30},
    )
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    return e
