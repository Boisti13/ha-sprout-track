"""Buttons for the things logged most often with one press."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SproutTrackConfigEntry, SproutTrackCoordinator
from .entity import SproutTrackEntity, add_entities_per_baby
from .services import async_log_activity, async_start_sleep, is_sleep_start

# One press, one request; let presses queue instead of racing each other.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SproutTrackButtonDescription(ButtonEntityDescription):
    """A button and the activity it logs."""

    payload: dict[str, Any]


BUTTONS: tuple[SproutTrackButtonDescription, ...] = (
    SproutTrackButtonDescription(
        key="log_wet_diaper",
        translation_key="log_wet_diaper",
        payload={"type": "diaper", "diaperType": "WET"},
    ),
    SproutTrackButtonDescription(
        key="log_dirty_diaper",
        translation_key="log_dirty_diaper",
        payload={"type": "diaper", "diaperType": "DIRTY"},
    ),
    SproutTrackButtonDescription(
        key="log_wet_and_dirty_diaper",
        translation_key="log_wet_and_dirty_diaper",
        payload={"type": "diaper", "diaperType": "BOTH"},
    ),
    SproutTrackButtonDescription(
        key="start_nap",
        translation_key="start_nap",
        payload={"type": "sleep", "action": "start", "sleepType": "NAP"},
    ),
    SproutTrackButtonDescription(
        key="start_night_sleep",
        translation_key="start_night_sleep",
        payload={"type": "sleep", "action": "start", "sleepType": "NIGHT_SLEEP"},
    ),
    SproutTrackButtonDescription(
        key="end_sleep",
        translation_key="end_sleep",
        payload={"type": "sleep", "action": "end"},
    ),
    # The breastfeeding timer, shared with the app (one session at a time)
    SproutTrackButtonDescription(
        key="start_breastfeeding_left",
        translation_key="start_breastfeeding_left",
        payload={"type": "feed", "feedType": "BREAST", "action": "start", "side": "LEFT"},
    ),
    SproutTrackButtonDescription(
        key="start_breastfeeding_right",
        translation_key="start_breastfeeding_right",
        payload={"type": "feed", "feedType": "BREAST", "action": "start", "side": "RIGHT"},
    ),
    SproutTrackButtonDescription(
        key="switch_breastfeeding_side",
        translation_key="switch_breastfeeding_side",
        payload={"type": "feed", "feedType": "BREAST", "action": "switch"},
    ),
    SproutTrackButtonDescription(
        key="end_breastfeeding",
        translation_key="end_breastfeeding",
        payload={"type": "feed", "feedType": "BREAST", "action": "end"},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SproutTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the buttons of every baby."""

    def factory(coordinator: SproutTrackCoordinator, baby_id: str) -> list[Entity]:
        return [SproutTrackButton(coordinator, baby_id, description) for description in BUTTONS]

    add_entities_per_baby(entry, async_add_entities, factory)


class SproutTrackButton(SproutTrackEntity, ButtonEntity):
    """Logs one fixed activity when pressed."""

    entity_description: SproutTrackButtonDescription

    def __init__(
        self,
        coordinator: SproutTrackCoordinator,
        baby_id: str,
        description: SproutTrackButtonDescription,
    ) -> None:
        """Set up the button."""
        super().__init__(coordinator, baby_id, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Log the activity."""
        payload = dict(self.entity_description.payload)
        if is_sleep_start(payload):
            payload.pop("type")
            payload.pop("action")
            await async_start_sleep(self.coordinator, self.baby_id, payload)
        else:
            await async_log_activity(self.coordinator, self.baby_id, payload)
