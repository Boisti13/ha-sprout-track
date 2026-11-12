"""Sensors, binary sensors and how babies come and go."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sprout_track.const import DOMAIN

from .conftest import API, BABY_1, BABY_2, api_error, load, mock_api


async def _poll(hass: HomeAssistant, seconds: int = 61) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done(wait_background_tasks=True)


def _device(hass: HomeAssistant, entry: MockConfigEntry, baby_id: str) -> dr.DeviceEntry | None:
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return next((d for d in devices if (DOMAIN, baby_id) in d.identifiers), None)


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def test_devices(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Each baby is a device named after it."""
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, loaded.entry_id)
    assert {d.name for d in devices} == {"Emma", "Mia"}
    emma = _device(hass, loaded, BABY_1)
    assert emma.manufacturer == "Sprout Track"
    assert emma.configuration_url == "http://192.168.1.50:3000"
    entities = er.async_entries_for_device(er.async_get(hass), emma.id)
    assert len(entities) == 24 + 5 + 6
    assert all(e.unique_id.startswith(f"{BABY_1}_") for e in entities)


async def test_sensors(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Values come out of the recorded status as expected."""
    assert _state(hass, "sensor.emma_last_feed") == "2026-11-12T13:38:21+00:00"
    feed = hass.states.get("sensor.emma_last_feed").attributes
    assert feed["feed_type"] == "BOTTLE"
    assert feed["amount"] == 120
    assert "caretaker" not in feed  # empty fields are left out

    assert _state(hass, "sensor.emma_last_diaper") == "2026-11-12T13:38:21+00:00"
    assert hass.states.get("sensor.emma_last_diaper").attributes["diaper_type"] == "WET"
    assert _state(hass, "sensor.emma_sleep_state") == "awake"
    assert _state(hass, "sensor.emma_asleep_since") == STATE_UNKNOWN
    assert _state(hass, "sensor.emma_awake_since") == "2026-11-12T09:45:00+00:00"
    assert _state(hass, "sensor.emma_last_sleep_duration") == "45"
    assert hass.states.get("sensor.emma_last_medicine").attributes["medicine"] == "Paracetamol"
    assert hass.states.get("sensor.emma_last_supplement").attributes["supplement"] == "Vitamin D"
    assert _state(hass, "sensor.emma_last_pump") == STATE_UNKNOWN

    assert _state(hass, "sensor.emma_feeds_today") == "1"
    assert _state(hass, "sensor.emma_diapers_today") == "1"
    assert _state(hass, "sensor.emma_wet_diapers_today") == "1"
    assert _state(hass, "sensor.emma_dirty_diapers_today") == "0"
    assert _state(hass, "sensor.emma_sleep_today") == "0.75"  # shown in hours
    assert _state(hass, "sensor.emma_naps_today") == "1"

    assert _state(hass, "sensor.emma_age") == "11"
    assert hass.states.get("sensor.emma_age").attributes["birth_date"] == "2026-10-31"

    weight = hass.states.get("sensor.emma_weight")
    assert weight.state == "4.2"
    assert weight.attributes["unit_of_measurement"] == "kg"
    assert weight.attributes["device_class"] == "weight"
    assert weight.attributes["date"] == "2026-11-12"
    assert hass.states.get("sensor.emma_height").attributes["unit_of_measurement"] == "cm"
    assert _state(hass, "sensor.emma_head_circumference") == STATE_UNKNOWN

    # A baby without any entries yet
    assert _state(hass, "sensor.mia_last_feed") == STATE_UNKNOWN
    assert _state(hass, "sensor.mia_sleep_state") == "awake"
    assert _state(hass, "sensor.mia_awake_since") == STATE_UNKNOWN
    assert _state(hass, "sensor.mia_feeds_today") == "0"
    assert _state(hass, "sensor.mia_wet_diapers_today") == "0"
    assert _state(hass, "sensor.mia_weight") == STATE_UNKNOWN


async def test_binary_sensors(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Warnings and timers are binary sensors."""
    assert _state(hass, "binary_sensor.emma_sleeping") == "off"
    assert _state(hass, "binary_sensor.emma_feed_overdue") == "on"
    assert hass.states.get("binary_sensor.emma_feed_overdue").attributes["warning_after"] == "02:00"
    assert _state(hass, "binary_sensor.emma_diaper_overdue") == "on"
    assert _state(hass, "binary_sensor.emma_breastfeeding") == "off"
    assert _state(hass, "binary_sensor.emma_pumping") == "off"
    assert _state(hass, "binary_sensor.mia_feed_overdue") == "off"


async def test_asleep_and_timers(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A running sleep, breastfeed and pump show up on the next poll."""
    status = load("status")
    last = status["data"]["lastActivities"]
    last["sleep"] = {
        "id": "s2",
        "startTime": "2026-11-12T19:00:00.000Z",
        "endTime": None,
        "minutesAgo": 5,
        "duration": None,
        "type": "NIGHT_SLEEP",
        "isActive": True,
    }
    last["pump"] = {"startTime": "2026-11-12T19:01:00.000Z", "endTime": None, "isActive": True}
    status["data"]["activeFeed"] = {
        "sessionStartTime": "2026-11-12T19:02:00.000Z",
        "activeSide": "LEFT",
        "isPaused": False,
    }
    mock_api(aioclient_mock, status_1=status)
    await _poll(hass)

    assert _state(hass, "binary_sensor.emma_sleeping") == "on"
    assert hass.states.get("binary_sensor.emma_sleeping").attributes["sleep_type"] == "NIGHT_SLEEP"
    assert _state(hass, "sensor.emma_sleep_state") == "night_sleep"
    assert _state(hass, "sensor.emma_asleep_since") == "2026-11-12T19:00:00+00:00"
    assert _state(hass, "sensor.emma_awake_since") == STATE_UNKNOWN
    assert _state(hass, "sensor.emma_last_sleep_duration") == STATE_UNKNOWN
    assert _state(hass, "binary_sensor.emma_pumping") == "on"
    assert _state(hass, "sensor.emma_last_pump") == "2026-11-12T19:01:00+00:00"
    assert _state(hass, "binary_sensor.emma_breastfeeding") == "on"
    assert hass.states.get("binary_sensor.emma_breastfeeding").attributes["side"] == "LEFT"

    # A sleep type this version does not know still counts as asleep
    last["sleep"]["type"] = None
    mock_api(aioclient_mock, status_1=status)
    await _poll(hass)
    assert _state(hass, "sensor.emma_sleep_state") == "asleep"


async def test_measurement_units(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Known units get a device class, unknown ones are shown as logged."""
    measurements = load("measurements")
    measurements["data"]["measurements"] |= {
        "WEIGHT": {"value": 9.5, "unit": "lb", "date": "2026-11-01"},
        "TEMPERATURE": {"value": 98.6, "unit": "°F", "date": "2026-11-01"},
        "HEAD_CIRCUMFERENCE": {"value": 3, "unit": "hands", "date": "2026-11-01"},
    }
    mock_api(aioclient_mock, measurements_1=measurements)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.emma_weight").attributes["unit_of_measurement"] == "lb"
    temperature = hass.states.get("sensor.emma_temperature")
    assert temperature.attributes["device_class"] == "temperature"
    head = hass.states.get("sensor.emma_head_circumference")
    assert head.state == "3"
    assert head.attributes["unit_of_measurement"] == "hands"
    assert "device_class" not in head.attributes


async def test_measurements_polled_less_often(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Status is fetched on every poll, measurements every 15 minutes."""

    def count(part: str) -> int:
        return sum(part in str(call[1]) for call in aioclient_mock.mock_calls)

    assert count("/status") == 2
    assert count("/measurements") == 2
    await _poll(hass)
    assert count("/status") == 4
    assert count("/measurements") == 2
    freezer.tick(timedelta(minutes=16))
    await _poll(hass)
    assert count("/measurements") == 4


async def test_baby_added_and_removed(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A new baby gets a device without a reload; a removed one loses it."""
    one = load("babies")
    one["data"]["babies"] = one["data"]["babies"][:1]
    mock_api(aioclient_mock, babies=one)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.mia_last_feed") is None

    mock_api(aioclient_mock)
    await _poll(hass)
    assert _state(hass, "sensor.mia_feeds_today") == "0"
    assert _device(hass, entry, BABY_2) is not None

    mock_api(aioclient_mock, babies=one)
    await _poll(hass)
    assert _device(hass, entry, BABY_2) is None
    assert hass.states.get("sensor.mia_feeds_today") is None
    assert _state(hass, "sensor.emma_feeds_today") == "1"

    # ... and coming back works too
    mock_api(aioclient_mock)
    await _poll(hass)
    assert _state(hass, "sensor.mia_feeds_today") == "0"


async def test_update_fails_and_recovers(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Entities go unavailable while the server is down."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/babies", exc=TimeoutError)
    await _poll(hass)
    assert _state(hass, "sensor.emma_feeds_today") == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{API}/babies", status=429, json=api_error("RATE_LIMITED"), headers={"Retry-After": "30"}
    )
    await _poll(hass)
    assert _state(hass, "sensor.emma_feeds_today") == STATE_UNAVAILABLE

    mock_api(aioclient_mock)
    await _poll(hass)
    assert _state(hass, "sensor.emma_feeds_today") == "1"


async def test_setup_not_ready(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Setup is retried while the server cannot be reached."""
    aioclient_mock.get(f"{API}/babies", exc=TimeoutError)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_revoked_key_starts_reauth(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A key that stops working asks the user for a new one."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/babies", status=401, json=api_error("KEY_REVOKED"))
    await _poll(hass)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_unload(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """The entry unloads cleanly."""
    assert await hass.config_entries.async_unload(loaded.entry_id)
    assert loaded.state is ConfigEntryState.NOT_LOADED
    assert _state(hass, "sensor.emma_feeds_today") == STATE_UNAVAILABLE


async def test_diagnostics(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Diagnostics carry the data's shape but nothing personal."""
    from custom_components.sprout_track.diagnostics import (  # noqa: PLC0415
        async_get_config_entry_diagnostics,
    )

    result = await async_get_config_entry_diagnostics(hass, loaded)
    text = str(result)
    for secret in (
        "st_live_test",
        "192.168.1.50",
        "Emma",
        "Mia",
        "Sample",
        "2026-10-31",
        "Paracetamol",
        BABY_1,
    ):
        assert secret not in text
    assert result["babies"][0]["status"]["dailyCounts"]["feeds"] == 1
