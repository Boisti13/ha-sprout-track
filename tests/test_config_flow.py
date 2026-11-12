"""Config flow: setup, errors, re-auth, reconfigure, options."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sprout_track.const import CONF_SCAN_INTERVAL, DOMAIN

from .conftest import API, FAMILY, KEY, URL, api_error, load, mock_api

USER_INPUT = {CONF_URL: f"{URL}/api/hooks/v1/", CONF_API_KEY: f" {KEY} ", CONF_VERIFY_SSL: True}


async def test_user_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A pasted API URL is cut down to the server address; babies name the entry."""
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], dict(USER_INPUT))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Emma & Mia"
    assert result["data"] == {CONF_URL: URL, CONF_API_KEY: KEY, CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == FAMILY
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize(
    ("status", "body", "field", "error"),
    [
        (401, api_error("INVALID_KEY"), CONF_API_KEY, "invalid_auth"),
        (401, api_error("KEY_REVOKED"), CONF_API_KEY, "invalid_auth"),
        (403, api_error("INSUFFICIENT_SCOPE"), CONF_API_KEY, "read_scope_required"),
        (403, api_error("HTTPS_REQUIRED"), CONF_URL, "https_required"),
        (500, api_error("INTERNAL_ERROR"), "base", "unknown"),
        (404, None, "base", "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: dict | None,
    field: str,
    error: str,
) -> None:
    """Every failure shows its own message, and a retry still works."""
    if body is None:
        aioclient_mock.get(f"{API}/babies", status=status, text="<html>Not found</html>")
    else:
        aioclient_mock.get(f"{API}/babies", status=status, json=body)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=dict(USER_INPUT)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}

    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], dict(USER_INPUT))
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A server that does not answer is reported as such."""
    aioclient_mock.get(f"{API}/babies", exc=TimeoutError)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=dict(USER_INPUT)
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_invalid_url(hass: HomeAssistant) -> None:
    """An address without http(s):// is refused before any request."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={**USER_INPUT, CONF_URL: "sprout.local:3000"}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_already_configured(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """The same family cannot be added twice."""
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=dict(USER_INPUT)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A new key replaces the old one."""
    result = await loaded.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/babies", status=401, json=api_error("INVALID_KEY"))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "st_live_bad"}
    )
    assert result["errors"] == {CONF_API_KEY: "invalid_auth"}

    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "st_live_new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert loaded.data[CONF_API_KEY] == "st_live_new"
    assert loaded.data[CONF_URL] == URL


async def test_reauth_other_family(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A key of another family is refused."""
    babies = load("babies")
    babies["meta"]["familyId"] = "family-2"
    mock_api(aioclient_mock, babies=babies)
    result = await loaded.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "st_live_other"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_family"
    assert loaded.data[CONF_API_KEY] == KEY


async def test_reconfigure(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """The address can be changed later."""
    new_url = "https://sprout.example.com"
    aioclient_mock.get(f"{new_url}/api/hooks/v1/babies", json=load("babies"))
    result = await loaded.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: new_url, CONF_API_KEY: KEY, CONF_VERIFY_SSL: False}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert loaded.data == {CONF_URL: new_url, CONF_API_KEY: KEY, CONF_VERIFY_SSL: False}


async def test_options(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """The polling interval is an option and takes effect after the reload."""
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert loaded.options == {CONF_SCAN_INTERVAL: 120}
    assert loaded.runtime_data.update_interval.total_seconds() == 120
