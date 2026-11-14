"""Constants for the Sprout Track integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "sprout_track"

API_PATH = "/api/hooks/v1"
REQUEST_TIMEOUT = 15

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 3600

# Measurements (weight, height, ...) change rarely; fetch them less often
# than the status so a family with several babies stays far below the
# API's 60 reads per minute.
MEASUREMENT_INTERVAL = timedelta(minutes=15)

# Medicines and supplements: the doses of each kind are fetched when the
# status shows a new one, and with the measurements (to catch edits and
# deletions); the list of medicines with the measurements too.
DOSE_KINDS = ("medicine", "supplement")
DOSE_HISTORY = timedelta(days=30)
# Released Sprout Track doesn't send minimum gaps between doses yet
DOSE_GAP_ISSUE_URL = "https://github.com/Oak-and-Sprout/sprout-track/issues/298"
DOSE_LIMIT = 50  # the API's maximum
# A full page of doses means older ones were cut off; those are kept from
# earlier fetches (and saved across restarts) instead of being forgotten.
DOSE_STORE_VERSION = 1
DOSE_STORE_DELAY = 10  # seconds to gather changes before writing the file
FEED_HISTORY = timedelta(hours=24)  # covers today and the last 24 hours
FEED_LIMIT = 50

# A baby or medicine Sprout Track stops listing keeps its device and
# entities (unavailable) this long before they are removed, so one odd
# answer (an empty list during a server update, a medicine switched off for
# a day) doesn't throw away entity IDs, names and history.
REMOVE_AFTER = timedelta(hours=24)

MANUFACTURER = "Sprout Track"
