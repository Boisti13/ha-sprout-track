"""Actions and buttons: what is sent to Sprout Track."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sprout_track.const import DOMAIN

from .conftest import API, BABY_1, BABY_2, api_error, load, mock_api


def _device_id(hass: HomeAssistant, entry: MockConfigEntry, baby_id: str) -> str:
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return next(d.id for d in devices if (DOMAIN, baby_id) in d.identifiers)


def _posts(mock: AiohttpClientMocker) -> list[tuple[str, dict[str, Any]]]:
    """Return (baby, body) of every POST so far."""
    return [
        (str(url).split("/babies/")[1].split("/")[0], body)
        for method, url, body, _headers in mock.mock_calls
        if method == "POST"
    ]


async def _call(hass: HomeAssistant, service: str, data: dict[str, Any], **kwargs: Any) -> Any:
    return await hass.services.async_call(DOMAIN, service, data, blocking=True, **kwargs)


@pytest.fixture
async def one_baby(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> MockConfigEntry:
    """Set up a family with a single baby."""
    babies = load("babies")
    babies["data"]["babies"] = babies["data"]["babies"][:1]
    mock_api(aioclient_mock, babies=babies)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_log_diaper(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """All diaper fields reach the API under its own names."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    response = await _call(
        hass,
        "log_diaper",
        {
            "device_id": _device_id(hass, loaded, BABY_2),
            "diaper_type": "Both",
            "condition": "loose",
            "color": "yellow",
            "blowout": True,
            "cream_applied": False,
            "notes": "rash",
            "time": "2026-11-12 08:30:00",
            "caretaker": "Dad",
        },
        return_response=True,
    )
    assert response == {"id": "new-1", "activityType": "x"}
    assert _posts(aioclient_mock) == [
        (
            BABY_2,
            {
                "type": "diaper",
                "diaperType": "BOTH",
                "condition": "LOOSE",
                "color": "YELLOW",
                "blowout": True,
                "creamApplied": False,
                "notes": "rash",
                "time": "2026-11-12T07:30:00+00:00",  # local time, sent as UTC
                "caretakerName": "Dad",
            },
        )
    ]


async def test_baby_is_optional_with_one_baby(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """With a single baby no device has to be picked."""
    await _call(hass, "log_diaper", {"diaper_type": "wet"})
    assert _posts(aioclient_mock) == [(BABY_1, {"type": "diaper", "diaperType": "WET"})]


async def test_baby_is_required_with_two(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """With two babies a call without device is refused, as is a foreign device."""
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "log_diaper", {"diaper_type": "wet"})
    assert err.value.translation_key == "multiple_babies"

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "log_diaper", {"diaper_type": "wet", "device_id": "nope"})
    assert err.value.translation_key == "baby_not_found"
    assert _posts(aioclient_mock) == []


async def test_no_baby(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Without a loaded entry the actions say so."""
    await hass.config_entries.async_unload(loaded.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "log_diaper", {"diaper_type": "wet"})
    assert err.value.translation_key == "no_baby"


async def test_sleep_actions(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Start, end and log a sleep."""
    await _call(hass, "start_sleep", {"location": "Car Seat"})
    await _call(hass, "start_sleep", {"sleep_type": "night_sleep"})
    await _call(hass, "end_sleep", {"quality": "good", "sleep_type": "nap"})
    await _call(hass, "end_sleep", {})
    await _call(hass, "log_sleep", {"duration": 45, "location": "Crib"})
    assert [body for _, body in _posts(aioclient_mock)] == [
        {"type": "sleep", "action": "start", "sleepType": "NAP", "location": "Car Seat"},
        {"type": "sleep", "action": "start", "sleepType": "NIGHT_SLEEP"},
        {"type": "sleep", "action": "end", "sleepType": "NAP", "quality": "GOOD"},
        {"type": "sleep", "action": "end"},
        {"type": "sleep", "action": "log", "sleepType": "NAP", "duration": 45, "location": "Crib"},
    ]


async def test_start_sleep_refused_while_asleep(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A second sleep is not started next to a running one."""
    status = load("status")
    status["data"]["lastActivities"]["sleep"]["isActive"] = True
    mock_api(aioclient_mock, status_1=status)
    for call in (
        _call(hass, "start_sleep", {}),
        _call(
            hass, "log_activity", {"data": {"type": "sleep", "action": "start", "sleepType": "NAP"}}
        ),
        hass.services.async_call(
            "button", "press", {"entity_id": "button.emma_start_nap"}, blocking=True
        ),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await call
        assert err.value.translation_key == "already_asleep"
    assert _posts(aioclient_mock) == []


async def test_medicine_and_supplement(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without an amount the typical dose from Sprout Track is used."""
    await _call(hass, "log_medicine", {"medicine": "paracetamol"})
    await _call(
        hass,
        "log_medicine",
        {"medicine": "Paracetamol", "amount": 3, "unit": "ML", "notes": "fever"},
    )
    await _call(hass, "log_supplement", {"supplement": "Vitamin D"})
    assert [body for _, body in _posts(aioclient_mock)] == [
        {"type": "medicine", "medicineName": "Paracetamol", "amount": 2.5},
        {
            "type": "medicine",
            "medicineName": "Paracetamol",
            "amount": 3,
            "unitAbbr": "ML",
            "notes": "fever",
        },
        {"type": "supplement", "supplementName": "Vitamin D", "amount": 1},
    ]

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "log_medicine", {"medicine": "Aspirin"})
    assert err.value.translation_key == "medicine_not_found"
    assert err.value.translation_placeholders["available"] == "Paracetamol"


async def test_medicine_without_typical_dose(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A medicine without typical dose needs an amount."""
    reference = load("reference")
    reference["data"]["medicines"][0]["typicalDoseSize"] = None
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/babies/{BABY_1}/reference", json=reference)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "log_medicine", {"medicine": "Paracetamol"})
    assert err.value.translation_key == "amount_required"


async def test_log_activity(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """The raw payload is passed through untouched."""
    payload = {"type": "feed", "feedType": "formula", "amount": 120, "unitAbbr": "ML"}
    await _call(hass, "log_activity", {"data": payload})
    assert _posts(aioclient_mock) == [(BABY_1, payload)]

    with pytest.raises(Exception, match="type"):
        await _call(hass, "log_activity", {"data": {"amount": 1}})


async def test_measurement_refreshes_measurements(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A logged measurement shows up at once, not 15 minutes later."""
    before = sum("/measurements" in str(c[1]) for c in aioclient_mock.mock_calls)
    await _call(
        hass,
        "log_activity",
        {"data": {"type": "measurement", "measurementType": "WEIGHT", "value": 5}},
    )
    await hass.async_block_till_done()
    assert sum("/measurements" in str(c[1]) for c in aioclient_mock.mock_calls) == before + 1


async def test_log_feed(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Bottle feeds and finished breastfeeds reach the API under its own names."""
    await _call(
        hass,
        "log_feed",
        {"amount": 120.0, "unit": "ML", "bottle_type": "Breast_Milk", "notes": "fussy"},
    )
    await _call(
        hass,
        "log_feed",
        {"feed_type": "breast", "side": "left", "duration": 15, "caretaker": "Mum"},
    )
    assert _posts(aioclient_mock) == [
        (
            BABY_1,
            {
                "type": "feed",
                "feedType": "BOTTLE",
                "amount": 120,
                "bottleType": "Breast Milk",
                "unitAbbr": "ML",
                "notes": "fussy",
            },
        ),
        (
            BABY_1,
            {
                "type": "feed",
                "feedType": "BREAST",
                "caretakerName": "Mum",
                "side": "LEFT",
                "duration": 15,
            },
        ),
    ]


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"side": "left"}, "side"),
        ({"duration": 10}, "duration"),
        ({"feed_type": "breast", "bottle_type": "formula"}, "bottle_type"),
    ],
)
async def test_log_feed_wrong_fields(
    hass: HomeAssistant,
    one_baby: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    data: dict[str, Any],
    field: str,
) -> None:
    """A bottle has no side, a breastfeed no bottle content: refused before sending."""
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "log_feed", data)
    assert err.value.translation_key == "feed_field_mismatch"
    assert err.value.translation_placeholders["field"] == field
    assert _posts(aioclient_mock) == []


async def test_log_feed_amount_needs_unit(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """An amount without a unit would be stored unitless: refused."""
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "log_feed", {"amount": 90})
    assert err.value.translation_key == "unit_required"
    assert _posts(aioclient_mock) == []


async def test_breastfeeding_timer(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """The timer actions go to the API; starting needs a side."""
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "breastfeeding", {"action": "start"})
    assert err.value.translation_key == "side_required"

    await _call(hass, "breastfeeding", {"action": "start", "side": "right", "caretaker": "Mum"})
    await _call(hass, "breastfeeding", {"action": "pause"})
    await _call(hass, "breastfeeding", {"action": "resume", "side": "left"})
    await _call(hass, "breastfeeding", {"action": "end"})
    feed = {"type": "feed", "feedType": "BREAST"}
    assert _posts(aioclient_mock) == [
        (BABY_1, {**feed, "action": "start", "side": "RIGHT", "caretakerName": "Mum"}),
        (BABY_1, {**feed, "action": "pause"}),
        (BABY_1, {**feed, "action": "resume", "side": "LEFT"}),
        (BABY_1, {**feed, "action": "end"}),
    ]

    # A second start while one runs: the API's refusal is the caller's mistake
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{API}/babies/{BABY_1}/activities",
        status=409,
        json=api_error("FEED_ALREADY_ACTIVE", "A feed session is already active"),
    )
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "breastfeeding", {"action": "start", "side": "left"})
    assert err.value.translation_placeholders["code"] == "FEED_ALREADY_ACTIVE"


async def test_errors(
    hass: HomeAssistant, one_baby: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """API errors and outages come back as readable action errors."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{API}/babies/{BABY_1}/activities",
        status=403,
        json=api_error("INSUFFICIENT_SCOPE", "This key does not have 'write' scope"),
    )
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "log_diaper", {"diaper_type": "wet"})
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "api_error"
    assert err.value.translation_placeholders == {
        "code": "INSUFFICIENT_SCOPE",
        "message": "This key does not have 'write' scope",
    }

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{API}/babies/{BABY_1}/activities",
        status=400,
        json=api_error("NO_ACTIVE_SLEEP", "No active sleep session found to end"),
    )
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "end_sleep", {})
    assert err.value.translation_placeholders["code"] == "NO_ACTIVE_SLEEP"

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API}/babies/{BABY_1}/activities", exc=TimeoutError)
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "end_sleep", {})
    assert err.value.translation_key == "cannot_connect"


@pytest.mark.parametrize(
    ("button", "payload"),
    [
        ("log_wet_diaper", {"type": "diaper", "diaperType": "WET"}),
        ("log_dirty_diaper", {"type": "diaper", "diaperType": "DIRTY"}),
        ("log_wet_and_dirty_diaper", {"type": "diaper", "diaperType": "BOTH"}),
        ("start_nap", {"type": "sleep", "action": "start", "sleepType": "NAP"}),
        ("start_night_sleep", {"type": "sleep", "action": "start", "sleepType": "NIGHT_SLEEP"}),
        ("end_sleep", {"type": "sleep", "action": "end"}),
        (
            "start_breastfeeding_left",
            {"type": "feed", "feedType": "BREAST", "action": "start", "side": "LEFT"},
        ),
        (
            "start_breastfeeding_right",
            {"type": "feed", "feedType": "BREAST", "action": "start", "side": "RIGHT"},
        ),
        ("switch_breastfeeding_side", {"type": "feed", "feedType": "BREAST", "action": "switch"}),
        ("end_breastfeeding", {"type": "feed", "feedType": "BREAST", "action": "end"}),
    ],
)
async def test_buttons(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    button: str,
    payload: dict[str, Any],
) -> None:
    """Each button logs its activity for its own baby and refreshes the data."""
    polls = sum(str(c[1]).endswith("/babies") for c in aioclient_mock.mock_calls)
    await hass.services.async_call(
        "button", "press", {"entity_id": f"button.mia_{button}"}, blocking=True
    )
    await hass.async_block_till_done()
    assert _posts(aioclient_mock) == [(BABY_2, payload)]
    assert sum(str(c[1]).endswith("/babies") for c in aioclient_mock.mock_calls) == polls + 1


def test_translations_cover_everything() -> None:
    """Every action, field, entity and error has an English and a German text."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    import yaml  # noqa: PLC0415

    from custom_components.sprout_track import binary_sensor, button, sensor  # noqa: PLC0415

    base = Path(sensor.__file__).parent
    services = yaml.safe_load((base / "services.yaml").read_text())
    icons = json.loads((base / "icons.json").read_text())
    translations = {
        lang: json.loads((base / "translations" / f"{lang}.json").read_text())
        for lang in ("en", "de")
    }

    def keys(node: Any, prefix: str = "") -> set[str]:
        if not isinstance(node, dict):
            return {prefix}
        return {k for key, value in node.items() for k in keys(value, f"{prefix}/{key}")}

    assert keys(translations["de"]) == keys(translations["en"])

    for strings in translations.values():
        for name, service in services.items():
            assert set(strings["services"][name]["fields"]) == set(service["fields"]), name
            assert name in icons["services"]
        for platform, descriptions in (
            ("sensor", (*sensor.SENSORS, *sensor.MEASUREMENTS)),
            ("binary_sensor", binary_sensor.BINARY_SENSORS),
            ("button", button.BUTTONS),
        ):
            expected = {d.translation_key for d in descriptions}
            if platform == "sensor":
                expected |= set(sensor.DOSE_TRANSLATION_KEYS)
            if platform == "binary_sensor":
                expected |= set(binary_sensor.DOSE_TRANSLATION_KEYS)
            assert set(strings["entity"][platform]) == expected
        assert set(strings["entity"]["sensor"]["sleep_state"]["state"]) == set(sensor.SLEEP_STATES)


def test_brand_icons() -> None:
    """The icon ships in both sizes Home Assistant asks for."""
    from pathlib import Path  # noqa: PLC0415
    import struct  # noqa: PLC0415

    from custom_components import sprout_track  # noqa: PLC0415

    brand = Path(sprout_track.__file__).parent / "brand"
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        data = (brand / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", data[16:24]) == (size, size)
