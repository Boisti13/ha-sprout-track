"""Polls Sprout Track and keeps one data set per baby."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import inspect
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SproutTrackApiError,
    SproutTrackAuthError,
    SproutTrackClient,
    SproutTrackError,
    SproutTrackRateLimitError,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DIAPER_HISTORY,
    DIAPER_LIMIT,
    DIRTY_DIAPER_TYPES,
    DOMAIN,
    DOSE_GAP_ISSUE_URL,
    DOSE_HISTORY,
    DOSE_KINDS,
    DOSE_LIMIT,
    DOSE_STORE_DELAY,
    DOSE_STORE_VERSION,
    FEED_HISTORY,
    FEED_LIMIT,
    MEASUREMENT_INTERVAL,
    REMOVE_AFTER,
)

_LOGGER = logging.getLogger(__name__)

type SproutTrackConfigEntry = ConfigEntry[SproutTrackCoordinator]

# UpdateFailed takes retry_after only in newer Home Assistant versions
_UPDATE_FAILED_RETRY_AFTER = "retry_after" in inspect.signature(UpdateFailed.__init__).parameters


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
    dirty_diaper: dict[str, Any] = field(default_factory=dict)

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

    def last_dose(self, kind: str, item_id: str) -> dict[str, Any] | None:
        """Return the latest dose of one medicine (or supplement), if any."""
        item = self.item(kind, item_id)
        if item is None:
            return None
        doses = self.doses_of(kind, str(item.get("name") or ""))
        return doses[0] if doses else None

    def next_dose(self, kind: str, dose: dict[str, Any]) -> datetime | None:
        """Return when the next dose of a dose's medicine is allowed, if it has a gap."""
        name = dose_name(dose, kind).casefold()
        item = next(
            (i for i in self.items(kind) if str(i.get("name", "")).casefold() == name), None
        )
        gap = parse_dose_gap(item.get("doseMinTime")) if item else None
        given = dt_util.parse_datetime(str(dose.get("time")))
        return given + gap if gap and given else None

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


def merge_doses(
    cached: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
    since: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    """Combine a fresh page of doses with the ones known from before.

    A page shorter than the limit holds every dose since ``since``: it
    replaces what was known (doses deleted in Sprout Track go away too). A
    full page was cut off; the doses older than its oldest one are then
    taken from before, so a medicine given rarely keeps its last dose while
    another one is given often.
    """
    if len(fresh) < limit:
        return fresh
    times = [t for dose in fresh if (t := dt_util.parse_datetime(str(dose.get("time"))))]
    if not times:
        return fresh
    oldest = min(times)
    fresh_ids = {dose.get("id") for dose in fresh}
    older = [
        dose
        for dose in cached
        if dose.get("id") not in fresh_ids
        and (t := dt_util.parse_datetime(str(dose.get("time"))))
        and since <= t <= oldest
    ]
    return [*fresh, *older]


def newest_dirty_diaper(
    cached: dict[str, Any] | None,
    fresh: list[dict[str, Any]],
    since: datetime,
    limit: int,
) -> dict[str, Any] | None:
    """Return the newest dirty (or wet and dirty) diaper.

    Taken from a fresh page of diapers; if it has none, the one known from
    before is kept as long as it is older than everything the page covers
    (since ``since``, or since the page's oldest diaper when it is full).
    A known one inside that range was deleted or changed in Sprout Track.
    """
    dirty = [
        (t, diaper)
        for diaper in fresh
        if str((diaper.get("details") or {}).get("type") or "").upper() in DIRTY_DIAPER_TYPES
        and (t := dt_util.parse_datetime(str(diaper.get("time"))))
    ]
    if dirty:
        return max(dirty, key=lambda pair: pair[0])[1]
    if not cached:
        return None
    covered_from = since
    if len(fresh) >= limit:
        times = [t for d in fresh if (t := dt_util.parse_datetime(str(d.get("time"))))]
        if times:
            covered_from = min(times)
    known = dt_util.parse_datetime(str(cached.get("time")))
    return cached if known and known < covered_from else None


def dose_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the file the doses of a config entry are saved in."""
    return Store(hass, DOSE_STORE_VERSION, f"{DOMAIN}.{entry_id}.doses")


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
        self._dirty_diapers: dict[str, dict[str, Any]] = {}
        # The last diaper each fetch saw in the status, per baby
        self._diaper_marks: dict[str, Any] = {}
        # Babies Sprout Track stopped listing, and since when (see REMOVE_AFTER)
        self.missing_since: dict[str, datetime] = {}
        self._store = dose_store(hass, entry.entry_id)

    async def _async_setup(self) -> None:
        """Load the doses saved before the last restart."""
        saved = await self._store.async_load() or {}
        for key, doses in (saved.get("doses") or {}).items():
            baby_id, _, kind = key.partition("|")
            if kind in DOSE_KINDS and isinstance(doses, list):
                self._doses[(baby_id, kind)] = [d for d in doses if isinstance(d, dict)]
        for baby_id, diaper in (saved.get("dirty_diapers") or {}).items():
            if isinstance(diaper, dict):
                self._dirty_diapers[baby_id] = diaper

    async def async_shutdown(self) -> None:
        """Write the doses now (on unload), not after the save delay."""
        await super().async_shutdown()
        if self._doses or self._dirty_diapers:
            await self._store.async_save(self._dose_store_data())

    def _dose_store_data(self) -> dict[str, Any]:
        return {
            "doses": {f"{baby_id}|{kind}": doses for (baby_id, kind), doses in self._doses.items()},
            "dirty_diapers": self._dirty_diapers,
        }

    async def _async_update_data(self) -> dict[str, BabyData]:
        try:
            babies, _ = await self.client.get_babies()
            ids = [str(baby["id"]) for baby in babies]
            self._track_missing(set(ids))
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
                self._check_dose_gap_support()

            await self._update_doses(ids, statuses, now, refresh=slow_due)
            await self._update_feeds(ids, statuses, now, refresh=slow_due)
            await self._update_dirty_diapers(ids, statuses, now, refresh=slow_due)
        except SproutTrackAuthError as err:
            raise ConfigEntryAuthFailed(err.message) from err
        except SproutTrackRateLimitError as err:
            # Wait as long as the server asks before the next update (Home
            # Assistant then returns to the normal interval); older versions
            # of Home Assistant can't, and just try again at the next one
            if _UPDATE_FAILED_RETRY_AFTER:
                raise UpdateFailed(
                    f"{err.code}: {err.message}", retry_after=err.retry_after
                ) from err
            raise UpdateFailed(f"{err.code}: {err.message}") from err
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
                dirty_diaper=self._dirty_diapers.get(baby_id, {}),
            )
            for baby_id, baby, status in zip(ids, babies, statuses, strict=True)
        }
        self._sync_device_names(data)
        return data

    def _sync_device_names(self, data: dict[str, BabyData]) -> None:
        """Rename a baby's device when the baby is renamed in Sprout Track.

        A name given in Home Assistant (name_by_user) still wins; entity IDs
        stay as they are.
        """
        registry = dr.async_get(self.hass)
        # The entry's own devices (async_get_device by identifier is
        # deprecated from Home Assistant 2026.11; its replacement doesn't
        # exist in older versions)
        devices = {
            identifier[1]: device
            for device in dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)
            for identifier in device.identifiers
            if identifier[0] == DOMAIN
        }
        for baby_id, baby in data.items():
            device = devices.get(baby_id)
            if device is not None and device.name != baby.name:
                registry.async_update_device(device.id, name=baby.name)

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
            since_time = now - DOSE_HISTORY
            for (baby_id, kind, mark), doses in zip(wanted, results, strict=True):
                key = (baby_id, kind)
                self._doses[key] = merge_doses(
                    self._doses.get(key, []), doses, since_time, DOSE_LIMIT
                )
                self._dose_marks[key] = mark
        # A baby that is only missing for now keeps its doses
        gone = [
            key for key in self._doses if key[0] not in ids and key[0] not in self.missing_since
        ]
        for key in gone:
            del self._doses[key]
            self._dose_marks.pop(key, None)
        if wanted or gone:
            self._store.async_delay_save(self._dose_store_data, DOSE_STORE_DELAY)

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

    async def _update_dirty_diapers(
        self,
        ids: list[str],
        statuses: list[dict[str, Any]],
        now: datetime,
        *,
        refresh: bool,
    ) -> None:
        """Find the newest dirty diaper when the status shows a new diaper (or on refresh)."""
        since_time = now - DIAPER_HISTORY
        since = since_time.isoformat().replace("+00:00", "Z")
        wanted: list[tuple[str, Any]] = []
        for baby_id, status in zip(ids, statuses, strict=True):
            diaper = (status.get("lastActivities") or {}).get("diaper") or {}
            mark = (diaper.get("id"), diaper.get("time"), diaper.get("type"))
            if refresh or baby_id not in self._diaper_marks or self._diaper_marks[baby_id] != mark:
                wanted.append((baby_id, mark))
        changed = False
        if wanted:
            results = await asyncio.gather(
                *(
                    self.client.get_activities(baby_id, "diaper", since, DIAPER_LIMIT)
                    for baby_id, _ in wanted
                )
            )
            for (baby_id, mark), diapers in zip(wanted, results, strict=True):
                dirty = newest_dirty_diaper(
                    self._dirty_diapers.get(baby_id), diapers, since_time, DIAPER_LIMIT
                )
                if dirty != self._dirty_diapers.get(baby_id):
                    changed = True
                if dirty:
                    self._dirty_diapers[baby_id] = dirty
                else:
                    self._dirty_diapers.pop(baby_id, None)
                self._diaper_marks[baby_id] = mark
        # A baby that is only missing for now keeps its last dirty diaper
        for baby_id in [
            b for b in self._dirty_diapers if b not in ids and b not in self.missing_since
        ]:
            del self._dirty_diapers[baby_id]
            self._diaper_marks.pop(baby_id, None)
            changed = True
        if changed:
            self._store.async_delay_save(self._dose_store_data, DOSE_STORE_DELAY)

    def _check_dose_gap_support(self) -> None:
        """Explain in Repairs why *next dose* is missing, when the server can't send it.

        Sprout Track's released hooks API leaves out each medicine's minimum
        time between doses (Oak-and-Sprout/sprout-track#298); a server that
        sends it has the field on every medicine, empty or not.
        """
        items = [
            item
            for reference in self._references.values()
            for kind in DOSE_KINDS
            for item in reference.get(f"{kind}s") or []
            if isinstance(item, dict)
        ]
        issue_id = f"no_dose_min_time_{self.config_entry.entry_id}"
        if items and not any("doseMinTime" in item for item in items):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                learn_more_url=DOSE_GAP_ISSUE_URL,
                translation_key="no_dose_min_time",
                translation_placeholders={"title": self.config_entry.title},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def invalidate_measurements(self) -> None:
        """Fetch the measurements again on the next update."""
        self._measurements_at = None

    def forget(self, baby_id: str) -> None:
        """Stop waiting for a baby whose device was deleted by hand."""
        self.missing_since.pop(baby_id, None)

    def _track_missing(self, current: set[str]) -> None:
        """Remove the devices of babies Sprout Track hasn't listed for a while.

        A baby that is missing from one answer only goes unavailable; its
        device (and with it the entities, their IDs and settings) is removed
        once it has been missing for REMOVE_AFTER.
        """
        now = dt_util.utcnow()
        registry = dr.async_get(self.hass)
        missing: dict[str, datetime] = {}
        for device in dr.async_entries_for_config_entry(registry, self.config_entry.entry_id):
            baby_ids = {
                identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN
            }
            if not baby_ids or baby_ids & current:
                continue
            since = min(self.missing_since.get(baby_id, now) for baby_id in baby_ids)
            if now - since >= REMOVE_AFTER:
                _LOGGER.info("Removing %s: not listed by Sprout Track since %s", device.name, since)
                registry.async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )
                continue
            if since == now:
                _LOGGER.info("%s is no longer listed by Sprout Track", device.name)
            missing.update(dict.fromkeys(baby_ids, since))
        self.missing_since = missing
