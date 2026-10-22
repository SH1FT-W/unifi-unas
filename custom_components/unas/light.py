"""Display und Status-LED der UNAS (UniFi OS): an/aus, beim Display auch die Helligkeit.

Eine Farbe gibt es bei der UNAS nicht (UniFi OS bietet sie nur Konsolen mit „Bezel“ an). Ändern braucht ein Konto mit
Admin-Rechten in UniFi OS (Super Admin), sonst meldet die UNAS 403 und HA zeigt „fehlende Rechte“.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import UnasConfigEntry, UnasCoordinator
from .entity import UnasEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: UnasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    settings = coordinator.data.os_settings
    entities: list[LightEntity] = []
    if settings.get("hasLCM"):
        entities.append(UnasOsLight(coordinator, "display", "lcmSettings", ColorMode.BRIGHTNESS))
    if "ledSettings" in settings:
        entities.append(UnasOsLight(coordinator, "status_led", "ledSettings", ColorMode.ONOFF))
    async_add_entities(entities)


class UnasOsLight(UnasEntity, LightEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: UnasCoordinator, key: str, setting: str, mode: ColorMode) -> None:
        super().__init__(coordinator, key)
        self._setting = setting
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    @property
    def _values(self) -> dict[str, Any]:
        values = self.coordinator.data.os_settings.get(self._setting)
        return values if isinstance(values, dict) else {}

    @property
    def available(self) -> bool:
        return super().available and bool(self._values)

    @property
    def is_on(self) -> bool | None:
        enabled = self._values.get("enabled")
        return bool(enabled) if enabled is not None else None

    @property
    def brightness(self) -> int | None:
        value = self._values.get("brightness")
        if self._attr_color_mode != ColorMode.BRIGHTNESS or not isinstance(value, int | float):
            return None
        # UniFi: 1–100 %, HA: 0–255
        return round(int(value) * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        changes: dict[str, Any] = {"enabled": True}
        if ATTR_BRIGHTNESS in kwargs and self._attr_color_mode == ColorMode.BRIGHTNESS:
            changes["brightness"] = max(1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
        await self.coordinator.async_os_command({self._setting: changes})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_os_command({self._setting: {"enabled": False}})
