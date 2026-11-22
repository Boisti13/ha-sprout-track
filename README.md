# Sprout Track for Home Assistant

[![Release](https://img.shields.io/github/v/release/Boisti13/ha-sprout-track?include_prereleases)](https://github.com/Boisti13/ha-sprout-track/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Validate](https://github.com/Boisti13/ha-sprout-track/actions/workflows/validate.yml/badge.svg)](https://github.com/Boisti13/ha-sprout-track/actions/workflows/validate.yml)
[![HACS: custom](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.8%2B-18BCF2)

A Home Assistant integration for
[Sprout Track](https://github.com/Oak-and-Sprout/sprout-track), the
self-hosted baby tracker. It shows when your baby last ate, slept and got
a fresh diaper, and lets Home Assistant log things for you: a button on the
changing table, a nap that starts when the car does, the vitamin drops
ticked off from a dashboard.

It uses Sprout Track's own API (the *hooks* API), so there is no YAML to
write and nothing to install on the Sprout Track side. This is a community
project and not part of Sprout Track.

## Highlights

- **Set up in the UI**: address and API key, done. A new key can be
  entered later without removing anything.
- **One device per baby**, with every baby of the family. Babies added in
  Sprout Track appear on their own; a renamed baby renames its device.
- **26 sensors per baby**: last feed, diaper, dirty diaper, sleep, bath, medicine and
  supplement; today's counts and millilitres fed; age; latest weight, height, head
  circumference and temperature.
- **Per medicine and supplement**: when it was last given and, where
  Sprout Track has a minimum time between doses, when the next dose is
  allowed and a *dose allowed* binary sensor that turns on the moment
  it is. They follow the medicines set up in Sprout Track.
- **Times, not counters**: "last feed" is a point in time, which Home
  Assistant shows as "2 hours ago" without the value changing every minute.
- **Warnings as binary sensors**: feed overdue and diaper overdue, using
  the warning times set per baby in Sprout Track.
- **Buttons** for the usual one-tap entries: diapers, start nap, start
  night sleep, end sleep, and the breastfeeding timer (start left or
  right, switch side, end).
- **Actions** for feeds, the breastfeeding timer, diapers, sleep, medicine,
  supplements, baths and measurements with all their details, plus one
  that passes any activity to the API.
- **Careful with sleep**: a second sleep is never started while one runs.
- **Local**: talks only to your Sprout Track server.
- English and German.

## Installation

You need Sprout Track 1.6 or newer (the version with *Settings → Admin →
Integrations*) and [HACS](https://hacs.xyz).

| Step | Where | What |
|---|---|---|
| 1 | Sprout Track → Settings → Admin → Integrations | Create an API key with the scopes **read** and **write** (read alone gives sensors without buttons and actions). Copy it: it is shown once. |
| 2 | HACS → ⋮ → Custom repositories | Add `https://github.com/Boisti13/ha-sprout-track`, type *Integration*. |
| 3 | HACS | Search *Sprout Track*, download, restart Home Assistant. |
| 4 | Settings → Devices & services → Add integration | Pick *Sprout Track*, enter the address (for example `http://192.168.1.50:3000`) and the key. |

Without HACS: copy `custom_components/sprout_track` into your
`config/custom_components` folder and restart.

**Address:** Sprout Track accepts plain `http://` only for private IP
addresses and `localhost`. With a host name (`http://sprout.lan:3000`) it
answers "HTTPS required": use the IP address, or `https://` through a
reverse proxy. For a self-signed certificate, untick *Check the server's
certificate*.

**One entry per family.** A key limited to one baby works too and brings
just that baby.

## Entities

Entity IDs start with the baby's first name, e.g. `sensor.emma_last_feed`.

### Sensors

| Sensor | Value | Attributes |
|---|---|---|
| Last feed | time | `feed_type`, `amount`, `unit`, `bottle_type`, `caretaker` |
| Last diaper | time | `diaper_type`, `caretaker` |
| Last dirty diaper | time of the newest dirty (or wet-and-dirty) diaper in the last 30 days | `diaper_type`, `caretaker` |
| Sleep state | Awake / Nap / Night sleep | |
| Asleep since | time, while asleep | |
| Awake since | time, while awake | |
| Last sleep duration | minutes | `sleep_type` |
| Last bath | time | `bath_type` |
| Last medicine | time | `medicine` |
| Last supplement | time | `supplement` |
| Last pump | time | `duration` |
| Feeds today | count | |
| Fed today | millilitres from bottle feeds since midnight (ounces converted; breastfeeds and solids left out) | `last_24_hours` |
| Diapers today | count | |
| Wet diapers today | count (wet, and wet-and-dirty) | |
| Dirty diapers today | count (dirty, and wet-and-dirty) | |
| Sleep today | hours | |
| Naps today | count | |
| Baths today | count | |
| Medicines / Supplements today | count | `doses`, `latest` (below) |
| Age | days | `birth_date`, `age_text` |
| Weight, Height, Head circumference, Temperature | latest measurement, in the unit it was logged in | `date` |

**Per medicine and supplement** set up in Sprout Track, e.g.
`sensor.emma_paracetamol_last_given`:

| Sensor | Value | Attributes |
|---|---|---|
| *Name* last given | time of the last dose (last 30 days) | `amount`, `unit`, `doses_today`, `minimum_gap_minutes` |
| *Name* next dose | time the next dose is allowed (in the past once it is) | same |

*Next dose* (and the binary sensor *dose allowed*, below) exists only for
medicines with a minimum time between doses (see *Good to know*). A medicine deleted in Sprout Track goes unavailable
and loses its sensors a day later.

*Medicines today* and *Supplements today* carry two lists for dashboards
and templates, newest first, times in local time:

- `doses`: every dose since midnight — `time`, `name`, `amount`, `unit`
- `latest`: each medicine given in the last 24 hours once, its latest
  dose, plus `next_dose` (or `null`)

"Today" starts at midnight in Home Assistant's time zone. Sleep that began
before midnight counts from midnight on. A left-and-right nursing session
is one feed.

### Binary sensors

| Binary sensor | On when | Attributes |
|---|---|---|
| Sleeping | a sleep is running | `sleep_type` |
| Feed overdue | the last feed is longer ago than the baby's feed warning time | `warning_after` |
| Diaper overdue | same for diapers | `warning_after` |
| Breastfeeding | a breastfeeding timer is running | `side`, `paused`, `started` |
| Pumping | a pump timer is running | |
| *Name* dose allowed | per medicine with a minimum gap: the gap since its last dose has passed (or it wasn't given lately); turns on at that moment, without waiting for an update | `allowed_from` while off |

Attributes with fixed values (`feed_type`, `bottle_type`, `diaper_type`,
`sleep_type`, `bath_type`, `side`) read as lowercase words such as
`bottle`, `breast_milk`, `wet`, `night_sleep`, `left`, shown translated in
the UI. A custom bath type stays as it was typed.

### Buttons

| Button | Logs |
|---|---|
| Log wet diaper / Log dirty diaper / Log wet and dirty diaper | a diaper change, now |
| Start nap / Start night sleep | the start of a sleep (refused while asleep) |
| End sleep | the end of the running sleep |
| Start breastfeeding left / right | starts the breastfeeding timer on that side (refused while one runs) |
| Switch breastfeeding side / End breastfeeding | switches sides of, or ends, the running timer |

The breastfeeding timer is the one in the Sprout Track app: started here,
it runs there too, and the other way round. Ending it logs the feed.

Entities you don't need can be disabled in Home Assistant as usual.

## Actions

Every action takes the baby as `device_id` (*Baby* in the UI); with a
single baby it can be left out. `time` (default: now) and `caretaker`
(a caretaker's name in Sprout Track) are optional everywhere. Each action
returns what Sprout Track created if you ask for the response.

| Action | Fields |
|---|---|
| `sprout_track.log_feed` | `feed_type` (bottle, breast; default bottle); for a bottle `amount` with `unit` (as set up in Sprout Track, e.g. ML or OZ) and `bottle_type` (formula, breast_milk, formula_breast, milk, other); for a breastfeed `side` (left, right) and `duration` (minutes); `notes` |
| `sprout_track.breastfeeding` | `action` (start, switch, pause, resume, end), `side` (needed to start; optional to resume on the other side). No `time`: the timer runs from now |
| `sprout_track.log_diaper` | `diaper_type` (wet, dirty, both, dry), `condition`, `color`, `blowout`, `cream_applied`, `notes` |
| `sprout_track.start_sleep` | `sleep_type` (nap, night_sleep; default nap), `location`, `notes` |
| `sprout_track.end_sleep` | `sleep_type` (to change it), `quality`, `notes` |
| `sprout_track.log_sleep` | `sleep_type`, `duration` (minutes), `location`, `quality`, `notes` |
| `sprout_track.log_medicine` | `medicine` (name in Sprout Track), `amount` (default: its typical dose), `unit`, `notes` |
| `sprout_track.log_supplement` | `supplement`, `amount`, `unit`, `notes` |
| `sprout_track.log_bath` | `bath_type` (full_bath, sponge_bath, wipe_down, or your own text), `soap_used`, `shampoo_used` (Sprout Track assumes yes for both if left out), `notes` |
| `sprout_track.log_measurement` | `measurement_type` (weight, height, head_circumference, temperature), `value`, `unit` (e.g. KG, LB, CM, IN, C, F) |
| `sprout_track.log_activity` | `data`: any body the [API](https://github.com/Oak-and-Sprout/sprout-track/blob/main/documentation/Admin-Documentation/webhook-api.md) accepts (feed, bath, note, pump, measurement, food, play, ...) |

## Examples

A reminder when the next dose is allowed again:

```yaml
automation:
  - alias: Paracetamol – Next dose allowed
    triggers:
      - trigger: time
        at: sensor.emma_paracetamol_next_dose
    actions:
      - action: notify.mobile_app_phone
        data:
          message: Emma may have Paracetamol again.
```

A Zigbee button on the changing table: one press wet, double press dirty.

```yaml
automation:
  - alias: Changing table – Log diaper
    triggers:
      - trigger: state
        entity_id: event.changing_table_button
    actions:
      - choose:
          - conditions: "{{ trigger.to_state.attributes.event_type == 'single' }}"
            sequence:
              - action: button.press
                target:
                  entity_id: button.emma_log_wet_diaper
          - conditions: "{{ trigger.to_state.attributes.event_type == 'double' }}"
            sequence:
              - action: button.press
                target:
                  entity_id: button.emma_log_dirty_diaper
```

A nap in the car seat, and the vitamin drops with their usual dose:

```yaml
- action: sprout_track.start_sleep
  data:
    location: Car Seat

- action: sprout_track.log_supplement
  data:
    supplement: Vitamin D
```

A bottle of formula, and a finished breastfeed:

```yaml
- action: sprout_track.log_feed
  data:
    amount: 120
    unit: ML
    bottle_type: formula

- action: sprout_track.log_feed
  data:
    feed_type: breast
    side: left
    duration: 15
```

Anything else, through the pass-through action (here a bath):

```yaml
- action: sprout_track.log_activity
  data:
    data:
      type: bath
      soapUsed: true
```

Tell the phone when a feed is overdue:

```yaml
automation:
  - alias: Emma – Feed overdue
    triggers:
      - trigger: state
        entity_id: binary_sensor.emma_feed_overdue
        to: "on"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >
            Emma's last feed was
            {{ relative_time(states('sensor.emma_last_feed') | as_datetime) }} ago.
```

## Good to know

- **Updates** come every 60 seconds (changeable under *Configure*), and at
  once after a button or action. Entries made in the Sprout Track app show
  up with the next update.
- **API limits:** Sprout Track allows each key 60 reads and 30 writes per
  minute. An update costs one read plus one per baby; give other tools
  their own key. Doses and diapers are only fetched when the status shows
  a new one.
- **Read-only key:** sensors work; buttons and actions answer "This key
  does not have 'write' scope".
- **Measurements**, the list of medicines, all doses and the diaper list are fetched every
  15 minutes (so edits and deletions in Sprout Track show up).
- **Next dose** and **dose allowed** need a Sprout Track whose hooks API
  sends each medicine's minimum time between doses (`doseMinTime`).
  Released versions don't yet
  ([Oak-and-Sprout/sprout-track#298](https://github.com/Oak-and-Sprout/sprout-track/issues/298));
  until then only *last given* appears, and *Settings → Repairs* says so.
  The notice goes away by itself once the server sends the field.
- **Removing a baby:** a baby Sprout Track no longer lists (made
  inactive, deleted) goes unavailable and loses its device in Home
  Assistant after 24 hours. The wait keeps entity IDs, names and history
  safe from a single odd answer, such as an empty list while the server is
  being updated; a baby back within the day carries on as before. To
  remove it at once, delete the device by hand (*Delete* on its device
  page, offered while the baby is missing).
- **Many doses:** Sprout Track returns at most 50 doses of a kind at a
  time. When a month holds more, the older doses are kept from earlier
  updates (and saved across restarts), so a medicine given rarely keeps
  its *last given* while another is given often.
- **Renaming:** a baby renamed in Sprout Track renames its device (and
  so the entities' names); entity IDs stay. A name you gave the device in
  Home Assistant wins.
- **Troubleshooting:** *Download diagnostics* on the integration gives a
  file without names, dates, IDs or the key. Debug log:
  `custom_components.sprout_track: debug` under `logger:`.

## Layout

```
custom_components/sprout_track/
  api.py            HTTP client for /api/hooks/v1
  coordinator.py    polling, one data set per baby
  config_flow.py    setup, re-auth, reconfigure, options
  entity.py         base entities, one device per baby, per-medicine entities
  sensor.py  binary_sensor.py  button.py
  services.py       actions (and what the buttons send)
  services.yaml  icons.json  translations/
  brand/            the integration's icon (source: assets/icon.svg)
tests/              pytest, with recorded API answers in fixtures/
```

## Development

```bash
uv venv --python 3.14 ~/.venvs/sprout-track-ha
uv pip install --python ~/.venvs/sprout-track-ha/bin/python -r requirements_test.txt
~/.venvs/sprout-track-ha/bin/python -m pytest -q
~/.venvs/sprout-track-ha/bin/ruff check . && ~/.venvs/sprout-track-ha/bin/ruff format --check .

# The same tests on the oldest supported Home Assistant (2025.8.0)
uv venv --python 3.13 ~/.venvs/sprout-track-ha-min
uv pip install --python ~/.venvs/sprout-track-ha-min/bin/python -r requirements_test_min.txt
~/.venvs/sprout-track-ha-min/bin/python -m pytest -q
```

Tested with Sprout Track 1.6.8 and Home Assistant 2026.10; the GitHub
checks also run the tests on Home Assistant 2025.8.0, the oldest version
it supports. Work happens on
`dev`, releases are tagged on `main`; see [CHANGELOG.md](CHANGELOG.md).
Issues and pull requests are welcome.

## License

[MIT](LICENSE). Sprout Track itself is a separate project by
[Oak and Sprout](https://github.com/Oak-and-Sprout).
