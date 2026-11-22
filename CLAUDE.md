# Working on this repository

Home Assistant custom integration (HACS) for
[Sprout Track](https://github.com/Oak-and-Sprout/sprout-track), a
self-hosted baby tracker. It talks to Sprout Track's hooks API
(`/api/hooks/v1`, documented upstream in
`documentation/Admin-Documentation/webhook-api.md`). Public repository,
MIT: other Sprout Track users install it, so nothing in it may assume the
owner's setup.

- **Branches:** `dev` for work, `main` for releases. Push to `dev`; merge
  to `main` and release only when the owner asks.
- **Keep the domain `sprout_track`** and the unique IDs
  (`<baby id>_<key>`): changing either makes Home Assistant create new
  devices and entities for every user. Renaming an entity's `key` is the
  same thing; change `translation_key` and the texts instead.
- **Never real data.** No names, IDs, keys, addresses or screenshots from
  the owner's own Sprout Track (real family data). Fixtures in
  `tests/fixtures/` are recorded from the test instance and anonymised
  (`baby-1`, `family-1`, Emma/Mia Sample). README screenshots, if any,
  come from the test instance.
- **Layout:** `api.py` is the HTTP client (no Home Assistant imports);
  `coordinator.py` polls `/babies`, each baby's `/status` every interval,
  `/measurements/latest` and `/reference` every 15 min, and the medicine
  and supplement doses (`/activities?type=…`, 30 days) when the status
  shows a new one or with the 15-min fetch, the same for the diaper list
  (newest dirty diaper, `newest_dirty_diaper()`), into one `BabyData` per baby
  (a full page of 50 doses is merged with the older doses known from
  before, saved in `.storage/sprout_track.<entry>.doses`);
  `entity.py` has the base entity and `add_entities_per_baby()` (babies
  come and go without a reload); the per-medicine sensors are added and
  removed by their own listener in `sensor.py` (`_add_dose_sensors`); `sensor.py`, `binary_sensor.py`,
  `button.py` are description tables; `services.py` holds the actions and
  the two helpers the buttons share (`async_log_activity`,
  `async_start_sleep`).
- **Nothing is removed at once.** A baby or medicine missing from an
  answer goes unavailable; device and entities are removed after
  `REMOVE_AFTER` (24 h, `coordinator.missing_since` / `gone_since` in
  `sensor.py`). Code that runs while an entity is unavailable (unit,
  device class) must not assume its baby is in `coordinator.data`.
- **A new entity or action** needs: the description or handler, texts in
  **both** `translations/en.json` and `de.json`, an icon in `icons.json`,
  for actions the fields in `services.yaml`, a test, a README table row
  and a CHANGELOG line. `test_translations_cover_everything` fails when
  texts, icons or fields are missing.
- **Icon:** `custom_components/sprout_track/brand/icon.png` (256 px) and
  `icon@2x.png` (512 px), rendered from `assets/icon.svg` with ImageMagick
  (`convert -background none -density 192 assets/icon.svg -resize 256x256
  PNG32:…/icon.png`, density 384 and 512x512 for `@2x`). An original
  drawing: never Sprout Track's own logo. ImageMagick's SVG renderer drops
  stroked curves without fill; draw shapes as filled paths.
- **Attribute values with a fixed set** (API words like `WET`,
  `Breast Milk`) go through `attribute_value()` into lowercase options
  (`ATTRIBUTE_VALUES` in `const.py`) and get `state_attributes` texts in
  both translations; the translation test checks them.
- **Timestamps, not "minutes ago".** The API's `minutesAgo` changes every
  minute; sensors expose the time instead and Home Assistant shows
  "5 minutes ago" itself. Don't add counters that tick.
- **API limits:** 60 reads and 30 writes per minute per key. One poll is
  1 + number of babies requests; keep it that way (measurements and
  reference data are fetched rarely or on demand).
- **Tests:** venv with Python 3.14 outside the repo
  (`uv venv --python 3.14 ~/.venvs/sprout-track-ha`,
  `uv pip install -r requirements_test.txt`), then
  `~/.venvs/sprout-track-ha/bin/python -m pytest -q` and
  `ruff check . && ruff format --check .`. All must pass before a push.
  Also on the oldest supported Home Assistant (2025.8.0, the minimum in
  `hacs.json`): venv `~/.venvs/sprout-track-ha-min` (Python 3.13) from
  `requirements_test_min.txt`, same `pytest -q`; CI runs it too. Newer
  Home Assistant APIs need a fallback there (see `retry_after` in
  `coordinator.py`) or a raised minimum in both files.
- **Trying it for real:** the owner's test Sprout Track (no real data; where it
  runs is in the owner's private notes) plus a throwaway Home Assistant from the
  same venv (`hass -c <scratch dir>` with `custom_components/sprout_track`
  linked in). Not against the live Sprout Track, and installing it in the
  owner's Home Assistant is the owner's call.
- **Release:** bump `version` in `manifest.json`, move *Unreleased* in
  `CHANGELOG.md` to `## vX.Y.Z — date`, merge to `main`, tag `vX.Y.Z`,
  `gh release create vX.Y.Z --verify-tag --notes-file <that section>`.
  HACS installs from releases once one exists.
- README and CHANGELOG follow the owner's house style; update both with
  every user-visible change.
