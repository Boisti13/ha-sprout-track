"""Sprout Track integration: a baby tracker's figures and logging in Home Assistant."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .api import SproutTrackClient
from .const import DOMAIN
from .coordinator import SproutTrackConfigEntry, SproutTrackCoordinator, dose_store
from .services import async_setup_services

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once, independent of config entries."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SproutTrackConfigEntry) -> bool:
    """Set up one Sprout Track server (family)."""
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True))
    client = SproutTrackClient(session, entry.data[CONF_URL], entry.data[CONF_API_KEY])
    coordinator = SproutTrackCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SproutTrackConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: SproutTrackConfigEntry, device: DeviceEntry
) -> bool:
    """Allow deleting a device by hand only if its baby is gone."""
    coordinator = entry.runtime_data
    baby_ids = [identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN]
    if any(baby_id in coordinator.data for baby_id in baby_ids):
        return False
    for baby_id in baby_ids:
        coordinator.forget(baby_id)
    # Let the platforms forget the baby, so it gets new entities if it comes back
    coordinator.async_update_listeners()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: SproutTrackConfigEntry) -> None:
    """Delete the saved doses along with the entry."""
    await dose_store(hass, entry.entry_id).async_remove()
