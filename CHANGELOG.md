# Changelog

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
