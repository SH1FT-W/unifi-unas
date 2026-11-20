"""Nachtmodus von UniFi OS: ab wann Display und LED der UNAS nachts gedimmt sind und bis wann."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import UnasData
from .coordinator import UnasConfigEntry, UnasCoordinator
from .entity import UnasEntity, track_new_items

PARALLEL_UPDATES = 1

# Schlüssel → Feld in nightMode (Minuten seit Mitternacht)
NIGHT_MODE = {"night_mode_start": "onMinute", "night_mode_end": "offMinute"}


async def async_setup_entry(
    hass: HomeAssistant, entry: UnasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    def _keys(data: UnasData) -> list[str]:
        return list(NIGHT_MODE) if "nightMode" in data.os_settings else []

    def _entities(keys: set[str]) -> list[Entity]:
        return [NightModeTime(coordinator, key) for key in NIGHT_MODE if key in keys]

    # UniFi OS settings come from the slow poll: if it failed at startup, the times appear once it succeeds
    entry.async_on_unload(track_new_items(coordinator, _keys, _entities, async_add_entities))


class NightModeTime(UnasEntity, TimeEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: UnasCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._field = NIGHT_MODE[key]

    @property
    def _night_mode(self) -> dict[str, int]:
        night_mode = self.coordinator.data.os_settings.get("nightMode")
        items = night_mode.items() if isinstance(night_mode, dict) else ()
        return {k: v for k, v in items if isinstance(v, int) and not isinstance(v, bool)}

    @property
    def native_value(self) -> time | None:
        minutes = self._night_mode.get(self._field)
        if minutes is None:
            return None
        minutes %= 24 * 60
        return time(minutes // 60, minutes % 60)

    async def async_set_value(self, value: time) -> None:
        # Die Oberfläche schickt immer beide Zeiten
        await self.coordinator.async_os_command(
            {"nightMode": {**self._night_mode, self._field: value.hour * 60 + value.minute}}
        )
