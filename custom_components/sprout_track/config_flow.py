"""Config flow: server address + API key, re-auth, reconfigure, options."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol
from yarl import URL

from .api import (
    SproutTrackApiError,
    SproutTrackAuthError,
    SproutTrackClient,
    SproutTrackConnectionError,
    normalize_url,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import SproutTrackConfigEntry

_LOGGER = logging.getLogger(__name__)

_URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, vol.UNDEFINED)): (_URL_SELECTOR),
            vol.Required(CONF_API_KEY): _KEY_SELECTOR,
            vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)): bool,
        }
    )


class SproutTrackConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Sprout Track from the UI."""

    VERSION = 1

    async def _validate(
        self, data: dict[str, Any], errors: dict[str, str]
    ) -> tuple[str | None, str]:
        """Try the address and key; return the family ID and a title."""
        url = normalize_url(data[CONF_URL])
        parsed = URL(url)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            errors[CONF_URL] = "invalid_url"
            return None, ""
        data[CONF_URL] = url
        data[CONF_API_KEY] = data[CONF_API_KEY].strip()

        session = async_get_clientsession(self.hass, verify_ssl=data.get(CONF_VERIFY_SSL, True))
        client = SproutTrackClient(session, url, data[CONF_API_KEY])
        try:
            babies, family_id = await client.get_babies()
        except SproutTrackAuthError:
            errors[CONF_API_KEY] = "invalid_auth"
        except SproutTrackConnectionError:
            errors["base"] = "cannot_connect"
        except SproutTrackApiError as err:
            if err.code == "HTTPS_REQUIRED":
                errors[CONF_URL] = "https_required"
            elif err.code == "INSUFFICIENT_SCOPE":
                errors[CONF_API_KEY] = "read_scope_required"
            else:
                _LOGGER.error("Sprout Track answered %s: %s", err.code, err.message)
                errors["base"] = "unknown"
        except Exception:
            _LOGGER.exception("Unexpected error while talking to Sprout Track")
            errors["base"] = "unknown"
        else:
            if not family_id:
                errors["base"] = "unknown"
                return None, ""
            names = [b.get("firstName") for b in babies if b.get("firstName")]
            return family_id, " & ".join(names) or (parsed.host or "Sprout Track")
        return None, ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the server address and an API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            family_id, title = await self._validate(user_input, errors)
            if family_id:
                await self.async_set_unique_id(family_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start again when the key was revoked or expired."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            family_id, _ = await self._validate(data, errors)
            if family_id:
                await self.async_set_unique_id(family_id)
                self._abort_if_unique_id_mismatch(reason="wrong_family")
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): _KEY_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address or key of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            family_id, _ = await self._validate(user_input, errors)
            if family_id:
                await self.async_set_unique_id(family_id)
                self._abort_if_unique_id_mismatch(reason="wrong_family")
                return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(user_input or entry.data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: SproutTrackConfigEntry,
    ) -> SproutTrackOptionsFlow:
        """Return the options flow."""
        return SproutTrackOptionsFlow()


class SproutTrackOptionsFlow(OptionsFlowWithReload):
    """Options: how often to poll."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show or save the options."""
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
