"""Async client for Sprout Track's hooks API (/api/hooks/v1).

Kept free of Home Assistant imports so it can be tested and reused alone.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import API_PATH, REQUEST_TIMEOUT


class SproutTrackError(Exception):
    """Base class for all Sprout Track errors."""


class SproutTrackConnectionError(SproutTrackError):
    """The server could not be reached, or what answered is not Sprout Track."""


class SproutTrackApiError(SproutTrackError):
    """The API answered with an error."""

    def __init__(self, code: str, message: str, status: int) -> None:
        """Keep the API's error code, message and HTTP status."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class SproutTrackAuthError(SproutTrackApiError):
    """The API key is missing, unknown, revoked or expired (HTTP 401)."""


class SproutTrackRateLimitError(SproutTrackApiError):
    """Too many requests (HTTP 429)."""

    def __init__(self, code: str, message: str, status: int, retry_after: float | None) -> None:
        """Keep the seconds to wait, if the server said so."""
        super().__init__(code, message, status)
        self.retry_after = retry_after


def normalize_url(url: str) -> str:
    """Return the server's base URL without a trailing slash or API path."""
    url = url.strip().rstrip("/")
    if url.endswith(API_PATH):
        url = url[: -len(API_PATH)].rstrip("/")
    return url


class SproutTrackClient:
    """Small client for the hooks API."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str) -> None:
        """Set up the client; the session belongs to the caller."""
        self._session = session
        self.base_url = normalize_url(base_url)
        self._api_key = api_key.strip()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """Send one request; return the answer's ``data`` and ``meta``."""
        url = f"{self.base_url}{API_PATH}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.request(
                    method, url, headers=headers, params=params, json=json
                )
                status = response.status
                retry_after = response.headers.get("Retry-After")
                try:
                    body = await response.json(content_type=None)
                except ValueError:
                    body = None
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SproutTrackConnectionError(
                f"Cannot reach Sprout Track at {self.base_url}: {err or type(err).__name__}"
            ) from err

        if not isinstance(body, dict) or "success" not in body:
            # A proxy's error page, a wrong URL, another web app ...
            raise SproutTrackConnectionError(
                f"{self.base_url} did not answer like Sprout Track (HTTP {status})"
            )

        if body["success"] is True:
            return body.get("data"), body.get("meta") or {}

        error = body.get("error")
        if not isinstance(error, dict):
            error = {}
        code = str(error.get("code") or "UNKNOWN")
        message = str(error.get("message") or code)

        if status == 401:
            raise SproutTrackAuthError(code, message, status)
        if status == 429:
            try:
                wait = float(retry_after) if retry_after else None
            except ValueError:
                wait = None
            raise SproutTrackRateLimitError(code, message, status, wait)
        raise SproutTrackApiError(code, message, status)

    async def get_babies(self) -> tuple[list[dict[str, Any]], str | None]:
        """Return the babies the key can see, and the family's ID."""
        data, meta = await self._request("GET", "/babies")
        babies = (data or {}).get("babies") or []
        return [b for b in babies if isinstance(b, dict) and b.get("id")], meta.get("familyId")

    async def get_status(self, baby_id: str, timezone: str | None = None) -> dict[str, Any]:
        """Return a baby's status: last activities, today's counts, warnings."""
        params = {"timezone": timezone} if timezone else None
        data, _ = await self._request("GET", f"/babies/{baby_id}/status", params=params)
        return data or {}

    async def get_measurements(self, baby_id: str) -> dict[str, Any]:
        """Return the latest measurement of each type (or None per type)."""
        data, _ = await self._request("GET", f"/babies/{baby_id}/measurements/latest")
        return (data or {}).get("measurements") or {}

    async def get_reference(self, baby_id: str, ref_type: str | None = None) -> dict[str, Any]:
        """Return reference data: medicines, supplements, sleep locations, ..."""
        params = {"type": ref_type} if ref_type else None
        data, _ = await self._request("GET", f"/babies/{baby_id}/reference", params=params)
        return data or {}

    async def create_activity(self, baby_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Log an activity; return what the API created."""
        data, _ = await self._request("POST", f"/babies/{baby_id}/activities", json=payload)
        return data or {}
