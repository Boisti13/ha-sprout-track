# Changelog

## v0.6.1 — 2026-11-22

### Fixed

- **Deprecation warning with Home Assistant 2026.11** ("calls
  `device_registry.async_get_device`, which is deprecated … will stop
  working in Home Assistant 2027.8.0"): renaming a baby's device now looks
  it up among the entry's own devices.

## v0.6.0 — 2026-11-22

### Added

- **Last dirty diaper** (`sensor.<baby>_last_dirty_diaper`): when the
  newest dirty or wet-and-dirty diaper was changed, with `diaper_type`
  and `caretaker`. The status only names the last diaper of any kind, so
  the diaper list (`/activities?type=diaper`, 30 days, up to 50) is
  fetched when the status shows a new diaper and every 15 minutes. A
  dirty diaper older than the 50 newest diapers is kept from before and
  saved across restarts.

## v0.5.0 — 2026-11-14

### Added

- **Dose allowed** (`binary_sensor.<baby>_<medicine>_dose_allowed`) for
  every medicine and supplement with a minimum time between doses: on
  while it may be given, and it turns on the moment the gap has passed,
  without waiting for an update; attribute `allowed_from` while off.
- **`sprout_track.log_bath`** (bath type, soap, shampoo, notes) and
  **`sprout_track.log_measurement`** (weight, height, head circumference
  or temperature with value and unit).
- **Repairs notice** when the Sprout Track server doesn't send minimum
  times between doses (released versions don't yet), explaining why
  *next dose* and *dose allowed* are missing; it goes away by itself.
- A baby renamed in Sprout Track **renames its device**; a name given in
  Home Assistant still wins, entity IDs stay.

### Changed

- **Attribute values are lowercase words now**, translated in the UI:
  `feed_type` (`bottle`, `breast`, `solids`), `bottle_type` (`formula`,
  `breast_milk`, `formula_breast`, `milk`, `other`), `diaper_type`
  (`wet`, `dirty`, `both`, `dry`), `sleep_type` (`nap`, `night_sleep`),
  `bath_type` (`full_bath`, `sponge_bath`, `wipe_down`; custom types
  unchanged) and `side` (`left`, `right`) — they were Sprout Track's
  `BOTTLE`, `Breast Milk`, `WET`, `NIGHT_SLEEP`, `LEFT` … **Templates and
  automations that compare these attributes need the new words** (or
  `| upper` on the value to accept both).

### Fixed

- **Home Assistant 2025.8 to 2025.x:** v0.4.0's wait after "too many
  requests" passed an argument those versions don't know, so such an
  update failed with an unexpected error. It now waits only where Home
  Assistant supports it.

### Under the hood

- The GitHub checks also run the tests on Home Assistant 2025.8.0, the
  oldest version `hacs.json` names (`requirements_test_min.txt`).
- The handling of per-medicine entities (adding, the 24-hour wait,
  removal) is shared by sensors and binary sensors (`entity.py`).

## v0.4.0 — 2026-11-14

### Added

- **`sprout_track.log_feed`**: logs a bottle (amount with unit, bottle
  content) or a finished breastfeed (side, duration). A bottle amount
  needs its unit; without one Sprout Track would store a bare number that
  *Fed today* can't count.
- **`sprout_track.breastfeeding`**: starts, switches, pauses, resumes or
  ends the breastfeeding timer, the same one the Sprout Track app shows.
- **Buttons** *Start breastfeeding left / right*, *Switch breastfeeding
  side* and *End breastfeeding* for every baby.

### Fixed

- **Devices and entities vanished after one odd answer.** A baby missing
  from Sprout Track's list (or a medicine missing from its medicines) was
  removed at the next update, along with its entity IDs, names and
  settings; an empty list while the server was being updated would have
  removed every baby. Now it goes unavailable and is removed only after
  24 hours; back within the day, the same entities carry on. A missing
  baby's device can still be deleted by hand at once.
- **A rarely given medicine could lose its *last given*.** The API
  returns at most 50 doses of a kind; when a month held more (a sick
  week with several doses a day), older doses of other medicines fell out
  of the list and their sensor showed *unknown*. Doses older than a full
  page are now kept from earlier updates and saved across restarts
  (`.storage/sprout_track.<entry>.doses`, deleted with the entry).
- Measurement sensors raised an error while their baby was missing.
- **Too many requests:** when Sprout Track answers HTTP 429, the next
  update now waits as long as its `Retry-After` says instead of trying
  again after the normal interval.

## v0.3.0 — 2026-11-13

### Added

- **Fed today**: how much was fed since midnight, in millilitres, added
  up from the bottle feeds logged with an amount (ounces are converted;
  breastfeeds and solids have no volume and are left out). Attribute
  `last_24_hours` has the same for the last 24 hours. The feed list is
  fetched only when the status shows a new or changed feed, and every
  15 minutes.

### Under the hood

- The GitHub checks run on every push again (Actions had to be switched
  off and on for the repository), and a newer push cancels the older run.

## v0.2.1 — 2026-11-13

### Fixed

- **Sensors of deleted medicines stayed behind** when the medicine went
  away while the integration was reloading or Home Assistant was off:
  they are now removed from the entity registry at the next update.

## v0.2.0 — 2026-11-13

### Added

- **Sensors per medicine and supplement**: *Name last given* (time of the
  last dose, with amount, unit and doses today) for every medicine and
  supplement set up in Sprout Track, and *Name next dose* (when the next
  dose is allowed) for those with a minimum time between doses. They
  appear and go away with the medicines in Sprout Track. *Next dose* needs
  a Sprout Track whose hooks API sends `doseMinTime`
  (Oak-and-Sprout/sprout-track#298).
- **Dose lists** on *Medicines today* and *Supplements today*: `doses`
  (every dose since midnight) and `latest` (each medicine given in the
  last 24 hours, with `next_dose`), for dashboards and templates.

### Under the hood

- Doses are fetched only when the status shows a new one, plus every
  15 minutes with the measurements and the medicine list; a normal update
  still costs one read plus one per baby.

## v0.1.1 — 2026-11-13

### Added

- **Icon**: the integration brings its own icon (a sprout), shown in the
  integration list, on the device page and in the Marketplace / HACS.
  Needs Home Assistant 2026.3 or newer; older versions show no icon.

## v0.1.0 — 2026-11-12

First release.

### Added

- **Config flow**: add Sprout Track from the UI with its address and an
  API key; one entry per family. Re-authentication when the key is revoked
  or expires, *Reconfigure* to change address or key, and an option for
  the update interval (default 60 s).
- **One device per baby**, added and removed on their own when babies are
  added or made inactive in Sprout Track.
- **Sensors** (24 per baby): last feed, diaper, bath, medicine, supplement
  and pump as timestamps with details as attributes; sleep state, asleep
  since, awake since, last sleep duration; today's feeds, diapers (all,
  wet, dirty), sleep, naps, baths, medicines and supplements; age; latest
  weight, height, head circumference and temperature.
- **Binary sensors** (5): sleeping, feed overdue, diaper overdue,
  breastfeeding, pumping.
- **Buttons** (6): log wet / dirty / wet-and-dirty diaper, start nap, start
  night sleep, end sleep.
- **Actions**: `sprout_track.log_diaper`, `start_sleep`, `end_sleep`,
  `log_sleep`, `log_medicine`, `log_supplement`, and `log_activity` for
  everything else the API takes. Each returns what Sprout Track created.
  The baby can be left out while there is only one.
- Starting a sleep is refused while one is running (the API itself would
  start a second one).
- Medicine and supplement doses default to the typical dose set in Sprout
  Track.
- Diagnostics download with names, IDs, dates and the key blanked.
- English and German texts.

### Under the hood

- Tests against recorded answers of Sprout Track 1.6.8; GitHub
  workflow with hassfest, the HACS check, ruff and the tests.
