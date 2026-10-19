"""UNAS: UniFi-NAS (UNAS 2, UNAS Pro …) lokal über UniFi OS und UniFi Drive einbinden."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.ssl import get_default_no_verify_context

from .api import UnasClient, UnasError
from .const import CONF_FINGERPRINT, DOMAIN
from .coordinator import UnasConfigEntry, UnasCoordinator
from .entity import disk_identifier, entry_key, unas_device_info

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT, Platform.SENSOR, Platform.UPDATE]


def create_client(hass: HomeAssistant, data: Mapping[str, Any], *, verify_ssl: bool = False) -> UnasClient:
    # Selbstsigniertes Zertifikat der UNAS: standardmäßig HAs Session ohne CA-Prüfung, dafür nur das gemerkte
    # Zertifikat (Fingerabdruck in entry.data); mit der Option „Zertifikat prüfen“ die normale Session mit Prüfung
    return UnasClient(
        async_get_clientsession(hass, verify_ssl=verify_ssl),
        data[CONF_HOST],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        verify_ssl=verify_ssl,
        fingerprint=data.get(CONF_FINGERPRINT),
    )


async def async_read_fingerprint(hass: HomeAssistant, host: str) -> str:
    """Fingerabdruck des Zertifikats, das die UNAS gerade zeigt (zum Merken bei Einrichtung oder Reparatur)."""
    client = UnasClient(async_get_clientsession(hass, verify_ssl=False), host, "", "")
    return await client.certificate_fingerprint(get_default_no_verify_context())


def verify_ssl_option(entry: UnasConfigEntry) -> bool:
    return bool(entry.options.get(CONF_VERIFY_SSL, False))


async def async_setup_entry(hass: HomeAssistant, entry: UnasConfigEntry) -> bool:
    verify_ssl = verify_ssl_option(entry)
    if not verify_ssl and not entry.data.get(CONF_FINGERPRINT):
        # Einträge von vor dem Pinning: Zertifikat jetzt einmal merken
        try:
            fingerprint = await async_read_fingerprint(hass, entry.data[CONF_HOST])
        except UnasError as err:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="unreachable", translation_placeholders={"error": str(err)}
            ) from err
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_FINGERPRINT: fingerprint})
    client = create_client(hass, entry.data, verify_ssl=verify_ssl)
    coordinator = UnasCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **unas_device_info(coordinator))
    coordinator.device_id = device.id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Geänderte Optionen (Zertifikat prüfen) gelten nach dem Neuladen
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: UnasConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: UnasConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: UnasConfigEntry, device: dr.DeviceEntry) -> bool:
    """Ausgebaute Festplatten dürfen weg, die UNAS selbst und eingebaute Platten nicht."""
    key = entry_key(entry)
    present = {f"{key}_{disk_identifier(d)}" for d in entry.runtime_data.data.disks.values()}
    return not any(domain == DOMAIN and (ident == key or ident in present) for domain, ident in device.identifiers)
