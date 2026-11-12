"""Binary sensors: sleeping, overdue warnings, running timers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BabyData, SproutTrackConfigEntry, SproutTrackCoordinator
from .entity import SproutTrackEntity, add_entities_per_baby

PARALLEL_UPDATES = 0


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
        "side": feed.get("activeSide"),
        "paused": feed.get("isPaused"),
        "started": feed.get("sessionStartTime"),
    }


BINARY_SENSORS: tuple[SproutTrackBinarySensorDescription, ...] = (
    SproutTrackBinarySensorDescription(
        key="sleeping",
        translation_key="sleeping",
        is_on_fn=lambda baby: baby.is_asleep if baby.status else None,
        attrs_fn=lambda baby: (
            {"sleep_type": baby.last("sleep").get("type")} if baby.is_asleep else {}
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
