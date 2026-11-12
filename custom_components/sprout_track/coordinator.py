"""Polls Sprout Track and keeps one data set per baby."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SproutTrackApiError,
    SproutTrackAuthError,
    SproutTrackClient,
    SproutTrackError,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MEASUREMENT_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type SproutTrackConfigEntry = ConfigEntry[SproutTrackCoordinator]


@dataclass
class BabyData:
    """Everything the entities of one baby read."""

    info: dict[str, Any]
    status: dict[str, Any] = field(default_factory=dict)
    measurements: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Return the baby's first name (or full name, or ID as a last resort)."""
        return self.info.get("firstName") or self.info.get("lastName") or str(self.info.get("id"))

    def last(self, kind: str) -> dict[str, Any]:
        """Return the last activity of a kind, or an empty dict."""
        return (self.status.get("lastActivities") or {}).get(kind) or {}

    @property
    def is_asleep(self) -> bool:
        """Return whether a sleep is running."""
        return bool(self.last("sleep").get("isActive"))


class SproutTrackCoordinator(DataUpdateCoordinator[dict[str, BabyData]]):
    """Fetches the babies and their status in one go."""

    config_entry: SproutTrackConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SproutTrackConfigEntry,
        client: SproutTrackClient,
    ) -> None:
        """Set up the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self._measurements: dict[str, dict[str, Any]] = {}
        self._measurements_at: datetime | None = None

    async def _async_update_data(self) -> dict[str, BabyData]:
        try:
            babies, _ = await self.client.get_babies()
            ids = [str(baby["id"]) for baby in babies]
            timezone = self.hass.config.time_zone
            statuses = await asyncio.gather(
                *(self.client.get_status(baby_id, timezone) for baby_id in ids)
            )

            now = dt_util.utcnow()
            if (
                self._measurements_at is None
                or now - self._measurements_at >= MEASUREMENT_INTERVAL
                or any(baby_id not in self._measurements for baby_id in ids)
            ):
                results = await asyncio.gather(
                    *(self.client.get_measurements(baby_id) for baby_id in ids)
                )
                self._measurements = dict(zip(ids, results, strict=True))
                self._measurements_at = now
        except SproutTrackAuthError as err:
            raise ConfigEntryAuthFailed(err.message) from err
        except SproutTrackApiError as err:
            if err.code == "INSUFFICIENT_SCOPE":
                raise ConfigEntryAuthFailed(err.message) from err
            raise UpdateFailed(f"{err.code}: {err.message}") from err
        except SproutTrackError as err:
            raise UpdateFailed(str(err)) from err

        data = {
            baby_id: BabyData(
                info=baby,
                status=status,
                measurements=self._measurements.get(baby_id, {}),
            )
            for baby_id, baby, status in zip(ids, babies, statuses, strict=True)
        }
        self._remove_stale_devices(set(data))
        return data

    def invalidate_measurements(self) -> None:
        """Fetch the measurements again on the next update."""
        self._measurements_at = None

    def _remove_stale_devices(self, current: set[str]) -> None:
        """Remove the devices of babies Sprout Track no longer lists."""
        registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(registry, self.config_entry.entry_id):
            baby_ids = {
                identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN
            }
            if baby_ids and not baby_ids & current:
                _LOGGER.info("Removing %s: no longer listed by Sprout Track", device.name)
                registry.async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )
