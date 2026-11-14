"""Binary sensors: sleeping, overdue warnings, running timers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import DOSE_KINDS
from .coordinator import BabyData, SproutTrackConfigEntry, SproutTrackCoordinator, parse_dose_gap
from .entity import (
    SproutTrackDoseEntity,
    SproutTrackEntity,
    add_dose_entities,
    add_entities_per_baby,
    attribute_value,
)

PARALLEL_UPDATES = 0

# Made per medicine and supplement (not in BINARY_SENSORS: they come and go
# with the medicines set up in Sprout Track)
DOSE_TRANSLATION_KEYS = tuple(f"{kind}_dose_allowed" for kind in DOSE_KINDS)


@dataclass(frozen=True, kw_only=True)
class SproutTrackBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor and how to read it from a baby's data."""

    is_on_fn: Callable[[BabyData], bool | None]
    attrs_fn: Callable[[BabyData], dict[str, Any]] | None = None


def _warning(key: str) -> Callable[[BabyData], bool | None]:
    def is_on(baby: BabyData) -> bool | None:
        warnings = baby.status.get("warnings")
        return bool(warnings.get(key)) if warnings else None

    return is_on


def _breastfeeding_attrs(baby: BabyData) -> dict[str, Any]:
    feed = baby.status.get("activeFeed") or {}
    return {
        "side": attribute_value("side", feed.get("activeSide")),
        "paused": feed.get("isPaused"),
        "started": feed.get("sessionStartTime"),
    }


BINARY_SENSORS: tuple[SproutTrackBinarySensorDescription, ...] = (
    SproutTrackBinarySensorDescription(
        key="sleeping",
        translation_key="sleeping",
        is_on_fn=lambda baby: baby.is_asleep if baby.status else None,
        attrs_fn=lambda baby: (
            {"sleep_type": attribute_value("sleep_type", baby.last("sleep").get("type"))}
            if baby.is_asleep
            else {}
        ),
    ),
    SproutTrackBinarySensorDescription(
        key="feed_overdue",
        translation_key="feed_overdue",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_warning("feedOverdue"),
        attrs_fn=lambda baby: {"warning_after": baby.info.get("feedWarningTime")},
    ),
    SproutTrackBinarySensorDescription(
        key="diaper_overdue",
        translation_key="diaper_overdue",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_warning("diaperOverdue"),
        attrs_fn=lambda baby: {"warning_after": baby.info.get("diaperWarningTime")},
    ),
    SproutTrackBinarySensorDescription(
        key="breastfeeding",
        translation_key="breastfeeding",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda baby: bool(baby.status.get("activeFeed")) if baby.status else None,
        attrs_fn=_breastfeeding_attrs,
    ),
    SproutTrackBinarySensorDescription(
        key="pumping",
        translation_key="pumping",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda baby: bool(baby.last("pump").get("isActive")) if baby.status else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SproutTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors of every baby."""

    def factory(coordinator: SproutTrackCoordinator, baby_id: str) -> list[Entity]:
        return [
            SproutTrackBinarySensor(coordinator, baby_id, description)
            for description in BINARY_SENSORS
        ]

    add_entities_per_baby(entry, async_add_entities, factory)
    # Medicines with a minimum time between doses: may it be given now?
    add_dose_entities(
        hass,
        entry,
        async_add_entities,
        domain="binary_sensor",
        whiches=lambda item: ("dose_allowed",) if parse_dose_gap(item.get("doseMinTime")) else (),
        factory=lambda coordinator, key, name: SproutTrackDoseAllowedSensor(
            coordinator, key, name=name
        ),
        all_whiches=("dose_allowed",),
    )


class SproutTrackBinarySensor(SproutTrackEntity, BinarySensorEntity):
    """A binary sensor read from the baby's status."""

    entity_description: SproutTrackBinarySensorDescription

    def __init__(
        self,
        coordinator: SproutTrackCoordinator,
        baby_id: str,
        description: SproutTrackBinarySensorDescription,
    ) -> None:
        """Set up the binary sensor."""
        super().__init__(coordinator, baby_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.is_on_fn(self.baby)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return details, without empty fields."""
        if self.entity_description.attrs_fn is None:
            return None
        attrs = self.entity_description.attrs_fn(self.baby)
        return {key: value for key, value in attrs.items() if value is not None}


class SproutTrackDoseAllowedSensor(SproutTrackDoseEntity, BinarySensorEntity):
    """On while a medicine may be given: its minimum gap since the last dose has passed.

    Turns on at that moment by itself, not only at the next update.
    """

    _unsub_timer: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        """Return whether the medicine still has a minimum gap."""
        return (
            super().available and parse_dose_gap((self._item or {}).get("doseMinTime")) is not None
        )

    @property
    def _next_dose(self) -> datetime | None:
        dose = self._last_dose
        return self.baby.next_dose(self._kind, dose) if dose else None

    @property
    def is_on(self) -> bool:
        """Return whether the next dose is allowed (also when none was given lately)."""
        next_dose = self._next_dose
        return next_dose is None or dt_util.utcnow() >= next_dose

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return when the next dose is allowed, while that is still ahead."""
        next_dose = self._next_dose
        if next_dose is None or dt_util.utcnow() >= next_dose:
            return None
        return {"allowed_from": dt_util.as_local(next_dose).isoformat()}

    async def async_added_to_hass(self) -> None:
        """Start the timer for the current data."""
        await super().async_added_to_hass()
        self._schedule()
        self.async_on_remove(self._cancel_timer)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule()
        super()._handle_coordinator_update()

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _schedule(self) -> None:
        """Wake up when the next dose becomes allowed."""
        self._cancel_timer()
        if not self.available:
            return
        next_dose = self._next_dose
        if next_dose is not None and next_dose > dt_util.utcnow():
            self._unsub_timer = async_track_point_in_utc_time(
                self.hass, self._allowed_now, next_dose
            )

    @callback
    def _allowed_now(self, _now: datetime) -> None:
        self._unsub_timer = None
        self.async_write_ha_state()
