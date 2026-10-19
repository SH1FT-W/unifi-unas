"""Einrichtung: Adresse der UNAS und ein lokales UniFi-OS-Konto (kein UI-Konto mit Zwei-Faktor).

Ohne „Zertifikat prüfen“ merkt sich der Flow zuerst das Zertifikat der UNAS und schickt die Zugangsdaten nur an
dieses (trust on first use); später gilt nur noch dieses Zertifikat.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import voluptuous as vol

from . import async_read_fingerprint, create_client, verify_ssl_option
from .api import UnasAuthError, UnasCertificateError, UnasError, UnasPermissionError
from .const import CONF_FINGERPRINT, DOMAIN

_PASSWORD = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
_VERIFY_SSL = vol.Optional(CONF_VERIFY_SSL, default=False)


def _split(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Zugangsdaten (entry.data) und Optionen (entry.options) aus einer Formulareingabe trennen."""
    data = dict(user_input)
    verify = bool(data.pop(CONF_VERIFY_SSL, False))
    return data, {CONF_VERIFY_SSL: verify}


async def validate(
    hass: HomeAssistant, data: dict[str, Any], *, verify_ssl: bool = False
) -> tuple[str | None, str | None, str]:
    """Anmelden und einmal abfragen → (Fehler, MAC, Name).

    Ohne CA-Prüfung und ohne gemerktes Zertifikat liest es zuerst den Fingerabdruck und legt ihn in `data` ab."""
    if verify_ssl:
        data.pop(CONF_FINGERPRINT, None)
    elif not data.get(CONF_FINGERPRINT):
        try:
            data[CONF_FINGERPRINT] = await async_read_fingerprint(hass, data[CONF_HOST])
        except UnasError:
            return "cannot_connect", None, ""
    client = create_client(hass, data, verify_ssl=verify_ssl)
    try:
        result = await client.fetch()
    except UnasAuthError:
        return "invalid_auth", None, ""
    except UnasPermissionError:
        return "no_permission", None, ""
    except UnasCertificateError:
        return "cert_changed", None, ""
    except UnasError:
        return "cannot_connect", None, ""
    if not result.system.mac:
        return "not_unas", None, ""
    return None, format_mac(result.system.mac), result.system.name


class UnasConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host = ""
        self._name = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> UnasOptionsFlow:
        return UnasOptionsFlow()

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Gefunden über SMB-Ankündigung „UNAS-…“; /api/system bestätigt Modell und MAC ohne Anmeldung."""
        host = str(discovery_info.ip_address)
        try:
            mac, name, _model = await create_client(
                self.hass, {CONF_HOST: host, CONF_USERNAME: "", CONF_PASSWORD: ""}
            ).identify()
        except UnasError:
            return self.async_abort(reason="not_unas")
        await self.async_set_unique_id(format_mac(mac))
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._host, self._name = host, name
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data, options = _split({CONF_HOST: self._host, **user_input})
            error, mac, name = await validate(self.hass, data, verify_ssl=options[CONF_VERIFY_SSL])
            if error is None and mac == self.unique_id:
                return self.async_create_entry(title=name, data=data, options=options)
            errors["base"] = error or "wrong_device"
        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): _PASSWORD, _VERIFY_SSL: bool}
            ),
            description_placeholders={"name": self._name, "host": self._host},
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data, options = _split(user_input)
            error, mac, name = await validate(self.hass, data, verify_ssl=options[CONF_VERIFY_SSL])
            if error is None and mac:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured(updates={CONF_HOST: data[CONF_HOST]})
                return self.async_create_entry(title=name, data=data, options=options)
            errors["base"] = error or "unknown"
        defaults = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
                    vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, vol.UNDEFINED)): str,
                    vol.Required(CONF_PASSWORD): _PASSWORD,
                    vol.Optional(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, False)): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Neues Passwort, z. B. nachdem es in UniFi OS geändert wurde."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            error, mac, _name = await validate(self.hass, data, verify_ssl=verify_ssl_option(entry))
            if error is None:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")): str,
                    vol.Required(CONF_PASSWORD): _PASSWORD,
                }
            ),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Neue IP-Adresse oder anderes Konto."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            # Neue Adresse oder neu aufgesetzte UNAS: Zertifikat neu merken
            data.pop(CONF_FINGERPRINT, None)
            error, mac, _name = await validate(self.hass, data, verify_ssl=verify_ssl_option(entry))
            if error is None:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        current = user_input or entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=current.get(CONF_HOST, "")): str,
                    vol.Required(CONF_USERNAME, default=current.get(CONF_USERNAME, "")): str,
                    vol.Required(CONF_PASSWORD): _PASSWORD,
                }
            ),
            errors=errors,
        )


class UnasOptionsFlow(OptionsFlow):
    """Zertifikat prüfen: an nur mit vertrauenswürdigem Zertifikat auf der UNAS; die Verbindung wird vorher getestet."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            verify = bool(user_input[CONF_VERIFY_SSL])
            # Prüfen an einer Kopie: ein gemerkter Fingerabdruck bleibt im Eintrag (fehlt er, merkt ihn das Setup)
            error, _mac, _name = await validate(self.hass, dict(self.config_entry.data), verify_ssl=verify)
            if error is None:
                return self.async_create_entry(data={CONF_VERIFY_SSL: verify})
            errors["base"] = error
        current = user_input[CONF_VERIFY_SSL] if user_input else verify_ssl_option(self.config_entry)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required(CONF_VERIFY_SSL, default=current): bool}),
            errors=errors,
        )
