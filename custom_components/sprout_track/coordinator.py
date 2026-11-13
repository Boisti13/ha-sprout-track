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
    DOSE_HISTORY,
    DOSE_KINDS,
    DOSE_LIMIT,
    FEED_HISTORY,
    FEED_LIMIT,
    MEASUREMENT_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type SproutTrackConfigEntry = ConfigEntry[SproutTrackCoordinator]


def parse_dose_gap(value: Any) -> timedelta | None:
    """Turn a minimum time between doses ("DD:HH:MM") into a timedelta.

    Sprout Track leaves it empty for medicines without one; older versions
    of its hooks API don't send it at all.
    """
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        days, hours, minutes = (int(part) for part in parts)
    except ValueError:
        return None
    gap = timedelta(days=days, hours=hours, minutes=minutes)
    return gap if gap > timedelta(0) else None


@dataclass
class BabyData:
    """Everything the entities of one baby read."""

    info: dict[str, Any]
    status: dict[str, Any] = field(default_factory=dict)
    measurements: dict[str, Any] = field(default_factory=dict)
    reference: dict[str, Any] = field(default_factory=dict)
    doses: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    feeds: list[dict[str, Any]] = field(default_factory=list)

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

    def items(self, kind: str) -> list[dict[str, Any]]:
        """Return the medicines (or supplements) set up in Sprout Track."""
        items = self.reference.get(f"{kind}s") or []
        return [item for item in items if isinstance(item, dict) and item.get("id")]

    def item(self, kind: str, item_id: str) -> dict[str, Any] | None:
        """Return one medicine (or supplement) by its ID."""
        return next((i for i in self.items(kind) if str(i["id"]) == item_id), None)

    def doses_of(self, kind: str, name: str | None = None) -> list[dict[str, Any]]:
        """Return the recent doses of a kind, newest first; optionally of one name."""
        doses = sorted(self.doses.get(kind) or [], key=lambda d: d["time"], reverse=True)
        if name is None:
            return doses
        folded = name.casefold()
        return [d for d in doses if dose_name(d, kind).casefold() == folded]

    def fed_since(self, since: datetime) -> float:
        """Return how much was fed since a time, in millilitres.

        Counts the feeds logged with an amount in ml or oz (bottle feeds);
        breastfeeds (a duration) and solids (grams) are left out.
        """
        total = 0.0
        for feed in self.feeds:
            details = feed.get("details") or {}
            amount = details.get("amount")
            factor = FEED_UNITS.get(str(details.get("unitAbbr") or "").upper())
            when = dt_util.parse_datetime(str(feed.get("time")))
            if factor and isinstance(amount, int | float) and when and when >= since:
                total += amount * factor
        return round(total, 1)


# Millilitres per unit a feed amount can be logged in
FEED_UNITS = {"ML": 1.0, "OZ": 29.5735}


def dose_name(dose: dict[str, Any], kind: str) -> str:
    """Return the name of the medicine (or supplement) a dose was of."""
    return str((dose.get("details") or {}).get(f"{kind}Name") or "")


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
        self._references: dict[str, dict[str, Any]] = {}
        self._doses: dict[tuple[str, str], list[dict[str, Any]]] = {}
        # The last dose time each fetch saw in the status, per baby and kind
        self._dose_marks: dict[tuple[str, str], Any] = {}
        self._feeds: dict[str, list[dict[str, Any]]] = {}
        # The last feed each fetch saw in the status (ID and time), per baby
        self._feed_marks: dict[str, Any] = {}

    async def _async_update_data(self) -> dict[str, BabyData]:
        try:
            babies, _ = await self.client.get_babies()
            ids = [str(baby["id"]) for baby in babies]
            timezone = self.hass.config.time_zone
            statuses = await asyncio.gather(
                *(self.client.get_status(baby_id, timezone) for baby_id in ids)
            )

            now = dt_util.utcnow()
            slow_due = (
                self._measurements_at is None
                or now - self._measurements_at >= MEASUREMENT_INTERVAL
                or any(baby_id not in self._measurements for baby_id in ids)
            )
            if slow_due:
                results = await asyncio.gather(
                    *(self.client.get_measurements(baby_id) for baby_id in ids),
                    *(self.client.get_reference(baby_id) for baby_id in ids),
                )
                self._measurements = dict(zip(ids, results[: len(ids)], strict=True))
                self._references = dict(zip(ids, results[len(ids) :], strict=True))
                self._measurements_at = now

            await self._update_doses(ids, statuses, now, refresh=slow_due)
            await self._update_feeds(ids, statuses, now, refresh=slow_due)
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
                reference=self._references.get(baby_id, {}),
                doses={kind: self._doses.get((baby_id, kind), []) for kind in DOSE_KINDS},
                feeds=self._feeds.get(baby_id, []),
            )
            for baby_id, baby, status in zip(ids, babies, statuses, strict=True)
        }
        self._remove_stale_devices(set(data))
        return data

    async def _update_doses(
        self,
        ids: list[str],
        statuses: list[dict[str, Any]],
        now: datetime,
        *,
        refresh: bool,
    ) -> None:
        """Fetch a kind's doses when the status shows a new one (or on refresh)."""
        since = (now - DOSE_HISTORY).isoformat().replace("+00:00", "Z")
        wanted: list[tuple[str, str, Any]] = []
        for baby_id, status in zip(ids, statuses, strict=True):
            last = status.get("lastActivities") or {}
            for kind in DOSE_KINDS:
                mark = (last.get(kind) or {}).get("time")
                key = (baby_id, kind)
                if refresh or key not in self._doses or self._dose_marks.get(key) != mark:
                    wanted.append((baby_id, kind, mark))
        if wanted:
            results = await asyncio.gather(
                *(
                    self.client.get_activities(baby_id, kind, since, DOSE_LIMIT)
                    for baby_id, kind, _ in wanted
                )
            )
            for (baby_id, kind, mark), doses in zip(wanted, results, strict=True):
                self._doses[(baby_id, kind)] = doses
                self._dose_marks[(baby_id, kind)] = mark
        for key in [key for key in self._doses if key[0] not in ids]:
            del self._doses[key]
            self._dose_marks.pop(key, None)

    async def _update_feeds(
        self,
        ids: list[str],
        statuses: list[dict[str, Any]],
        now: datetime,
        *,
        refresh: bool,
    ) -> None:
        """Fetch the last day's feeds when the status shows a new one (or on refresh)."""
        since = (now - FEED_HISTORY).isoformat().replace("+00:00", "Z")
        wanted: list[tuple[str, Any]] = []
        for baby_id, status in zip(ids, statuses, strict=True):
            feed = (status.get("lastActivities") or {}).get("feed") or {}
            mark = (feed.get("id"), feed.get("time"), feed.get("amount"))
            if refresh or baby_id not in self._feeds or self._feed_marks.get(baby_id) != mark:
                wanted.append((baby_id, mark))
        if wanted:
            results = await asyncio.gather(
                *(
                    self.client.get_activities(baby_id, "feed", since, FEED_LIMIT)
                    for baby_id, _ in wanted
                )
            )
            for (baby_id, mark), feeds in zip(wanted, results, strict=True):
                self._feeds[baby_id] = feeds
                self._feed_marks[baby_id] = mark
        for baby_id in [baby_id for baby_id in self._feeds if baby_id not in ids]:
            del self._feeds[baby_id]
            self._feed_marks.pop(baby_id, None)

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
