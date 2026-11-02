"""Problem-Melder für Speicher und Festplatten, Zustand der Dateidienste."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Disk, Pool
from .coordinator import UnasConfigEntry, UnasCoordinator
from .entity import UnasDiskEntity, UnasEntity, disk_keys, disks_for, track_new_items
from .sensor import pool_state

PARALLEL_UPDATES = 0

# Drive-Dienst → Schlüssel der Entität
SERVICES = {"smb": "smb", "time-machine": "time_machine", "nfs": "nfs"}


async def async_setup_entry(
    hass: HomeAssistant, entry: UnasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[Entity] = [StorageProblem(coordinator)]
    entities += [ServiceRunning(coordinator, service) for service in SERVICES if service in coordinator.data.services]
    async_add_entities(entities)

    def _disk_entities(keys: set[str]) -> list[Entity]:
        return [DiskProblem(coordinator, d) for d in disks_for(coordinator.data, keys)]

    def _pool_entities(ids: set[str]) -> list[Entity]:
        return [PoolProblem(coordinator, pool_id) for pool_id in ids]

    entry.async_on_unload(track_new_items(coordinator, disk_keys, _disk_entities, async_add_entities))
    entry.async_on_unload(track_new_items(coordinator, lambda d: d.pools.keys(), _pool_entities, async_add_entities))


class PoolProblem(UnasEntity, BinarySensorEntity):
    """One per storage pool: on when the RAID is damaged (a disk failed or is missing), unknown for unknown states."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: UnasCoordinator, pool_id: str) -> None:
        super().__init__(coordinator, f"pool_{pool_id}_problem")
        self._pool_id = pool_id
        self._attr_translation_key = "pool_problem"
        self._attr_translation_placeholders = {"pool": str(coordinator.data.pools[pool_id].number)}

    @property
    def pool(self) -> Pool | None:
        return self.coordinator.data.pools.get(self._pool_id)

    @property
    def available(self) -> bool:
        return super().available and self.pool is not None

    @property
    def is_on(self) -> bool | None:
        pool = self.pool
        return pool.damaged if pool else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pool = self.pool
        if pool is None:
            return {}
        return {
            # Raw status from UniFi Drive, e.g. "fullyOperational"
            "status": pool.status,
            "raid": pool.raid_level,
            # How many disks may still fail now, and how many the RAID is set up for
            "redundancy": {"current": pool.protection, "expected": pool.expected_protection},
            "disks": {
                d.slot: d.state
                for d in sorted(self.coordinator.data.disks.values(), key=lambda d: d.slot)
                if d.pool_id == pool.id
            },
        }


class StorageProblem(UnasEntity, BinarySensorEntity):
    """An, sobald ein Speicher-Pool nicht voll funktionsfähig ist (degradiert, Wiederaufbau, Fehler, ohne Redundanz)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: UnasCoordinator) -> None:
        super().__init__(coordinator, "storage_problem")

    @property
    def is_on(self) -> bool | None:
        pools = self.coordinator.data.pools.values()
        return any(pool_state(p.status) != "ok" or p.redundancy_lost for p in pools) if pools else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pools = self.coordinator.data.pools.values()
        return {
            "pools": {f"pool_{p.number}": p.status for p in pools},
            # Wie viele Platten je Pool noch ausfallen dürfen (eingerichtet: expected)
            "redundancy": {
                f"pool_{p.number}": {"raid": p.raid_level, "current": p.protection, "expected": p.expected_protection}
                for p in pools
            },
        }


class DiskProblem(UnasDiskEntity, BinarySensorEntity):
    """An, wenn Drive die Platte nicht als „optimal“ führt oder Risiken meldet."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: UnasCoordinator, disk: Disk) -> None:
        super().__init__(coordinator, disk, "disk_problem")

    @property
    def is_on(self) -> bool | None:
        disk = self.disk
        if disk is None:
            return None
        return disk.at_risk

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disk = self.disk
        if disk is None:
            return {}
        return {"state": disk.state, "risk_reasons": list(disk.risk_reasons), "slot": disk.slot}


class ServiceRunning(UnasEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: UnasCoordinator, service: str) -> None:
        super().__init__(coordinator, SERVICES[service])
        self._service = service

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.services.get(self._service)
