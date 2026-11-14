"""Actions that log activities, and the helpers the buttons share with them."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import SproutTrackApiError, SproutTrackError
from .const import DOMAIN
from .coordinator import SproutTrackConfigEntry, SproutTrackCoordinator

SERVICE_LOG_DIAPER = "log_diaper"
SERVICE_START_SLEEP = "start_sleep"
SERVICE_END_SLEEP = "end_sleep"
SERVICE_LOG_SLEEP = "log_sleep"
SERVICE_LOG_MEDICINE = "log_medicine"
SERVICE_LOG_SUPPLEMENT = "log_supplement"
SERVICE_LOG_ACTIVITY = "log_activity"
SERVICE_LOG_FEED = "log_feed"
SERVICE_BREASTFEEDING = "breastfeeding"

ATTR_TIME = "time"
ATTR_CARETAKER = "caretaker"
ATTR_NOTES = "notes"
ATTR_DIAPER_TYPE = "diaper_type"
ATTR_CONDITION = "condition"
ATTR_COLOR = "color"
ATTR_BLOWOUT = "blowout"
ATTR_CREAM_APPLIED = "cream_applied"
ATTR_SLEEP_TYPE = "sleep_type"
ATTR_LOCATION = "location"
ATTR_QUALITY = "quality"
ATTR_DURATION = "duration"
ATTR_MEDICINE = "medicine"
ATTR_SUPPLEMENT = "supplement"
ATTR_AMOUNT = "amount"
ATTR_UNIT = "unit"
ATTR_DATA = "data"
ATTR_FEED_TYPE = "feed_type"
ATTR_BOTTLE_TYPE = "bottle_type"
ATTR_SIDE = "side"
ATTR_ACTION = "action"

DIAPER_TYPES = ["wet", "dirty", "both", "dry"]
DIAPER_CONDITIONS = ["normal", "loose", "firm", "other"]
DIAPER_COLORS = ["yellow", "brown", "green", "black", "red", "other"]
SLEEP_TYPES = ["nap", "night_sleep"]
SLEEP_QUALITIES = ["poor", "fair", "good", "excellent"]
FEED_TYPES = ["bottle", "breast"]
# Action values and how Sprout Track spells them
BOTTLE_TYPES = {
    "formula": "Formula",
    "breast_milk": "Breast Milk",
    "formula_breast": "Formula/Breast",
    "milk": "Milk",
    "other": "Other",
}
SIDES = ["left", "right"]
BREASTFEEDING_ACTIONS = ["start", "switch", "pause", "resume", "end"]
# Fields that only fit one kind of feed
_BOTTLE_ONLY = (ATTR_BOTTLE_TYPE,)
_BREAST_ONLY = (ATTR_SIDE, ATTR_DURATION)

_COMMON = {
    vol.Optional(ATTR_DEVICE_ID): cv.string,
    vol.Optional(ATTR_TIME): cv.datetime,
    vol.Optional(ATTR_CARETAKER): cv.string,
}


def _lower_in(options: list[str]) -> vol.All:
    return vol.All(cv.string, vol.Lower, vol.In(options))


LOG_DIAPER_SCHEMA = vol.Schema(
    {
        **_COMMON,
        vol.Required(ATTR_DIAPER_TYPE): _lower_in(DIAPER_TYPES),
        vol.Optional(ATTR_CONDITION): _lower_in(DIAPER_CONDITIONS),
        vol.Optional(ATTR_COLOR): _lower_in(DIAPER_COLORS),
        vol.Optional(ATTR_BLOWOUT): cv.boolean,
        vol.Optional(ATTR_CREAM_APPLIED): cv.boolean,
        vol.Optional(ATTR_NOTES): cv.string,
    }
)

START_SLEEP_SCHEMA = vol.Schema(
    {
        **_COMMON,
        vol.Optional(ATTR_SLEEP_TYPE, default="nap"): _lower_in(SLEEP_TYPES),
        vol.Optional(ATTR_LOCATION): cv.string,
        vol.Optional(ATTR_NOTES): cv.string,
    }
)

END_SLEEP_SCHEMA = vol.Schema(
    {
        **_COMMON,
        vol.Optional(ATTR_SLEEP_TYPE): _lower_in(SLEEP_TYPES),
        vol.Optional(ATTR_QUALITY): _lower_in(SLEEP_QUALITIES),
        vol.Optional(ATTR_NOTES): cv.string,
    }
)

LOG_SLEEP_SCHEMA = vol.Schema(
    {
        **_COMMON,
        vol.Optional(ATTR_SLEEP_TYPE, default="nap"): _lower_in(SLEEP_TYPES),
        vol.Required(ATTR_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_LOCATION): cv.string,
        vol.Optional(ATTR_QUALITY): _lower_in(SLEEP_QUALITIES),
        vol.Optional(ATTR_NOTES): cv.string,
    }
)

_DOSE = {
    vol.Optional(ATTR_AMOUNT): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional(ATTR_UNIT): cv.string,
    vol.Optional(ATTR_NOTES): cv.string,
}

LOG_MEDICINE_SCHEMA = vol.Schema({**_COMMON, vol.Required(ATTR_MEDICINE): cv.string, **_DOSE})
LOG_SUPPLEMENT_SCHEMA = vol.Schema({**_COMMON, vol.Required(ATTR_SUPPLEMENT): cv.string, **_DOSE})

LOG_FEED_SCHEMA = vol.Schema(
    {
        **_COMMON,
        vol.Optional(ATTR_FEED_TYPE, default="bottle"): _lower_in(FEED_TYPES),
        vol.Optional(ATTR_AMOUNT): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional(ATTR_UNIT): cv.string,
        vol.Optional(ATTR_BOTTLE_TYPE): _lower_in(list(BOTTLE_TYPES)),
        vol.Optional(ATTR_SIDE): _lower_in(SIDES),
        vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_NOTES): cv.string,
    }
)

BREASTFEEDING_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_ACTION): _lower_in(BREASTFEEDING_ACTIONS),
        vol.Optional(ATTR_SIDE): _lower_in(SIDES),
        vol.Optional(ATTR_CARETAKER): cv.string,
    }
)

LOG_ACTIVITY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_DATA): vol.All(
            dict, vol.Schema({vol.Required("type"): str}, extra=vol.ALLOW_EXTRA)
        ),
    }
)


def _loaded_entries(hass: HomeAssistant) -> list[SproutTrackConfigEntry]:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _resolve_baby(hass: HomeAssistant, device_id: str | None) -> tuple[SproutTrackCoordinator, str]:
    """Find the baby an action is for.

    With a device: that baby. Without: the only baby there is, so homes with
    one child never have to pick.
    """
    entries = _loaded_entries(hass)
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        baby_ids = (
            {ident[1] for ident in device.identifiers if ident[0] == DOMAIN} if device else set()
        )
        for entry in entries:
            for baby_id in baby_ids:
                if baby_id in entry.runtime_data.data:
                    return entry.runtime_data, baby_id
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="baby_not_found")

    babies = [
        (entry.runtime_data, baby_id) for entry in entries for baby_id in entry.runtime_data.data
    ]
    if not babies:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_baby")
    if len(babies) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="multiple_babies")
    return babies[0]


def _to_error(err: SproutTrackError) -> HomeAssistantError:
    """Turn a client error into one Home Assistant shows to the user.

    What the API refuses as wrong input (unknown medicine, no sleep to end)
    is the caller's mistake, not a fault: no traceback in the log for those.
    """
    if isinstance(err, SproutTrackApiError):
        error_class = (
            ServiceValidationError if err.status in (400, 404, 409) else HomeAssistantError
        )
        return error_class(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"code": err.code, "message": err.message},
        )
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="cannot_connect",
        translation_placeholders={"message": str(err)},
    )


async def async_log_activity(
    coordinator: SproutTrackCoordinator, baby_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Send one activity, then refresh the entities right away."""
    try:
        result = await coordinator.client.create_activity(baby_id, payload)
    except SproutTrackError as err:
        raise _to_error(err) from err
    if payload.get("type") == "measurement":
        coordinator.invalidate_measurements()
    # Not async_request_refresh: that waits 10 s, and a pressed button
    # should show its effect at once.
    await coordinator.async_refresh()
    return result


async def async_start_sleep(
    coordinator: SproutTrackCoordinator, baby_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Start a sleep, unless one is running.

    The API would start a second one next to it, so look first (with fresh
    data: the coordinator's can be a minute old).
    """
    try:
        status = await coordinator.client.get_status(baby_id)
    except SproutTrackError as err:
        raise _to_error(err) from err
    if ((status.get("lastActivities") or {}).get("sleep") or {}).get("isActive"):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="already_asleep",
            translation_placeholders={"name": coordinator.data[baby_id].name},
        )
    return await async_log_activity(
        coordinator, baby_id, {"type": "sleep", "action": "start", **payload}
    )


def _whole(amount: float) -> float | int:
    """Send 2.0 as 2, so whole amounts look the same as typed in the app."""
    return int(amount) if float(amount).is_integer() else amount


def is_sleep_start(payload: dict[str, Any]) -> bool:
    """Return whether a payload starts a sleep (checked for a running one first)."""
    return payload.get("type") == "sleep" and payload.get("action") == "start"


def _common_payload(call: ServiceCall) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    time: datetime | None = call.data.get(ATTR_TIME)
    if time is not None:
        # A time without zone is local time, as everywhere in Home Assistant
        payload["time"] = dt_util.as_utc(time).isoformat()
    if caretaker := call.data.get(ATTR_CARETAKER):
        payload["caretakerName"] = caretaker
    return payload


def _copy(
    call: ServiceCall, payload: dict[str, Any], fields: dict[str, str], *, upper: bool = False
) -> None:
    """Copy optional action fields into the API payload under their API names."""
    for attr, api_key in fields.items():
        if (value := call.data.get(attr)) is not None:
            payload[api_key] = value.upper() if upper else value


async def _log_feed(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Log a bottle feed or a finished breastfeed."""
    coordinator, baby_id = _resolve_baby(hass, call.data.get(ATTR_DEVICE_ID))
    feed_type: str = call.data[ATTR_FEED_TYPE]
    wrong = _BREAST_ONLY if feed_type == "bottle" else _BOTTLE_ONLY
    if field := next((f for f in wrong if f in call.data), None):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="feed_field_mismatch",
            translation_placeholders={"field": field, "feed_type": feed_type},
        )
    if ATTR_AMOUNT in call.data and ATTR_UNIT not in call.data:
        # Sprout Track would store the amount without a unit, and nothing
        # (Fed today included) could tell millilitres from ounces
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unit_required")
    payload: dict[str, Any] = {
        "type": "feed",
        "feedType": feed_type.upper(),
        **_common_payload(call),
    }
    if (amount := call.data.get(ATTR_AMOUNT)) is not None:
        payload["amount"] = _whole(amount)
    if bottle_type := call.data.get(ATTR_BOTTLE_TYPE):
        payload["bottleType"] = BOTTLE_TYPES[bottle_type]
    _copy(call, payload, {ATTR_SIDE: "side"}, upper=True)
    _copy(call, payload, {ATTR_UNIT: "unitAbbr", ATTR_DURATION: "duration", ATTR_NOTES: "notes"})
    return await async_log_activity(coordinator, baby_id, payload)


async def _breastfeeding(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Control the breastfeeding timer shared with the app."""
    coordinator, baby_id = _resolve_baby(hass, call.data.get(ATTR_DEVICE_ID))
    action: str = call.data[ATTR_ACTION]
    if action == "start" and ATTR_SIDE not in call.data:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="side_required")
    payload: dict[str, Any] = {"type": "feed", "feedType": "BREAST", "action": action}
    _copy(call, payload, {ATTR_SIDE: "side"}, upper=True)
    _copy(call, payload, {ATTR_CARETAKER: "caretakerName"})
    return await async_log_activity(coordinator, baby_id, payload)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""

    async def log_diaper(call: ServiceCall) -> ServiceResponse:
        coordinator, baby_id = _resolve_baby(hass, call.data.get(ATTR_DEVICE_ID))
        payload = {"type": "diaper", **_common_payload(call)}
        _copy(
            call,
            payload,
            {
                ATTR_DIAPER_TYPE: "diaperType",
                ATTR_CONDITION: "condition",
                ATTR_COLOR: "color",
            },
            upper=True,
        )
        _copy(
            call,
            payload,
            {
                ATTR_BLOWOUT: "blowout",
                ATTR_CREAM_APPLIED: "creamApplied",
                ATTR_NOTES: "notes",
            },
        )
        return await async_log_activity(coordinator, baby_id, payload)

    async def start_sleep(call: ServiceCall) -> ServiceResponse:
        coordinator, baby_id = _resolve_baby(hass, call.data.get(ATTR_DEVICE_ID))
        payload = _common_payload(call)
        _copy(call, payload, {ATTR_SLEEP_TYPE: "sleepType"}, upper=True)
        _copy(call, payload, {ATTR_LOCATION: "location", ATTR_NOTES: "notes"})
        return await async_start_sleep(coordinator, baby_id, payload)

    async def end_sleep(call: ServiceCall) -> ServiceResponse:
        coordinator, baby_id = _resolve_baby(hass, call.data.get(ATTR_DEVICE_ID))
        payload = {"type": "sleep", "action": "end", **_common_payload(call)}
        _copy(
            call,
            payload,
            {ATTR_SLEEP_TYPE: "sleepType", ATTR_QUALITY: "quality"},
            upper=True,
        )
        _copy(call, payload, {ATTR_NOTES: "notes"})
        return await async_log_activity(coordinator, baby_id, payload)

    async def log_sleep(call: ServiceCall) -> ServiceResponse:
        coordinator, baby_id = _resolve_baby(hass, call.data.get(ATTR_DEVICE_ID))
        payload = {"type": "sleep", "action": "log", **_common_payload(call)}
        _copy(
            call,
            payload,
            {ATTR_SLEEP_TYPE: "sleepType", ATTR_QUALITY: "quality"},
            upper=True,
        )
        _copy(
            call,
            payload,
            {
                ATTR_DURATION: "duration",
                ATTR_LOCATION: "location",
                ATTR_NOTES: "notes",
            },
        )
        return await async_log_activity(coordinator, baby_id, payload)

    async def _log_dose(call: ServiceCall, kind: str) -> ServiceResponse:
        """Log a medicine or supplement; without amount, use its usual dose."""
        coordinator, baby_id = _resolve_baby(hass, call.data.get(ATTR_DEVICE_ID))
        name: str = call.data[kind]
        amount: float | None = call.data.get(ATTR_AMOUNT)
        if amount is None:
            try:
                reference = await coordinator.client.get_reference(baby_id, f"{kind}s")
            except SproutTrackError as err:
                raise _to_error(err) from err
            known = {
                str(item.get("name", "")).casefold(): item
                for item in reference.get(f"{kind}s") or []
            }
            item = known.get(name.casefold())
            if item is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key=f"{kind}_not_found",
                    translation_placeholders={
                        "name": name,
                        "available": ", ".join(sorted(str(i.get("name")) for i in known.values()))
                        or "-",
                    },
                )
            name = item["name"]
            amount = item.get("typicalDoseSize")
            if amount is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="amount_required",
                    translation_placeholders={"name": name},
                )
        payload = {
            "type": kind,
            f"{kind}Name": name,
            "amount": _whole(amount),
            **_common_payload(call),
        }
        _copy(call, payload, {ATTR_UNIT: "unitAbbr", ATTR_NOTES: "notes"})
        return await async_log_activity(coordinator, baby_id, payload)

    async def log_medicine(call: ServiceCall) -> ServiceResponse:
        return await _log_dose(call, ATTR_MEDICINE)

    async def log_supplement(call: ServiceCall) -> ServiceResponse:
        return await _log_dose(call, ATTR_SUPPLEMENT)

    async def log_activity(call: ServiceCall) -> ServiceResponse:
        coordinator, baby_id = _resolve_baby(hass, call.data.get(ATTR_DEVICE_ID))
        payload = dict(call.data[ATTR_DATA])
        if is_sleep_start(payload):
            payload.pop("type")
            payload.pop("action")
            return await async_start_sleep(coordinator, baby_id, payload)
        return await async_log_activity(coordinator, baby_id, payload)

    for name, handler, schema in (
        (SERVICE_LOG_DIAPER, log_diaper, LOG_DIAPER_SCHEMA),
        (SERVICE_START_SLEEP, start_sleep, START_SLEEP_SCHEMA),
        (SERVICE_END_SLEEP, end_sleep, END_SLEEP_SCHEMA),
        (SERVICE_LOG_SLEEP, log_sleep, LOG_SLEEP_SCHEMA),
        (SERVICE_LOG_MEDICINE, log_medicine, LOG_MEDICINE_SCHEMA),
        (SERVICE_LOG_SUPPLEMENT, log_supplement, LOG_SUPPLEMENT_SCHEMA),
        (SERVICE_LOG_FEED, partial(_log_feed, hass), LOG_FEED_SCHEMA),
        (SERVICE_BREASTFEEDING, partial(_breastfeeding, hass), BREASTFEEDING_SCHEMA),
        (SERVICE_LOG_ACTIVITY, log_activity, LOG_ACTIVITY_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
