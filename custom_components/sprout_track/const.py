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

MANUFACTURER = "Sprout Track"
