"""Sensors: last activities, today's counts, age, measurements and doses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfLength,
    UnitOfMass,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import DOSE_KINDS
from .coordinator import (
    BabyData,
    SproutTrackConfigEntry,
    SproutTrackCoordinator,
    dose_name,
    parse_dose_gap,
)
from .entity import SproutTrackEntity, add_entities_per_baby

# The coordinator polls; entities never fetch on their own.
PARALLEL_UPDATES = 0

SLEEP_STATES = ["awake", "nap", "night_sleep", "asleep"]

DOSE_SUFFIXES = ("_last_given", "_next_dose")


def _dose_unique_id(baby_id: str, kind: str, item_id: str, which: str) -> str:
    return f"{baby_id}_{kind}_{item_id}_{which}"


# Sensors made per medicine and supplement (not in SENSORS: they come and go
# with the medicines set up in Sprout Track).
DOSE_TRANSLATION_KEYS = tuple(
    f"{kind}_{which}" for kind in DOSE_KINDS for which in ("last_given", "next_dose")
)


@dataclass(frozen=True, kw_only=True)
class SproutTrackSensorDescription(SensorEntityDescription):
    """A sensor and how to read it from a baby's data."""

    value_fn: Callable[[BabyData], StateType | datetime]
    attrs_fn: Callable[[BabyData], dict[str, Any]] | None = None


def _timestamp(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def _last_time(kind: str, key: str = "time") -> Callable[[BabyData], datetime | None]:
    return lambda baby: _timestamp(baby.last(kind).get(key))


def _attrs(kind: str, **names: str) -> Callable[[BabyData], dict[str, Any]]:
    """Map API fields of a last activity to attribute names."""

    def attrs(baby: BabyData) -> dict[str, Any]:
        last = baby.last(kind)
        return {attr: last.get(api_key) for attr, api_key in names.items()}

    return attrs


def _count(key: str) -> Callable[[BabyData], StateType]:
    return lambda baby: (baby.status.get("dailyCounts") or {}).get(key)


def _diapers(*types: str) -> Callable[[BabyData], StateType]:
    def count(baby: BabyData) -> StateType:
        counts = baby.status.get("dailyCounts")
        if not counts:
            return None
        by_type = counts.get("diapersByType") or {}
        return sum(by_type.get(diaper_type, 0) for diaper_type in types)

    return count


def _next_dose(baby: BabyData, kind: str, dose: dict[str, Any]) -> datetime | None:
    """Return when the next dose of a dose's medicine is allowed, if it has a gap."""
    name = dose_name(dose, kind).casefold()
    item = next((i for i in baby.items(kind) if str(i.get("name", "")).casefold() == name), None)
    gap = parse_dose_gap(item.get("doseMinTime")) if item else None
    given = _timestamp(dose.get("time"))
    return given + gap if gap and given else None


def _iso(value: datetime | None) -> str | None:
    return dt_util.as_local(value).isoformat() if value else None


def _dose_entry(kind: str, dose: dict[str, Any]) -> dict[str, Any]:
    details = dose.get("details") or {}
    return {
        "time": _iso(_timestamp(dose.get("time"))),
        "name": dose_name(dose, kind),
        "amount": details.get("doseAmount"),
        "unit": details.get("unitAbbr"),
    }


def _dose_lists(kind: str) -> Callable[[BabyData], dict[str, Any]]:
    """Today's doses, and the latest dose of each one given in the last 24 hours."""

    def attrs(baby: BabyData) -> dict[str, Any]:
        doses = baby.doses_of(kind)
        midnight = dt_util.start_of_local_day()
        day_ago = dt_util.utcnow() - timedelta(hours=24)
        today = [
            _dose_entry(kind, d)
            for d in doses
            if (t := _timestamp(d.get("time"))) and t >= midnight
        ]
        latest: list[dict[str, Any]] = []
        seen: set[str] = set()
        for dose in doses:
            given = _timestamp(dose.get("time"))
            name = dose_name(dose, kind).casefold()
            if not given or given < day_ago or name in seen:
                continue
            seen.add(name)
            latest.append(
                {
                    **_dose_entry(kind, dose),
                    "next_dose": _iso(_next_dose(baby, kind, dose)),
                }
            )
        return {"doses": today, "latest": latest}

    return attrs


def _fed_today(baby: BabyData) -> StateType:
    return baby.fed_since(dt_util.start_of_local_day())


def _fed_attrs(baby: BabyData) -> dict[str, Any]:
    return {"last_24_hours": baby.fed_since(dt_util.utcnow() - timedelta(hours=24))}


def _sleep_state(baby: BabyData) -> str | None:
    if not baby.status:
        return None
    if not baby.is_asleep:
        return "awake"
    sleep_type = str(baby.last("sleep").get("type") or "").lower()
    return sleep_type if sleep_type in SLEEP_STATES else "asleep"


def _asleep_since(baby: BabyData) -> datetime | None:
    return _timestamp(baby.last("sleep").get("startTime")) if baby.is_asleep else None


def _awake_since(baby: BabyData) -> datetime | None:
    return None if baby.is_asleep else _timestamp(baby.last("sleep").get("endTime"))


def _last_sleep_duration(baby: BabyData) -> StateType:
    return None if baby.is_asleep else baby.last("sleep").get("duration")


def _last_pump(baby: BabyData) -> datetime | None:
    pump = baby.last("pump")
    return _timestamp(pump.get("endTime") or pump.get("startTime"))


SENSORS: tuple[SproutTrackSensorDescription, ...] = (
    # --- last activities -------------------------------------------------
    SproutTrackSensorDescription(
        key="last_feed",
        translation_key="last_feed",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_time("feed"),
        attrs_fn=_attrs(
            "feed",
            feed_type="type",
            amount="amount",
            unit="unitAbbr",
            bottle_type="bottleType",
            caretaker="caretakerName",
        ),
    ),
    SproutTrackSensorDescription(
        key="last_diaper",
        translation_key="last_diaper",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_time("diaper"),
        attrs_fn=_attrs("diaper", diaper_type="type", caretaker="caretakerName"),
    ),
    SproutTrackSensorDescription(
        key="sleep_state",
        translation_key="sleep_state",
        device_class=SensorDeviceClass.ENUM,
        options=SLEEP_STATES,
        value_fn=_sleep_state,
    ),
    SproutTrackSensorDescription(
        key="asleep_since",
        translation_key="asleep_since",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_asleep_since,
    ),
    SproutTrackSensorDescription(
        key="awake_since",
        translation_key="awake_since",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_awake_since,
    ),
    SproutTrackSensorDescription(
        key="last_sleep_duration",
        translation_key="last_sleep_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=_last_sleep_duration,
        attrs_fn=_attrs("sleep", sleep_type="type"),
    ),
    SproutTrackSensorDescription(
        key="last_bath",
        translation_key="last_bath",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_time("bath"),
        attrs_fn=_attrs("bath", bath_type="bathType"),
    ),
    SproutTrackSensorDescription(
        key="last_medicine",
        translation_key="last_medicine",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_time("medicine"),
        attrs_fn=_attrs("medicine", medicine="medicineName"),
    ),
    SproutTrackSensorDescription(
        key="last_supplement",
        translation_key="last_supplement",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_time("supplement"),
        attrs_fn=_attrs("supplement", supplement="supplementName"),
    ),
    SproutTrackSensorDescription(
        key="last_pump",
        translation_key="last_pump",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_pump,
        attrs_fn=_attrs("pump", duration="duration"),
    ),
    # --- today -------------------------------------------------------------
    SproutTrackSensorDescription(
        key="feeds_today",
        translation_key="feeds_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("feeds"),
    ),
    SproutTrackSensorDescription(
        key="fed_today",
        translation_key="fed_today",
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_fed_today,
        attrs_fn=_fed_attrs,
    ),
    SproutTrackSensorDescription(
        key="diapers_today",
        translation_key="diapers_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("diapers"),
    ),
    SproutTrackSensorDescription(
        key="wet_diapers_today",
        translation_key="wet_diapers_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_diapers("WET", "BOTH"),
    ),
    SproutTrackSensorDescription(
        key="dirty_diapers_today",
        translation_key="dirty_diapers_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_diapers("DIRTY", "BOTH"),
    ),
    SproutTrackSensorDescription(
        key="sleep_today",
        translation_key="sleep_today",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("sleepMinutes"),
    ),
    SproutTrackSensorDescription(
        key="naps_today",
        translation_key="naps_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("naps"),
    ),
    SproutTrackSensorDescription(
        key="baths_today",
        translation_key="baths_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("baths"),
    ),
    SproutTrackSensorDescription(
        key="medicines_today",
        translation_key="medicines_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("medicines"),
        attrs_fn=_dose_lists("medicine"),
    ),
    SproutTrackSensorDescription(
        key="supplements_today",
        translation_key="supplements_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("supplements"),
        attrs_fn=_dose_lists("supplement"),
    ),
    # --- the baby ----------------------------------------------------------
    SproutTrackSensorDescription(
        key="age",
        translation_key="age",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda baby: baby.info.get("ageInDays"),
        attrs_fn=lambda baby: {
            "birth_date": baby.info.get("birthDate"),
            "age_text": baby.info.get("ageFormatted"),
        },
    ),
)


@dataclass(frozen=True, kw_only=True)
class SproutTrackMeasurementDescription(SensorEntityDescription):
    """A measurement sensor; unit and device class follow the logged unit."""

    measurement_type: str
    measured_class: SensorDeviceClass
    units: dict[str, str]


_MASS = {
    "KG": UnitOfMass.KILOGRAMS,
    "G": UnitOfMass.GRAMS,
    "LB": UnitOfMass.POUNDS,
    "OZ": UnitOfMass.OUNCES,
}
_LENGTH = {
    "CM": UnitOfLength.CENTIMETERS,
    "MM": UnitOfLength.MILLIMETERS,
    "M": UnitOfLength.METERS,
    "IN": UnitOfLength.INCHES,
}
_TEMPERATURE = {"C": UnitOfTemperature.CELSIUS, "F": UnitOfTemperature.FAHRENHEIT}

MEASUREMENTS: tuple[SproutTrackMeasurementDescription, ...] = (
    SproutTrackMeasurementDescription(
        key="weight",
        translation_key="weight",
        measurement_type="WEIGHT",
        measured_class=SensorDeviceClass.WEIGHT,
        units=_MASS,
    ),
    SproutTrackMeasurementDescription(
        key="height",
        translation_key="height",
        measurement_type="HEIGHT",
        measured_class=SensorDeviceClass.DISTANCE,
        units=_LENGTH,
    ),
    SproutTrackMeasurementDescription(
        key="head_circumference",
        translation_key="head_circumference",
        measurement_type="HEAD_CIRCUMFERENCE",
        measured_class=SensorDeviceClass.DISTANCE,
        units=_LENGTH,
    ),
    SproutTrackMeasurementDescription(
        key="temperature",
        translation_key="temperature",
        measurement_type="TEMPERATURE",
        measured_class=SensorDeviceClass.TEMPERATURE,
        units=_TEMPERATURE,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SproutTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of every baby."""

    def factory(coordinator: SproutTrackCoordinator, baby_id: str) -> list[Entity]:
        return [
            *(SproutTrackSensor(coordinator, baby_id, desc) for desc in SENSORS),
            *(SproutTrackMeasurementSensor(coordinator, baby_id, desc) for desc in MEASUREMENTS),
        ]

    add_entities_per_baby(entry, async_add_entities, factory)
    _add_dose_sensors(hass, entry, async_add_entities)


def _add_dose_sensors(
    hass: HomeAssistant,
    entry: SproutTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Keep one sensor per medicine and supplement, as they come and go.

    Every one gets *last given*; those with a minimum time between doses
    also get *next dose*.
    """
    coordinator = entry.runtime_data
    known: dict[tuple[str, str, str, str], SproutTrackDoseSensor] = {}

    @callback
    def _check() -> None:
        wanted: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for baby_id, baby in coordinator.data.items():
            for kind in DOSE_KINDS:
                for item in baby.items(kind):
                    item_id = str(item["id"])
                    wanted[(baby_id, kind, item_id, "last_given")] = item
                    if parse_dose_gap(item.get("doseMinTime")):
                        wanted[(baby_id, kind, item_id, "next_dose")] = item

        registry = er.async_get(hass)
        for key in known.keys() - wanted.keys():
            entity = known.pop(key)
            if entity.hass is not None and not (
                entity.entity_id and registry.async_get(entity.entity_id)
            ):
                hass.async_create_task(entity.async_remove())
        # Also sensors of medicines that went away while the integration was
        # not running (or reloading): those are only in the registry.
        wanted_ids = {_dose_unique_id(*key) for key in wanted}
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                reg_entry.domain == "sensor"
                and reg_entry.unique_id.endswith(DOSE_SUFFIXES)
                and reg_entry.unique_id not in wanted_ids
            ):
                registry.async_remove(reg_entry.entity_id)

        new = [
            SproutTrackDoseSensor(coordinator, *key, name=str(wanted[key].get("name") or ""))
            for key in sorted(wanted.keys() - known.keys())
        ]
        if new:
            known.update({entity.dose_key: entity for entity in new})
            async_add_entities(new)

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))


class SproutTrackSensor(SproutTrackEntity, SensorEntity):
    """A sensor read from the baby's status."""

    entity_description: SproutTrackSensorDescription

    def __init__(
        self,
        coordinator: SproutTrackCoordinator,
        baby_id: str,
        description: SproutTrackSensorDescription,
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, baby_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return the value."""
        return self.entity_description.value_fn(self.baby)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return details of the activity, without empty fields."""
        if self.entity_description.attrs_fn is None:
            return None
        attrs = self.entity_description.attrs_fn(self.baby)
        return {key: value for key, value in attrs.items() if value is not None}


class SproutTrackMeasurementSensor(SproutTrackEntity, SensorEntity):
    """The latest measurement of one type."""

    entity_description: SproutTrackMeasurementDescription
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SproutTrackCoordinator,
        baby_id: str,
        description: SproutTrackMeasurementDescription,
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, baby_id, description.key)
        self.entity_description = description

    @property
    def _measurement(self) -> dict[str, Any]:
        return self.baby.measurements.get(self.entity_description.measurement_type) or {}

    @property
    def _unit(self) -> str | None:
        """Return the Home Assistant unit for the logged unit, if known."""
        unit = str(self._measurement.get("unit") or "").replace("°", "").upper()
        return self.entity_description.units.get(unit.strip())

    @property
    def native_value(self) -> StateType:
        """Return the measured value."""
        return self._measurement.get("value")

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the known unit, else the unit as it was logged."""
        return self._unit or self._measurement.get("unit") or None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return the device class only for units Home Assistant can convert."""
        return self.entity_description.measured_class if self._unit else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the day it was measured."""
        date = self._measurement.get("date")
        return {"date": date} if date else None


class SproutTrackDoseSensor(SproutTrackEntity, SensorEntity):
    """When one medicine (or supplement) was last given, or may be given next."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: SproutTrackCoordinator,
        baby_id: str,
        kind: str,
        item_id: str,
        which: str,
        *,
        name: str,
    ) -> None:
        """Set up the sensor; the medicine's name goes into the entity name."""
        super().__init__(coordinator, baby_id, f"{kind}_{item_id}_{which}")
        self.dose_key = (baby_id, kind, item_id, which)
        self._kind = kind
        self._item_id = item_id
        self._which = which
        self._attr_translation_key = f"{kind}_{which}"
        self._attr_translation_placeholders = {"name": name}

    @property
    def _item(self) -> dict[str, Any] | None:
        return self.baby.item(self._kind, self._item_id)

    @property
    def _last_dose(self) -> dict[str, Any] | None:
        item = self._item
        if item is None:
            return None
        doses = self.baby.doses_of(self._kind, str(item.get("name") or ""))
        return doses[0] if doses else None

    @property
    def available(self) -> bool:
        """Return whether Sprout Track still lists this medicine."""
        return super().available and self._item is not None

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the last dose, or when the next one is allowed."""
        dose = self._last_dose
        if dose is None:
            return None
        if self._which == "last_given":
            return _timestamp(dose.get("time"))
        return _next_dose(self.baby, self._kind, dose)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the last dose's amount, today's count and the minimum gap."""
        item = self._item or {}
        dose = self._last_dose
        details = (dose or {}).get("details") or {}
        midnight = dt_util.start_of_local_day()
        name = str(item.get("name") or "")
        attrs: dict[str, Any] = {
            "amount": details.get("doseAmount"),
            "unit": details.get("unitAbbr") or item.get("unitAbbr"),
            "doses_today": sum(
                1
                for d in self.baby.doses_of(self._kind, name)
                if (t := _timestamp(d.get("time"))) and t >= midnight
            ),
        }
        gap = parse_dose_gap(item.get("doseMinTime"))
        if gap:
            attrs["minimum_gap_minutes"] = int(gap.total_seconds() // 60)
        return {key: value for key, value in attrs.items() if value is not None}
