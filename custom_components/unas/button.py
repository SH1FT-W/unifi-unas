"""Neustart, Ausschalten, Datenträger-Prüfung (Scrub) und SMART-Schnelltest je Festplatte."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Disk, UnasClient
from .const import DOMAIN, SMART_TEST_QUICK
from .coordinator import UnasConfigEntry, UnasCoordinator
from .entity import UnasDiskEntity, UnasEntity, disk_keys, disks_for, track_new_items
from .sensor import first_pool

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class UnasButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[UnasCoordinator], Awaitable[None]]


async def _start_scrub(coordinator: UnasCoordinator) -> None:
    pool = first_pool(coordinator.data)
    if pool is None:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_pool")
    client: UnasClient = coordinator.client
    await coordinator.async_command(lambda: client.start_scrub(pool.id))


BUTTONS: tuple[UnasButtonDescription, ...] = (
    UnasButtonDescription(
        key="restart",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda c: c.async_command(c.client.reboot),
    ),
    UnasButtonDescription(
        key="shutdown",
        entity_category=EntityCategory.CONFIG,
        # Danach ist die UNAS nur noch per Taste am Gerät wieder einzuschalten
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.async_command(c.client.power_off),
    ),
    UnasButtonDescription(key="start_scrub", entity_category=EntityCategory.CONFIG, press_fn=_start_scrub),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: UnasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(UnasButton(coordinator, description) for description in BUTTONS)

    def _disk_entities(keys: set[str]) -> list[Entity]:
        return [SmartTestButton(coordinator, d) for d in disks_for(coordinator.data, keys) if d.smart_test_supported]

    entry.async_on_unload(track_new_items(coordinator, disk_keys, _disk_entities, async_add_entities))


class UnasButton(UnasEntity, ButtonEntity):
    entity_description: UnasButtonDescription

    def __init__(self, coordinator: UnasCoordinator, description: UnasButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)


class SmartTestButton(UnasDiskEntity, ButtonEntity):
    """SMART-Schnelltest (ein paar Minuten); das Ergebnis zeigt Drive im Gesundheitsmonitor."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: UnasCoordinator, disk: Disk) -> None:
        super().__init__(coordinator, disk, "smart_test")

    async def async_press(self) -> None:
        disk = self.disk
        if disk is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="disk_missing")
        await self.coordinator.async_command(
            lambda: self.coordinator.client.run_smart_test(disk.slot, SMART_TEST_QUICK)
        )
