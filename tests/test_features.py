"""Zertifikat-Pinning mit Reparatur, Speicher-Durchsatz, Zusammenfassungen, Zustand je Platte, Laufwerke, Redundanz."""

from __future__ import annotations

import copy
import hashlib
import ssl
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.components.repairs import DOMAIN as REPAIRS_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.unas.api import UnasClient, UnasError, format_fingerprint
from custom_components.unas.const import DOMAIN

from .conftest import ENTRY_DATA, FINGERPRINT, HOST, MAC, V1, V2, fixture, mock_unas

DISK1 = "unas_2_disk_1"
NEW_FINGERPRINT = "cd" * 32
# Echte Methode, bevor conftest sie für alle anderen Tests ersetzt
READ_FINGERPRINT = UnasClient.certificate_fingerprint


async def _tick(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, data: dict[str, Any] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="UNAS 2", unique_id=MAC, data=data or ENTRY_DATA)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _mismatch() -> aiohttp.ServerFingerprintMismatch:
    return aiohttp.ServerFingerprintMismatch(bytes.fromhex(FINGERPRINT), bytes.fromhex(NEW_FINGERPRINT), HOST, 443)


# --- Zertifikat ---------------------------------------------------------------------------------------------------


async def test_existing_entry_remembers_certificate(hass: HomeAssistant, unas: AiohttpClientMocker) -> None:
    """Einträge von vor dem Pinning merken sich beim nächsten Start das Zertifikat und nutzen es dann."""
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["fingerprint"] == FINGERPRINT
    assert entry.runtime_data.client.fingerprint == FINGERPRINT


async def test_certificate_unreadable_at_setup_retries(
    hass: HomeAssistant, unas: AiohttpClientMocker, read_fingerprint: AsyncMock
) -> None:
    read_fingerprint.side_effect = UnasError("weg")
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "fingerprint" not in entry.data


async def test_certificate_changed_creates_repair_and_fix_accepts_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, read_fingerprint: AsyncMock
) -> None:
    mock_unas(aioclient_mock)
    entry = await _setup(hass, {**ENTRY_DATA, "fingerprint": FINGERPRINT})
    issue_id = f"cert_changed_{entry.entry_id}"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{V2}/storage", exc=_mismatch())
    mock_unas(aioclient_mock, clear=False)
    await _tick(hass, entry)
    assert hass.states.get("sensor.unas_2_storage_used").state == STATE_UNAVAILABLE
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders == {
        "name": "UNAS 2",
        "old": format_fingerprint(FINGERPRINT),
        "new": format_fingerprint(NEW_FINGERPRINT),
    }

    # Reparatur: zeigt den Fingerabdruck, der jetzt anliegt, und übernimmt genau diesen
    read_fingerprint.return_value = NEW_FINGERPRINT
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    from custom_components.unas.repairs import async_create_fix_flow

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass = hass
    flow.issue_id = issue_id
    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert result["description_placeholders"]["new"] == format_fingerprint(NEW_FINGERPRINT)
    mock_unas(aioclient_mock)
    result = await flow.async_step_confirm({})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.data["fingerprint"] == NEW_FINGERPRINT
    assert entry.runtime_data.client.fingerprint == NEW_FINGERPRINT


async def test_certificate_ok_again_removes_repair(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_unas(aioclient_mock)
    entry = await _setup(hass, {**ENTRY_DATA, "fingerprint": FINGERPRINT})
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{V2}/systems/device-info", exc=_mismatch())
    mock_unas(aioclient_mock, clear=False)
    await _tick(hass, entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"cert_changed_{entry.entry_id}") is not None
    mock_unas(aioclient_mock)
    await _tick(hass, entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"cert_changed_{entry.entry_id}") is None


async def test_login_certificate_changed(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post("https://192.0.2.20/api/auth/login", exc=_mismatch())
    mock_unas(aioclient_mock, clear=False)
    entry = await _setup(hass, {**ENTRY_DATA, "fingerprint": FINGERPRINT})
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"cert_changed_{entry.entry_id}") is not None


@pytest.mark.parametrize(("remove_entry", "unreachable"), [(True, False), (False, True)])
async def test_repair_flow_aborts(
    hass: HomeAssistant, unas: AiohttpClientMocker, read_fingerprint: AsyncMock, remove_entry: bool, unreachable: bool
) -> None:
    from custom_components.unas.repairs import async_create_fix_flow

    entry = await _setup(hass, {**ENTRY_DATA, "fingerprint": FINGERPRINT})
    if remove_entry:
        await hass.config_entries.async_remove(entry.entry_id)
    if unreachable:
        read_fingerprint.side_effect = UnasError("weg")
    flow = await async_create_fix_flow(hass, "x", {"entry_id": entry.entry_id})
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] == "abort"
    assert result["reason"] == ("entry_removed" if remove_entry else "cannot_connect")


async def test_read_fingerprint_from_tls() -> None:
    """Liest das Zertifikat aus der TLS-Verbindung und gibt seinen SHA-256 zurück."""
    der = b"zertifikat"
    ssl_object = MagicMock()
    ssl_object.getpeercert.return_value = der
    writer = MagicMock()
    writer.get_extra_info.return_value = ssl_object
    client = UnasClient(MagicMock(), HOST, "", "")
    context = ssl.create_default_context()
    with patch("asyncio.open_connection", AsyncMock(return_value=(MagicMock(), writer))) as connect:
        fingerprint = await READ_FINGERPRINT(client, context)
    assert fingerprint == hashlib.sha256(der).hexdigest()
    assert connect.call_args.kwargs == {"ssl": context}
    writer.close.assert_called_once()


@pytest.mark.parametrize("failure", ["connect", "no_cert"])
async def test_read_fingerprint_failures(failure: str) -> None:
    writer = MagicMock()
    writer.get_extra_info.return_value = None
    connect = AsyncMock(side_effect=OSError("weg")) if failure == "connect" else AsyncMock(return_value=(None, writer))
    client = UnasClient(MagicMock(), HOST, "", "")
    with patch("asyncio.open_connection", connect), pytest.raises(UnasError):
        await READ_FINGERPRINT(client, ssl.create_default_context())


# --- Neue Werte -----------------------------------------------------------------------------------------------------


async def test_new_values(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    state = hass.states.get
    assert state("sensor.unas_2_disks_at_risk").state == "0"
    assert state("sensor.unas_2_hottest_disk").state == "50.0"
    # Letzter Wert der Reihe, KB/s → MB/s
    read = state("sensor.unas_2_storage_read")
    assert read.attributes["unit_of_measurement"] == "MB/s"
    assert round(float(read.state), 2) == 0.03
    assert round(float(state("sensor.unas_2_storage_write").state), 2) == 0.55
    health = state(f"sensor.{DISK1}_health")
    assert health.state == "good"
    assert health.attributes["score"] == "5 / 5"
    assert health.attributes["options"] == ["good", "check"]
    drive = state("sensor.unas_2_timemachine_used").attributes
    assert (drive["quota"], drive["members"], drive["remote_backup"]) == (None, 2, False)
    problem = state("binary_sensor.unas_2_storage_problem").attributes
    assert problem["redundancy"] == {"pool_1": {"raid": "raid1", "current": 1, "expected": 1}}


async def test_disk_at_risk_and_lost_redundancy(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    storage = copy.deepcopy(fixture("storage"))
    storage["disks"][0]["healthScore"] = 3
    storage["disks"][1]["riskReasons"] = ["highTemperature"]
    # Pool meldet „läuft“, hat aber keine Redundanz mehr (eine Platte im RAID 1 fehlt)
    storage["pools"][0]["raidGroups"][0]["currentProtection"] = 0
    mock_unas(aioclient_mock, storage=storage)
    await _tick(hass, setup)
    assert hass.states.get(f"sensor.{DISK1}_health").state == "check"
    assert hass.states.get(f"sensor.{DISK1}_health").attributes["score"] == "3 / 5"
    assert hass.states.get("sensor.unas_2_disks_at_risk").state == "1"
    assert hass.states.get("sensor.unas_2_storage_status").state == "ok"
    assert hass.states.get("binary_sensor.unas_2_storage_problem").state == STATE_ON


async def test_quota_and_missing_values(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    drives = copy.deepcopy(fixture("drives"))
    drives["drives"][3]["quota"] = 500_000_000_000
    drives["drives"][3]["protections"]["remoteBackupEnabled"] = True
    storage = copy.deepcopy(fixture("storage"))
    for disk in storage["disks"]:
        disk["temperature"] = None
        disk["healthScore"] = None
    del storage["pools"][0]["raidGroups"]
    mock_unas(aioclient_mock, drives=drives, storage=storage)
    await _tick(hass, setup)
    drive = hass.states.get("sensor.unas_2_timemachine_used").attributes
    assert (drive["quota"], drive["remote_backup"]) == (500_000_000_000, True)
    assert hass.states.get("sensor.unas_2_hottest_disk").state == "unknown"
    assert hass.states.get(f"sensor.{DISK1}_health").state == "unknown"
    assert "score" not in hass.states.get(f"sensor.{DISK1}_health").attributes
    assert hass.states.get("binary_sensor.unas_2_storage_problem").state == STATE_OFF


async def test_empty_bay_is_no_disk(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    storage = copy.deepcopy(fixture("storage"))
    storage["disks"][1] = {"slotId": "2", "state": "empty", "temperature": 0, "size": 0}
    mock_unas(aioclient_mock, storage=storage)
    await _setup(hass)
    assert hass.states.get("sensor.unas_2_disk_2_temperature") is None
    assert hass.states.get("sensor.unas_2_disks_at_risk").state == "0"


@pytest.mark.parametrize("response", [{"status": 403}, {"json": {"err": None, "data": None}}])
async def test_storage_throughput_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, response: dict[str, Any]
) -> None:
    """Liefert die UNAS keinen Durchsatz, bleibt der Rest; die zwei Sensoren sind unbekannt."""
    aioclient_mock.get(f"{V1}/systems/storage-io-stats?interval=300", **response)
    mock_unas(aioclient_mock, clear=False)
    await _setup(hass)
    assert hass.states.get("sensor.unas_2_storage_read").state == "unknown"
    assert hass.states.get("sensor.unas_2_storage_used").state != STATE_UNAVAILABLE
