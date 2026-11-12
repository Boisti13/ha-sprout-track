"""Diagnostics download, with everything personal blanked."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant

from .coordinator import SproutTrackConfigEntry

TO_REDACT = {
    CONF_API_KEY,
    CONF_URL,
    "id",
    "firstName",
    "lastName",
    "birthDate",
    "ageInDays",
    "ageFormatted",
    "caretakerName",
    "medicineName",
    "supplementName",
    "notes",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SproutTrackConfigEntry
) -> dict[str, Any]:
    """Return the entry and the last data, redacted."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "babies": [async_redact_data(asdict(baby), TO_REDACT) for baby in coordinator.data.values()]
        if coordinator.data
        else [],
    }
