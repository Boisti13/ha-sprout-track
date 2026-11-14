"""Sensors, binary sensors and how babies come and go."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sprout_track.const import DOMAIN, DOSE_LIMIT
from custom_components.sprout_track.coordinator import merge_doses, parse_dose_gap

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
    # 25 sensors, 5 binary sensors, 6 buttons, *last given* for Paracetamol
    # and Vitamin D (no *next dose*: no minimum gap in the fixture)
    assert len(entities) == 25 + 5 + 6 + 2
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
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A new baby gets a device without a reload; one gone for a day loses it."""
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

    # Missing from one answer: unavailable, but device and entities stay
    mock_api(aioclient_mock, babies=one)
    await _poll(hass)
    assert _device(hass, entry, BABY_2) is not None
    assert _state(hass, "sensor.mia_feeds_today") == STATE_UNAVAILABLE
    assert _state(hass, "sensor.emma_feeds_today") == "1"

    # Back within the day: the same entities work again
    mock_api(aioclient_mock)
    await _poll(hass)
    assert _state(hass, "sensor.mia_feeds_today") == "0"

    # Gone for a day: device and entities are removed
    mock_api(aioclient_mock, babies=one)
    await _poll(hass)
    freezer.tick(timedelta(hours=23))
    await _poll(hass)
    assert _device(hass, entry, BABY_2) is not None
    freezer.tick(timedelta(hours=1))
    await _poll(hass)
    assert _device(hass, entry, BABY_2) is None
    assert hass.states.get("sensor.mia_feeds_today") is None
    assert er.async_get(hass).async_get("sensor.mia_feeds_today") is None

    # ... and coming back after that works too
    mock_api(aioclient_mock)
    await _poll(hass)
    assert _state(hass, "sensor.mia_feeds_today") == "0"


async def test_empty_baby_list_keeps_devices(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty answer (a server being updated) doesn't remove anything."""
    mock_api(aioclient_mock, babies={"success": True, "data": {"babies": []}})
    await _poll(hass)
    assert _device(hass, loaded, BABY_1) is not None
    assert _device(hass, loaded, BABY_2) is not None
    assert _state(hass, "sensor.emma_last_feed") == STATE_UNAVAILABLE
    assert _state(hass, "sensor.emma_paracetamol_last_given") == STATE_UNAVAILABLE

    mock_api(aioclient_mock)
    await _poll(hass)
    assert _state(hass, "sensor.emma_last_feed") == "2026-11-12T13:38:21+00:00"
    assert _state(hass, "sensor.emma_paracetamol_last_given") == "2026-11-12T13:38:21+00:00"


async def test_baby_missing_after_restart(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A baby gone while Home Assistant was off is removed a day later too."""
    assert await hass.config_entries.async_unload(loaded.entry_id)
    one = load("babies")
    one["data"]["babies"] = one["data"]["babies"][:1]
    mock_api(aioclient_mock, babies=one)
    assert await hass.config_entries.async_setup(loaded.entry_id)
    await hass.async_block_till_done()
    assert _device(hass, loaded, BABY_2) is not None

    freezer.tick(timedelta(hours=24))
    await _poll(hass)
    assert _device(hass, loaded, BABY_2) is None


async def test_device_deleted_by_hand(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client,
) -> None:
    """A missing baby's device can be deleted at once; a listed one's can't."""
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)

    async def remove(baby_id: str) -> bool:
        device = _device(hass, loaded, baby_id)
        await client.send_json_auto_id(
            {
                "type": "config/device_registry/remove_config_entry",
                "config_entry_id": loaded.entry_id,
                "device_id": device.id,
            }
        )
        return (await client.receive_json())["success"]

    assert not await remove(BABY_2)

    one = load("babies")
    one["data"]["babies"] = one["data"]["babies"][:1]
    mock_api(aioclient_mock, babies=one)
    await _poll(hass)
    assert await remove(BABY_2)
    await hass.async_block_till_done()
    assert _device(hass, loaded, BABY_2) is None
    assert loaded.runtime_data.missing_since == {}

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


def _reference_with_gap(gap: str = "00:04:00") -> dict:
    reference = load("reference")
    reference["data"]["medicines"][0]["doseMinTime"] = gap
    reference["data"]["supplements"][0]["doseMinTime"] = ""
    return reference


async def test_dose_sensors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Each medicine gets *last given*, one with a minimum gap *next dose* too."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-11-12T18:00:00+00:00")
    mock_api(aioclient_mock, reference=_reference_with_gap())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    last = hass.states.get("sensor.emma_paracetamol_last_given")
    assert last.state == "2026-11-12T13:38:21+00:00"
    assert last.attributes["amount"] == 2.5
    assert last.attributes["unit"] == "ML"
    assert last.attributes["doses_today"] == 2  # 08:00 and 14:38 Berlin time
    assert last.attributes["minimum_gap_minutes"] == 240
    assert _state(hass, "sensor.emma_paracetamol_next_dose") == "2026-11-12T17:38:21+00:00"

    assert _state(hass, "sensor.emma_vitamin_d_last_given") == "2026-11-12T13:38:21+00:00"
    assert hass.states.get("sensor.emma_vitamin_d_next_dose") is None
    assert _state(hass, "sensor.mia_paracetamol_last_given") == STATE_UNKNOWN
    assert _state(hass, "sensor.mia_paracetamol_next_dose") == STATE_UNKNOWN

    medicines = hass.states.get("sensor.emma_medicines_today").attributes
    assert [d["time"] for d in medicines["doses"]] == [
        "2026-11-12T14:38:21.893000+01:00",
        "2026-11-12T08:00:00+01:00",
    ]
    assert medicines["latest"] == [
        {
            "time": "2026-11-12T14:38:21.893000+01:00",
            "name": "Paracetamol",
            "amount": 2.5,
            "unit": "ML",
            "next_dose": "2026-11-12T18:38:21.893000+01:00",
        }
    ]
    supplements = hass.states.get("sensor.emma_supplements_today").attributes
    assert supplements["latest"][0]["name"] == "Vitamin D"
    assert supplements["latest"][0]["next_dose"] is None
    assert hass.states.get("sensor.mia_medicines_today").attributes == {
        **hass.states.get("sensor.mia_medicines_today").attributes,
        "doses": [],
        "latest": [],
    }

    # A day later: nothing today, nothing in the last 24 hours
    freezer.move_to("2026-11-13T18:00:00+00:00")
    await _poll(hass)
    medicines = hass.states.get("sensor.emma_medicines_today").attributes
    assert medicines["doses"] == []
    assert medicines["latest"] == []
    assert hass.states.get("sensor.emma_paracetamol_last_given").attributes["doses_today"] == 0


async def test_doses_fetched_on_change(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Doses are fetched when the status shows a new one, and every 15 minutes."""

    def count() -> int:
        return sum(
            call[0] == "GET" and "/activities" in str(call[1]) for call in aioclient_mock.mock_calls
        )

    assert count() == 6  # two babies: medicines, supplements and feeds
    await _poll(hass)
    assert count() == 6

    status = load("status")
    status["data"]["lastActivities"]["medicine"]["time"] = "2026-11-12T15:00:00.000Z"
    mock_api(aioclient_mock, status_1=status)
    await _poll(hass)
    assert count() == 1  # only Emma's medicines (the mock was reset)

    freezer.tick(timedelta(minutes=16))
    await _poll(hass)
    assert count() == 7


async def test_medicine_added_and_removed(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Sensors follow the medicines set up in Sprout Track."""
    registry = er.async_get(hass)
    assert registry.async_get("sensor.emma_paracetamol_last_given") is not None

    reference = load("reference")
    reference["data"]["medicines"] = []
    mock_api(aioclient_mock, reference=reference)
    freezer.tick(timedelta(minutes=16))
    await _poll(hass)
    # Kept for a day (unavailable), then removed
    assert _state(hass, "sensor.emma_paracetamol_last_given") == STATE_UNAVAILABLE
    freezer.tick(timedelta(hours=24))
    await _poll(hass)
    assert registry.async_get("sensor.emma_paracetamol_last_given") is None
    assert hass.states.get("sensor.emma_paracetamol_last_given") is None
    assert _state(hass, "sensor.emma_vitamin_d_last_given") != STATE_UNAVAILABLE

    mock_api(aioclient_mock, reference=_reference_with_gap("01:00:00"))
    freezer.tick(timedelta(minutes=16))
    await _poll(hass)
    assert _state(hass, "sensor.emma_paracetamol_last_given") == "2026-11-12T13:38:21+00:00"
    assert _state(hass, "sensor.emma_paracetamol_next_dose") == "2026-11-13T13:38:21+00:00"


async def test_medicine_removed_while_reloading(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A medicine that went away during a reload loses its sensor a day later."""
    registry = er.async_get(hass)
    assert registry.async_get("sensor.emma_paracetamol_last_given") is not None
    assert await hass.config_entries.async_unload(loaded.entry_id)

    reference = load("reference")
    reference["data"]["medicines"] = []
    mock_api(aioclient_mock, reference=reference)
    assert await hass.config_entries.async_setup(loaded.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("sensor.emma_paracetamol_last_given") is not None

    freezer.tick(timedelta(hours=24))
    await _poll(hass)
    assert registry.async_get("sensor.emma_paracetamol_last_given") is None
    assert registry.async_get("sensor.mia_paracetamol_last_given") is None
    assert _state(hass, "sensor.emma_vitamin_d_last_given") == "2026-11-12T13:38:21+00:00"
    assert _state(hass, "sensor.emma_last_feed") == "2026-11-12T13:38:21+00:00"


def test_parse_dose_gap() -> None:
    """Minimum gaps come as "DD:HH:MM"; anything else means none."""
    assert parse_dose_gap("00:04:00") == timedelta(hours=4)
    assert parse_dose_gap("01:02:03") == timedelta(days=1, hours=2, minutes=3)
    for value in ("", "00:00:00", "4h", "00:04", None, 240, "aa:bb:cc"):
        assert parse_dose_gap(value) is None, value


async def test_fed_today(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Bottle feeds since midnight add up in millilitres; ounces are converted."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-11-12T18:00:00+00:00")
    mock_api(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    fed = hass.states.get("sensor.emma_fed_today")
    # 120 ml at 14:38 and 3 oz at 07:00 Berlin time; the breastfeed has no
    # amount, and 100 ml at 22:00 the evening before is not today
    assert fed.state == "208.7"
    assert fed.attributes["unit_of_measurement"] == "mL"
    assert fed.attributes["last_24_hours"] == 308.7
    assert _state(hass, "sensor.mia_fed_today") == "0.0"

    # A new day starts at zero, even before the next fetch
    freezer.move_to("2026-11-13T18:00:00+00:00")
    await _poll(hass)
    assert _state(hass, "sensor.emma_fed_today") == "0.0"


async def test_feeds_fetched_on_change(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The feed list is fetched again when the status shows a new feed."""

    def count() -> int:
        return sum(
            call[0] == "GET" and "type=feed" in str(call[1]) for call in aioclient_mock.mock_calls
        )

    assert count() == 2
    status = load("status")
    status["data"]["lastActivities"]["feed"]["id"] = "feed-4"
    mock_api(aioclient_mock, status_1=status)
    await _poll(hass)
    assert count() == 1  # only Emma's (the mock was reset)


def _dose(dose_id: str, name: str, time: str) -> dict:
    return {
        "activityType": "medicine",
        "id": dose_id,
        "time": time,
        "details": {"medicineName": name, "doseAmount": 1, "unitAbbr": "ML", "notes": None},
        "caretakerName": None,
    }


def _page(doses: list[dict]) -> dict:
    return {
        "success": True,
        "data": {"activities": doses, "count": len(doses), "hasMore": False},
    }


async def test_rare_medicine_kept_when_page_is_full(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A medicine given often doesn't push a rarely given one out of view.

    The API returns at most 50 doses per kind, newest first. A full page was
    cut off; doses older than it are kept from before, also across a reload.
    """
    freezer.move_to("2026-11-12T18:00:00+00:00")
    reference = load("reference")
    reference["data"]["medicines"].append(
        {"id": "med-0003", "name": "Ibuprofen", "typicalDoseSize": 2, "unitAbbr": "ML"}
    )
    ibuprofen = _dose("ibu-1", "Ibuprofen", "2026-11-02T08:00:00.000Z")
    paracetamol = [
        _dose(f"para-{i}", "Paracetamol", (dt_util.utcnow() - timedelta(hours=5 + i)).isoformat())
        for i in range(DOSE_LIMIT)
    ]

    def serve(doses: list[dict]) -> None:
        mock_api(aioclient_mock, reference=reference, activities={"medicine": _page(doses)})

    serve([paracetamol[0], ibuprofen])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, "sensor.emma_ibuprofen_last_given") == "2026-11-02T08:00:00+00:00"

    # 50 newer Paracetamol doses: the page is full, Ibuprofen is kept
    serve(paracetamol)
    freezer.tick(timedelta(minutes=16))
    await _poll(hass)
    assert _state(hass, "sensor.emma_ibuprofen_last_given") == "2026-11-02T08:00:00+00:00"
    assert _state(hass, "sensor.emma_paracetamol_last_given") == paracetamol[0]["time"]

    # ... also after a reload (saved on unload, loaded on setup)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, "sensor.emma_ibuprofen_last_given") == "2026-11-02T08:00:00+00:00"

    # A page that isn't full is the whole truth: the dose was deleted
    serve(paracetamol[:3])
    freezer.tick(timedelta(minutes=16))
    await _poll(hass)
    assert _state(hass, "sensor.emma_ibuprofen_last_given") == STATE_UNKNOWN


def test_merge_doses() -> None:
    """Older doses fill in a full page, within the history and without doubles."""
    since = dt_util.parse_datetime("2026-11-01T00:00:00Z")
    fresh = [_dose(f"n{i}", "A", f"2026-11-10T{i:02d}:00:00Z") for i in range(3)]
    cached = [
        _dose("n0", "A", "2026-11-10T00:00:00Z"),  # also in the fresh page
        _dose("old", "B", "2026-11-05T00:00:00Z"),  # older than the page: kept
        _dose("gone", "B", "2026-11-10T01:30:00Z"),  # within the page: deleted
        _dose("ancient", "B", "2026-10-20T00:00:00Z"),  # beyond the history
    ]
    assert [d["id"] for d in merge_doses(cached, fresh, since, 3)] == ["n0", "n1", "n2", "old"]
    assert merge_doses(cached, fresh, since, 4) == fresh
