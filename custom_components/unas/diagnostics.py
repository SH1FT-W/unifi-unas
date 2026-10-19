"""Diagnose-Download: alle abgefragten Werte, ohne Adresse, Zugang, MAC und Seriennummern."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import UnasConfigEntry

TO_REDACT = {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, "mac", "ip", "serial"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: UnasConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    err = coordinator.last_exception
    error = str(err).replace(coordinator.client.host, "**REDACTED**") if err else None
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "last_error": None if coordinator.last_update_success else error,
        "data": async_redact_data(asdict(coordinator.data), TO_REDACT) if coordinator.data else None,
    }
