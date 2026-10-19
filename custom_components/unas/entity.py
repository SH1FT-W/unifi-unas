"""Gemeinsame Basis: ein Gerät für die UNAS, je Festplatte ein Untergerät (über die Seriennummer)."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Disk, UnasData
from .const import DOMAIN, MANUFACTURER
from .coordinator import UnasConfigEntry, UnasCoordinator


def entry_key(entry: UnasConfigEntry) -> str:
    """Eindeutiger Schlüssel des Eintrags für unique_ids und Geräte: MAC, sonst die Eintrags-ID."""
    return entry.unique_id or entry.entry_id


def unas_device_info(coordinator: UnasCoordinator) -> DeviceInfo:
    system = coordinator.data.system
    return DeviceInfo(
        identifiers={(DOMAIN, entry_key(coordinator.config_entry))},
        connections={(dr.CONNECTION_NETWORK_MAC, system.mac)} if system.mac else set(),
        manufacturer=MANUFACTURER,
        name=system.name,
        model=system.model,
        sw_version=system.firmware,
        configuration_url=f"https://{coordinator.client.host}/drive",
    )


def disk_identifier(disk: Disk) -> str:
    """Schlüssel einer Festplatte: Seriennummer, ohne Seriennummer der Schacht."""
    return f"disk_{disk.serial or disk.slot}"


def disk_keys(data: UnasData) -> Iterable[str]:
    return (disk_identifier(d) for d in data.disks.values())


def disks_for(data: UnasData, keys: set[str]) -> list[Disk]:
    return [d for d in data.disks.values() if disk_identifier(d) in keys]


class UnasEntity(CoordinatorEntity[UnasCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: UnasCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_key(coordinator.config_entry)}_{key}"
        self._attr_device_info = unas_device_info(coordinator)


class UnasDiskEntity(CoordinatorEntity[UnasCoordinator]):
    """Gehört zu einer Festplatte; wird sie entnommen, ist die Entität nicht verfügbar."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UnasCoordinator, disk: Disk, key: str) -> None:
        super().__init__(coordinator)
        self._serial = disk.serial
        self._slot = disk.slot
        self._attr_translation_key = key
        ident = disk_identifier(disk)
        self._attr_unique_id = f"{entry_key(coordinator.config_entry)}_{ident}_{key}"
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_key(coordinator.config_entry)}_{ident}")},
            model=disk.model or disk.type,
            translation_key="ssd" if disk.type == "SSD" else "disk",
            translation_placeholders={"name": coordinator.data.system.name, "slot": disk.slot},
            serial_number=disk.serial or None,
            sw_version=disk.firmware or None,
        )
        if coordinator.device_id:
            info["via_device_id"] = coordinator.device_id
        self._attr_device_info = info

    @property
    def disk(self) -> Disk | None:
        disk = self.coordinator.data.disks.get(self._slot)
        if disk is not None and disk.serial == self._serial:
            return disk
        if not self._serial:
            return None  # ohne Seriennummer zählt nur der Schacht
        # Platte in einen anderen Schacht gesteckt
        return next((d for d in self.coordinator.data.disks.values() if d.serial == self._serial), None)

    @property
    def available(self) -> bool:
        return super().available and self.disk is not None


def track_new_items(
    coordinator: UnasCoordinator,
    keys: Callable[[UnasData], Iterable[str]],
    create: Callable[[set[str]], list[Entity]],
    add: Callable[[list[Entity]], None],
) -> Callable[[], None]:
    """Entitäten für neu auftauchende Festplatten/Laufwerke nachlegen (auch zur Laufzeit)."""
    known: set[str] = set()

    @callback
    def _check() -> None:
        new = set(keys(coordinator.data)) - known
        if new:
            known.update(new)
            add(create(new))

    _check()
    return coordinator.async_add_listener(_check)
