"""Sensors: last activities, today's counts, age and measurements."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfLength, UnitOfMass, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import BabyData, SproutTrackConfigEntry, SproutTrackCoordinator
from .entity import SproutTrackEntity, add_entities_per_baby

# The coordinator polls; entities never fetch on their own.
PARALLEL_UPDATES = 0

SLEEP_STATES = ["awake", "nap", "night_sleep", "asleep"]


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
    ),
    SproutTrackSensorDescription(
        key="supplements_today",
        translation_key="supplements_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("supplements"),
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
