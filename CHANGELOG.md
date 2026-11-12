# Changelog

## Unreleased

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
