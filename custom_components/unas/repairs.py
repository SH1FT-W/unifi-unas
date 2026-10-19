"""Reparatur: die UNAS zeigt ein neues Zertifikat (z. B. nach Zurücksetzen); nach Bestätigung gilt es."""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import async_read_fingerprint
from .api import UnasError, format_fingerprint
from .const import CONF_FINGERPRINT


class CertificateChangedFlow(RepairsFlow):
    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id
        self._fingerprint = ""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_removed")
        if user_input is not None:
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_FINGERPRINT: self._fingerprint})
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(data={})
        try:
            # Bestätigt wird genau das Zertifikat, das hier angezeigt wird
            self._fingerprint = await async_read_fingerprint(self.hass, entry.data[CONF_HOST])
        except UnasError:
            return self.async_abort(reason="cannot_connect")
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": entry.title,
                "old": format_fingerprint(str(entry.data.get(CONF_FINGERPRINT, ""))),
                "new": format_fingerprint(self._fingerprint),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    return CertificateChangedFlow(str((data or {})["entry_id"]))
