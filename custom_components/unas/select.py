"""Lüfterprofil (leise / Standard / kühlen)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import FAN_PROFILES
from .coordinator import UnasConfigEntry, UnasCoordinator
from .entity import UnasEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: UnasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.data.fan_profile is not None:
        async_add_entities([FanProfileSelect(coordinator)])


class FanProfileSelect(UnasEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: UnasCoordinator) -> None:
        super().__init__(coordinator, "fan_profile")
        # Feste Reihenfolge leise → kühlen; unbekannte Profile neuer Firmware hinten anhängen
        offered = coordinator.data.fan_profiles or FAN_PROFILES
        self._attr_options = [p for p in FAN_PROFILES if p in offered] + [p for p in offered if p not in FAN_PROFILES]

    @property
    def current_option(self) -> str | None:
        return self.coordinator.data.fan_profile

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_command(lambda: self.coordinator.client.set_fan_profile(option))
        # Gleich anzeigen; die nächste Abfrage bestätigt (async_request_refresh wartet bis zu 10 s)
        self.coordinator.data.fan_profile = option
        self.async_write_ha_state()
