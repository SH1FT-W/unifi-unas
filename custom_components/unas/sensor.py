"""Sensoren: Speicher, System, Netzwerk, je Festplatte und je Drive-Laufwerk."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import Disk, Pool, UnasData
from .coordinator import UnasConfigEntry, UnasCoordinator
from .entity import UnasDiskEntity, UnasEntity, disk_keys, disks_for, track_new_items

PARALLEL_UPDATES = 0

POOL_STATES = ["ok", "degraded", "rebuilding", "initializing", "error", "other"]
SCRUB_STATES = ["idle", "in_progress", "paused", "other"]
# Zustandswert von Drive: 5 = gesund; was 1–4 genau heißt, verrät Drive nicht, darunter ist also „prüfen“
HEALTH_STATES = ["good", "check"]
HEALTH_SCORE_MAX = 5


def _health_attributes(disk: Disk) -> dict[str, Any]:
    return {"score": f"{disk.health_score} / {HEALTH_SCORE_MAX}"} if disk.health_score is not None else {}


def disk_health(disk: Disk) -> str | None:
    if disk.health_score is None:
        return None
    return "good" if disk.health_score >= HEALTH_SCORE_MAX else "check"


def pool_state(status: str) -> str:
    """Drive-Zustände („fullyOperational“, „degraded…“) auf wenige feste Werte abbilden."""
    s = status.lower()
    if s in ("fullyoperational", "healthy", "optimal"):
        return "ok"
    for needle, state in (
        ("degrad", "degraded"),
        ("rebuild", "rebuilding"),
        ("resync", "rebuilding"),
        ("expand", "rebuilding"),
        ("init", "initializing"),
        ("fail", "error"),
        ("error", "error"),
        ("crash", "error"),
    ):
        if needle in s:
            return state
    return "other"


def worst_pool_state(data: UnasData) -> str | None:
    states = [pool_state(p.status) for p in data.pools.values()]
    for state in ("error", "degraded", "rebuilding", "initializing", "other", "ok"):
        if state in states:
            return state
    return None


def first_pool(data: UnasData) -> Pool | None:
    return min(data.pools.values(), key=lambda p: p.number, default=None)


def _scrub_state(data: UnasData) -> str | None:
    pool = first_pool(data)
    if pool is None:
        return None
    return {"idle": "idle", "inProgress": "in_progress", "paused": "paused"}.get(pool.scrub_status, "other")


def _used_percent(data: UnasData) -> float | None:
    capacity = sum(p.capacity for p in data.pools.values())
    return round(sum(p.used for p in data.pools.values()) / capacity * 100, 1) if capacity else None


def _hottest_disk(data: UnasData) -> float | None:
    return max((d.temperature for d in data.disks.values() if d.temperature is not None), default=None)


def _memory_percent(data: UnasData) -> float | None:
    s = data.system
    if not s.memory_total or s.memory_available is None:
        return None
    return round((s.memory_total - s.memory_available) / s.memory_total * 100, 1)


@dataclass(frozen=True, kw_only=True)
class UnasSensorDescription(SensorEntityDescription):
    value_fn: Callable[[UnasData], StateType | datetime]


@dataclass(frozen=True, kw_only=True)
class DiskSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Disk], StateType]
    attributes_fn: Callable[[Disk], dict[str, Any]] | None = None


SENSORS: tuple[UnasSensorDescription, ...] = (
    UnasSensorDescription(
        key="storage_used",
        value_fn=lambda d: sum(p.used for p in d.pools.values()) or None,
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnasSensorDescription(
        key="storage_free",
        value_fn=lambda d: sum(max(p.capacity - p.used, 0) for p in d.pools.values()) or None,
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnasSensorDescription(
        key="storage_capacity",
        value_fn=lambda d: sum(p.capacity for p in d.pools.values()) or None,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnasSensorDescription(
        key="storage_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_used_percent,
    ),
    UnasSensorDescription(
        key="storage_status", device_class=SensorDeviceClass.ENUM, options=POOL_STATES, value_fn=worst_pool_state
    ),
    UnasSensorDescription(
        key="disks_at_risk",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for disk in d.disks.values() if disk.at_risk),
    ),
    UnasSensorDescription(
        key="hottest_disk",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_hottest_disk,
    ),
    UnasSensorDescription(
        key="storage_read",
        value_fn=lambda d: d.storage_read_kbps,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnasSensorDescription(
        key="storage_write",
        value_fn=lambda d: d.storage_write_kbps,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnasSensorDescription(
        key="scrub_status",
        device_class=SensorDeviceClass.ENUM,
        options=SCRUB_STATES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_scrub_state,
    ),
    UnasSensorDescription(
        key="scrub_last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: p.scrub_last_run if (p := first_pool(d)) else None,
    ),
    UnasSensorDescription(
        key="cpu_load",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system.cpu_load,
    ),
    UnasSensorDescription(
        key="cpu_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system.cpu_temperature,
    ),
    UnasSensorDescription(
        key="memory_used",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_memory_percent,
    ),
    UnasSensorDescription(
        key="started",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system.started,
    ),
    UnasSensorDescription(
        key="network_receive",
        value_fn=lambda d: d.receive_kbps,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnasSensorDescription(
        key="network_transmit",
        value_fn=lambda d: d.transmit_kbps,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnasSensorDescription(
        key="link_speed",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system.link_speed or None,
    ),
    UnasSensorDescription(
        key="drive_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.system.drive_version or None,
    ),
)

DISK_SENSORS: tuple[DiskSensorDescription, ...] = (
    DiskSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.temperature,
    ),
    DiskSensorDescription(
        key="power_on_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.power_on_hours,
    ),
    DiskSensorDescription(
        key="bad_sectors",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.bad_sectors,
    ),
    DiskSensorDescription(
        key="uncorrectable_sectors",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.uncorrectable_sectors,
    ),
    DiskSensorDescription(
        # Schlüssel bleibt health_score (unique_id), angezeigt wird „Gut“/„Prüfen“ statt der nackten Zahl
        key="health_score",
        device_class=SensorDeviceClass.ENUM,
        options=HEALTH_STATES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=disk_health,
        attributes_fn=lambda d: (
            {"score": f"{d.health_score} / {HEALTH_SCORE_MAX}"} if d.health_score is not None else {}
        ),
    ),
    DiskSensorDescription(
        key="disk_read",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.read_kbps,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DiskSensorDescription(
        key="disk_write",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.write_kbps,
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.KILOBYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: UnasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(UnasSensor(coordinator, description) for description in SENSORS)

    def _disk_entities(keys: set[str]) -> list[Entity]:
        disks = disks_for(coordinator.data, keys)
        return [UnasDiskSensor(coordinator, disk, description) for disk in disks for description in DISK_SENSORS]

    def _drive_entities(ids: set[str]) -> list[Entity]:
        return [DriveUsageSensor(coordinator, drive_id) for drive_id in ids]

    entry.async_on_unload(track_new_items(coordinator, disk_keys, _disk_entities, async_add_entities))
    entry.async_on_unload(track_new_items(coordinator, lambda d: d.drives.keys(), _drive_entities, async_add_entities))


class UnasSensor(UnasEntity, SensorEntity):
    entity_description: UnasSensorDescription

    def __init__(self, coordinator: UnasCoordinator, description: UnasSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator.data)


class UnasDiskSensor(UnasDiskEntity, SensorEntity):
    entity_description: DiskSensorDescription

    def __init__(self, coordinator: UnasCoordinator, disk: Disk, description: DiskSensorDescription) -> None:
        super().__init__(coordinator, disk, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        disk = self.disk
        return self.entity_description.value_fn(disk) if disk else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        disk = self.disk
        attributes_fn = self.entity_description.attributes_fn
        return attributes_fn(disk) if disk and attributes_fn else None


class DriveUsageSensor(UnasEntity, SensorEntity):
    """Belegung eines Drive-Laufwerks (geteilt oder persönlich), z. B. „TimeMachine belegt“."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UnasCoordinator, drive_id: str) -> None:
        super().__init__(coordinator, f"drive_{drive_id}")
        drive = coordinator.data.drives[drive_id]
        self._drive_id = drive_id
        self._attr_translation_key = "drive_usage"
        self._attr_translation_placeholders = {"drive": drive.name}

    @property
    def available(self) -> bool:
        return super().available and self._drive_id in self.coordinator.data.drives

    @property
    def native_value(self) -> int | None:
        drive = self.coordinator.data.drives.get(self._drive_id)
        return drive.usage if drive else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        drive = self.coordinator.data.drives.get(self._drive_id)
        if drive is None:
            return {}
        return {
            "type": drive.type,
            # Kontingent in Bytes, None = ohne Begrenzung
            "quota": drive.quota,
            "members": drive.members,
            "snapshots": drive.snapshot_enabled,
            "remote_backup": drive.remote_backup_enabled,
        }
