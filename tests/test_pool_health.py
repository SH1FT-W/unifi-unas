"""Storage pool health: one problem sensor per pool and a repair notice while a pool is damaged."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import patch

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.unas.api import parse_disk, parse_pool
from custom_components.unas.const import DOMAIN

from .conftest import ENTRY_DATA, MAC, POOL_ID, fixture, mock_unas

POOL = "binary_sensor.unas_2_storage_pool_1"
POOL2_ID = "0d6b3c1e-2f57-4a8e-9c41-7b1f2e3d4c5a"


async def _tick(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def _issue_id(entry: MockConfigEntry, pool_id: str = POOL_ID) -> str:
    return f"pool_damaged_{entry.entry_id}_{pool_id}"


def _issue(hass: HomeAssistant, entry: MockConfigEntry, pool_id: str = POOL_ID) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(entry, pool_id))


def _storage(*, protection: int = 1, status: str = "fullyOperational", **disk2: Any) -> dict[str, Any]:
    storage = copy.deepcopy(fixture("storage"))
    pool = storage["pools"][0]
    pool["status"] = status
    pool["raidGroups"][0]["currentProtection"] = protection
    storage["disks"][1].update(disk2)
    return storage


def _second_pool(storage: dict[str, Any]) -> dict[str, Any]:
    pool = copy.deepcopy(storage["pools"][0])
    pool.update(id=POOL2_ID, number=2, activeRaidGroupId="g2")
    pool["raidGroups"][0]["id"] = "g2"
    storage["pools"].append(pool)
    return storage


async def test_healthy_pool(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    state = hass.states.get(POOL)
    assert state.state == STATE_OFF
    assert state.attributes["device_class"] == "problem"
    assert state.attributes["friendly_name"] == "UNAS 2 Storage pool 1"
    assert state.attributes["status"] == "fullyOperational"
    assert state.attributes["raid"] == "raid1"
    assert state.attributes["redundancy"] == {"current": 1, "expected": 1}
    assert state.attributes["disks"] == {"1": "optimal", "2": "optimal"}
    assert _issue(hass, setup) is None


async def test_failed_disk_raises_repair_until_pool_is_healthy(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    # Disk 2 is gone (empty bay), the RAID 1 can no longer lose a disk
    mock_unas(aioclient_mock, storage=_storage(protection=0, state="empty"))
    await _tick(hass, setup)
    assert hass.states.get(POOL).state == STATE_ON
    assert hass.states.get(POOL).attributes["disks"] == {"1": "optimal"}
    issue = _issue(hass, setup)
    assert issue is not None
    assert (issue.translation_key, issue.severity, issue.is_persistent, issue.is_fixable) == (
        "pool_damaged",
        ir.IssueSeverity.ERROR,
        False,
        False,
    )
    assert issue.translation_placeholders == {
        "name": "UNAS 2",
        "pool": "1",
        "raid": "RAID1",
        "current": "0",
        "expected": "1",
    }

    # Disk replaced and RAID rebuilt: the notice goes away by itself
    mock_unas(aioclient_mock)
    await _tick(hass, setup)
    assert hass.states.get(POOL).state == STATE_OFF
    assert _issue(hass, setup) is None


async def test_repair_names_affected_disks(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_unas(aioclient_mock, storage=_storage(protection=0, state="faulty"))
    await _tick(hass, setup)
    issue = _issue(hass, setup)
    assert issue is not None
    assert issue.translation_key == "pool_damaged_disks"
    assert issue.translation_placeholders["disks"] == "2 (faulty)"
    # The disk's own problem sensor reports it as well
    assert hass.states.get("binary_sensor.unas_2_disk_2_problem").state == STATE_ON


async def test_unknown_status_is_no_alarm(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A status that UniFi Drive never showed us stays unknown and is logged once, instead of raising an alarm."""
    mock_unas(aioclient_mock, storage=_storage(status="someNewState"))
    with patch("custom_components.unas.coordinator._LOGGER.debug") as debug:
        await _tick(hass, setup)
        await _tick(hass, setup)
    assert hass.states.get(POOL).state == STATE_UNKNOWN
    assert hass.states.get(POOL).attributes["status"] == "someNewState"
    assert _issue(hass, setup) is None
    unknown = [c for c in debug.call_args_list if "unknown status" in c.args[0]]
    assert len(unknown) == 1
    assert unknown[0].args[1:] == (1, "someNewState")

    # Lost redundancy is a confirmed problem, whatever the status says
    mock_unas(aioclient_mock, storage=_storage(status="someNewState", protection=0))
    await _tick(hass, setup)
    assert hass.states.get(POOL).state == STATE_ON
    assert _issue(hass, setup) is not None

    # Back to an unknown state: no all-clear, the notice stays until the pool is known to be healthy
    mock_unas(aioclient_mock, storage=_storage(status="someNewState"))
    await _tick(hass, setup)
    assert hass.states.get(POOL).state == STATE_UNKNOWN
    assert _issue(hass, setup) is not None
    mock_unas(aioclient_mock)
    await _tick(hass, setup)
    assert _issue(hass, setup) is None


async def test_pool_added_and_removed_at_runtime(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    storage = _second_pool(_storage())
    storage["pools"][1]["raidGroups"][0]["currentProtection"] = 0
    mock_unas(aioclient_mock, storage=storage)
    await _tick(hass, setup)
    assert hass.states.get("binary_sensor.unas_2_storage_pool_2").state == STATE_ON
    assert hass.states.get(POOL).state == STATE_OFF
    assert _issue(hass, setup, POOL2_ID) is not None
    assert _issue(hass, setup) is None

    # Pool 2 deleted: its sensor is unavailable and its notice goes away
    mock_unas(aioclient_mock)
    await _tick(hass, setup)
    assert hass.states.get("binary_sensor.unas_2_storage_pool_2").state == STATE_UNAVAILABLE
    assert _issue(hass, setup, POOL2_ID) is None


async def test_stale_pool_repair_removed_after_reload(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Non-persistent notices survive a reload; the first poll removes those of healthy or deleted pools."""
    mock_unas(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, title="UNAS 2", unique_id=MAC, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    for pool_id in (POOL_ID, POOL2_ID):
        ir.async_create_issue(
            hass,
            DOMAIN,
            _issue_id(entry, pool_id),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="pool_damaged",
        )
    # A notice of another entry stays
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"pool_damaged_other_{POOL_ID}",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="pool_damaged",
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None
    assert _issue(hass, entry, POOL2_ID) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"pool_damaged_other_{POOL_ID}") is not None


def test_pool_damaged_from_api_values() -> None:
    raw = fixture("storage")["pools"][0]
    assert parse_pool(raw).damaged is False
    assert parse_pool({**raw, "status": "degraded"}).damaged is None
    no_groups = {k: v for k, v in raw.items() if k != "raidGroups"}
    assert parse_pool(no_groups).damaged is False
    assert parse_pool({**no_groups, "status": "other"}).damaged is None
    lost = copy.deepcopy(raw)
    lost["raidGroups"][0]["currentProtection"] = 0
    assert parse_pool(lost).damaged is True


def test_disk_pool_id() -> None:
    raw = fixture("storage")["disks"][0]
    assert parse_disk(raw).pool_id == POOL_ID
    assert parse_disk({k: v for k, v in raw.items() if k != "poolId"}).pool_id == ""


async def test_missing_pools_keep_notice(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A poll without pools (e.g. while Drive starts up) is no all-clear."""
    mock_unas(aioclient_mock, storage=_storage(protection=0))
    await _tick(hass, setup)
    assert _issue(hass, setup) is not None
    mock_unas(aioclient_mock, storage={"pools": [], "disks": []})
    await _tick(hass, setup)
    assert _issue(hass, setup) is not None


async def test_damaged_pool_keeps_notice_across_reload_and_removal_clears_it(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_unas(aioclient_mock, storage=_storage(protection=0))
    await _tick(hass, setup)
    await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, setup) is not None
    # Removing the UNAS removes its notice right away, not only at the next restart
    await hass.config_entries.async_remove(setup.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, setup) is None
