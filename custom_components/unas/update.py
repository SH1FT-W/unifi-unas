"""Updates für UniFi OS (Firmware) und die Drive-App, wie in der UniFi-Oberfläche."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import UnasConfigEntry, UnasCoordinator
from .entity import UnasEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: UnasConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([FirmwareUpdate(coordinator), DriveUpdate(coordinator)])


class FirmwareUpdate(UnasEntity, UpdateEntity):
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: UnasCoordinator) -> None:
        super().__init__(coordinator, "firmware")

    @property
    def entity_picture(self) -> str | None:
        return self.coordinator.picture or super().entity_picture

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.data.system.firmware or None

    @property
    def latest_version(self) -> str | None:
        latest = self.coordinator.data.latest_firmware
        return latest.version if latest else self.installed_version

    @property
    def release_url(self) -> str | None:
        latest = self.coordinator.data.latest_firmware
        return latest.changelog_url if latest else None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        # Die UNAS lädt, installiert und startet neu; danach meldet sie die neue Version
        await self.coordinator.async_command(self.coordinator.client.update_firmware)


class DriveUpdate(UnasEntity, UpdateEntity):
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: UnasCoordinator) -> None:
        super().__init__(coordinator, "drive_app")

    @property
    def entity_picture(self) -> str | None:
        return self.coordinator.picture or super().entity_picture

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.data.system.drive_version or None

    @property
    def latest_version(self) -> str | None:
        return self.coordinator.data.drive_update or self.installed_version

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        await self.coordinator.async_command(self.coordinator.client.update_drive)
