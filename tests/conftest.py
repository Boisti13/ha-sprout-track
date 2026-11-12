"""Shared fixtures: a mocked Sprout Track API fed with recorded answers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.const import CONF_API_KEY, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sprout_track.const import DOMAIN

URL = "http://192.168.1.50:3000"
API = f"{URL}/api/hooks/v1"
KEY = "st_live_test"
FAMILY = "family-1"
BABY_1 = "baby-1"
BABY_2 = "baby-2"

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict[str, Any]:
    """Load a recorded API answer."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def api_error(code: str, message: str = "nope") -> dict[str, Any]:
    """Build an API error body."""
    return {"success": False, "error": {"code": code, "message": message}}


def mock_api(
    mock: AiohttpClientMocker,
    *,
    babies: dict[str, Any] | None = None,
    status_1: dict[str, Any] | None = None,
    measurements_1: dict[str, Any] | None = None,
) -> None:
    """Answer every read endpoint for the two sample babies."""
    mock.clear_requests()
    mock.get(f"{API}/babies", json=babies or load("babies"))
    mock.get(f"{API}/babies/{BABY_1}/status", json=status_1 or load("status"))
    mock.get(f"{API}/babies/{BABY_2}/status", json=load("status_empty"))
    mock.get(
        f"{API}/babies/{BABY_1}/measurements/latest",
        json=measurements_1 or load("measurements"),
    )
    mock.get(
        f"{API}/babies/{BABY_2}/measurements/latest",
        json={"success": True, "data": {"measurements": {}}},
    )
    for baby in (BABY_1, BABY_2):
        mock.get(f"{API}/babies/{baby}/reference", json=load("reference"))
        mock.post(
            f"{API}/babies/{baby}/activities",
            json={"success": True, "data": {"id": "new-1", "activityType": "x"}},
        )


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components."""


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry that is added but not set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Emma & Mia",
        unique_id=FAMILY,
        data={CONF_URL: URL, CONF_API_KEY: KEY, CONF_VERIFY_SSL: True},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def loaded(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> MockConfigEntry:
    """Return a config entry that is set up against the mocked API."""
    mock_api(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
