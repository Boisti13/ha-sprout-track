"""Base entity: one device per baby."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import BabyData, SproutTrackConfigEntry, SproutTrackCoordinator


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
        known.intersection_update(current)
        new = current - known
        if not new:
            return
        known.update(new)
        async_add_entities(
            entity for baby_id in sorted(new) for entity in factory(coordinator, baby_id)
        )

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))
