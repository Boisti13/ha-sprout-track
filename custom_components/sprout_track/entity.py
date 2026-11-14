"""Base entity: one device per baby."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, DOSE_KINDS, MANUFACTURER, REMOVE_AFTER
from .coordinator import BabyData, SproutTrackConfigEntry, SproutTrackCoordinator

# baby ID, kind (medicine or supplement), medicine ID, which entity of it
type DoseKey = tuple[str, str, str, str]


class SproutTrackEntity(CoordinatorEntity[SproutTrackCoordinator]):
    """An entity that belongs to one baby."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SproutTrackCoordinator, baby_id: str, key: str) -> None:
        """Set the unique ID and attach the entity to the baby's device."""
        super().__init__(coordinator)
        self.baby_id = baby_id
        self._attr_unique_id = f"{baby_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, baby_id)},
            name=coordinator.data[baby_id].name,
            manufacturer=MANUFACTURER,
            model="Baby",
            configuration_url=coordinator.client.base_url,
        )

    @property
    def available(self) -> bool:
        """Return whether the last update worked and still lists this baby."""
        return super().available and self.baby_id in self.coordinator.data

    @property
    def baby(self) -> BabyData:
        """Return this baby's data."""
        return self.coordinator.data[self.baby_id]


def add_entities_per_baby(
    entry: SproutTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[SproutTrackCoordinator, str], list[Entity]],
) -> None:
    """Add a platform's entities for every baby, now and when one is added."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _check() -> None:
        current = set(coordinator.data)
        # A baby that is only missing for now keeps its entities (unavailable)
        known.intersection_update(current | set(coordinator.missing_since))
        new = current - known
        if not new:
            return
        known.update(new)
        async_add_entities(
            entity for baby_id in sorted(new) for entity in factory(coordinator, baby_id)
        )

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))


def dose_unique_id(baby_id: str, kind: str, item_id: str, which: str) -> str:
    """Return the unique ID of one entity of a medicine (or supplement)."""
    return f"{baby_id}_{kind}_{item_id}_{which}"


class SproutTrackDoseEntity(SproutTrackEntity):
    """An entity of one medicine (or supplement) of a baby."""

    def __init__(self, coordinator: SproutTrackCoordinator, key: DoseKey, *, name: str) -> None:
        """Set up the entity; the medicine's name goes into the entity name."""
        baby_id, kind, item_id, which = key
        super().__init__(coordinator, baby_id, f"{kind}_{item_id}_{which}")
        self.dose_key = key
        self._kind = kind
        self._item_id = item_id
        self._attr_translation_key = f"{kind}_{which}"
        self._attr_translation_placeholders = {"name": name}

    @property
    def _item(self) -> dict[str, Any] | None:
        return self.baby.item(self._kind, self._item_id)

    @property
    def _last_dose(self) -> dict[str, Any] | None:
        return self.baby.last_dose(self._kind, self._item_id)

    @property
    def available(self) -> bool:
        """Return whether Sprout Track still lists this medicine."""
        return super().available and self._item is not None


def add_dose_entities(
    hass: HomeAssistant,
    entry: SproutTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    *,
    domain: str,
    whiches: Callable[[dict[str, Any]], tuple[str, ...]],
    factory: Callable[[SproutTrackCoordinator, DoseKey, str], SproutTrackDoseEntity],
    all_whiches: tuple[str, ...],
) -> None:
    """Keep a platform's entities per medicine and supplement, as they come and go.

    ``whiches`` says which entities a medicine gets (``all_whiches`` lists
    every one the platform can make, to find its entities in the registry).
    A medicine Sprout Track stops listing keeps its entities (unavailable)
    for REMOVE_AFTER; those of a baby that is missing for now stay.
    """
    coordinator = entry.runtime_data
    known: dict[DoseKey, SproutTrackDoseEntity] = {}
    # Unique IDs of entities whose medicine is no longer listed, and since when
    gone_since: dict[str, datetime] = {}
    suffixes = tuple(f"_{which}" for which in all_whiches)

    @callback
    def _expire(gone: set[str], registered: dict[str, str]) -> None:
        """Remove the entities that have been gone for REMOVE_AFTER."""
        now = dt_util.utcnow()
        for unique_id in gone_since.keys() - gone:
            del gone_since[unique_id]
        for unique_id in gone:
            if now - gone_since.setdefault(unique_id, now) < REMOVE_AFTER:
                continue
            del gone_since[unique_id]
            key = next((k for k in known if dose_unique_id(*k) == unique_id), None)
            if key is not None:
                entity = known.pop(key)
                if entity.hass is not None and unique_id not in registered:
                    hass.async_create_task(entity.async_remove())
            if (entity_id := registered.get(unique_id)) is not None:
                er.async_get(hass).async_remove(entity_id)

    def waiting(unique_id: str) -> bool:
        """Return whether the entity's baby is only missing for now."""
        return any(unique_id.startswith(f"{b}_") for b in coordinator.missing_since)

    @callback
    def _check() -> None:
        wanted: dict[DoseKey, dict[str, Any]] = {}
        for baby_id, baby in coordinator.data.items():
            for kind in DOSE_KINDS:
                for item in baby.items(kind):
                    for which in whiches(item):
                        wanted[(baby_id, kind, str(item["id"]), which)] = item
        wanted_ids = {dose_unique_id(*key) for key in wanted}

        registry = er.async_get(hass)
        gone = {
            unique_id
            for key in known.keys() - wanted.keys()
            if not waiting(unique_id := dose_unique_id(*key))
        }
        # Also entities of medicines that went away while the integration was
        # not running (or reloading): those are only in the registry.
        registered = {
            reg_entry.unique_id: reg_entry.entity_id
            for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
            if reg_entry.domain == domain and reg_entry.unique_id.endswith(suffixes)
        }
        gone.update(
            unique_id
            for unique_id in registered
            if unique_id not in wanted_ids and not waiting(unique_id)
        )
        _expire(gone, registered)

        new = [
            factory(coordinator, key, str(wanted[key].get("name") or ""))
            for key in sorted(wanted.keys() - known.keys())
        ]
        if new:
            known.update({entity.dose_key: entity for entity in new})
            async_add_entities(new)

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))
