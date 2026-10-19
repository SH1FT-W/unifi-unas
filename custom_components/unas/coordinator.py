"""Fragt die UNAS alle 30 s ab; Firmware-Stand, Dienste und Speicher-Durchsatz nur alle 5 Minuten."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    UnasAuthError,
    UnasCertificateError,
    UnasClient,
    UnasData,
    UnasError,
    UnasPermissionError,
    format_fingerprint,
)
from .const import CONF_FINGERPRINT, DOMAIN, SLOW_EVERY, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type UnasConfigEntry = ConfigEntry[UnasCoordinator]


def cert_issue_id(entry: ConfigEntry) -> str:
    return f"cert_changed_{entry.entry_id}"


class UnasCoordinator(DataUpdateCoordinator[UnasData]):
    config_entry: UnasConfigEntry

    def __init__(self, hass: HomeAssistant, entry: UnasConfigEntry, client: UnasClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.client = client
        self._round = 0
        self._cert_issue = False
        # Gerät der UNAS (in async_setup_entry angelegt); Festplatten hängen darunter
        self.device_id: str | None = None

    async def _async_update_data(self) -> UnasData:
        try:
            data = await self.client.fetch()
            if self._round % SLOW_EVERY == 0 or self.data is None:
                await self.client.fetch_slow(data)
            else:
                data.latest_firmware = self.data.latest_firmware
                data.drive_update = self.data.drive_update
                data.services = self.data.services
                data.storage_read_kbps = self.data.storage_read_kbps
                data.storage_write_kbps = self.data.storage_write_kbps
        except UnasAuthError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="auth_failed") from err
        except UnasCertificateError as err:
            self._raise_cert_issue(err)
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="cert_changed") from err
        except UnasError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="unreachable", translation_placeholders={"error": str(err)}
            ) from err
        if self._cert_issue:
            ir.async_delete_issue(self.hass, DOMAIN, cert_issue_id(self.config_entry))
            self._cert_issue = False
        self._round += 1
        return data

    def _raise_cert_issue(self, err: UnasCertificateError) -> None:
        """Reparatur-Hinweis mit altem und neuem Fingerabdruck; bestätigt der Nutzer, gilt das neue Zertifikat."""
        entry = self.config_entry
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            cert_issue_id(entry),
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="cert_changed",
            translation_placeholders={
                "name": entry.title,
                "old": format_fingerprint(str(entry.data.get(CONF_FINGERPRINT, ""))),
                "new": format_fingerprint(err.fingerprint),
            },
            data={"entry_id": entry.entry_id},
        )
        self._cert_issue = True

    async def async_command(self, command: Callable[[], Awaitable[None]]) -> None:
        """Befehl an die UNAS schicken und danach (gebündelt) neu abfragen."""
        try:
            await command()
        except UnasAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
        except UnasPermissionError as err:
            # Passwort stimmt, nur die Rechte fehlen: kein Reauth
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_permission") from err
        except UnasCertificateError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="cert_changed") from err
        except UnasError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.async_request_refresh()
