"""Einrichtung, Geräte, Werte, Ausfall und neue/entnommene Festplatten."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse
from pytest_homeassistant_custom_component.typing import WebSocketGenerator
from yarl import URL

from custom_components.unas.const import DOMAIN

from .conftest import BASE, ENTRY_DATA, MAC, V2, calls, fixture, mock_unas

# Tests laufen auf Englisch
DISK1 = "unas_2_disk_1"


async def _tick(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_devices_and_values(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    dev_reg = dr.async_get(hass)
    unas = dev_reg.async_get_device_by_identifier((DOMAIN, MAC), setup.entry_id)
    assert unas is not None
    assert (unas.name, unas.model, unas.sw_version, unas.manufacturer) == ("UNAS 2", "UNAS2W", "6.0.11", "Ubiquiti")
    disk = dev_reg.async_get_device_by_identifier((DOMAIN, f"{MAC}_disk_SERIAL1"), setup.entry_id)
    assert disk is not None and disk.via_device_id == unas.id
    assert (disk.name, disk.model, disk.serial_number) == ("UNAS 2 disk 1", "ST3000VN007-2E4166", "SERIAL1")

    state = hass.states.get
    assert round(float(state("sensor.unas_2_storage_used").state), 1) == 539.6
    assert state("sensor.unas_2_storage_used").attributes["unit_of_measurement"] == "GB"
    assert state("sensor.unas_2_storage_usage").state == "18.0"
    assert state("sensor.unas_2_storage_status").state == "ok"
    assert state("sensor.unas_2_cpu_temperature").state == "67.7"
    assert state("sensor.unas_2_cpu_load").state == "10.7"
    assert state("sensor.unas_2_link_speed").state == "GbE"
    assert state("sensor.unas_2_network_receive").attributes["unit_of_measurement"] == "Mbit/s"
    assert state(f"sensor.{DISK1}_temperature").state == "47.0"
    assert state(f"sensor.{DISK1}_power_on_hours").state == "402"
    assert state(f"binary_sensor.{DISK1}_problem").state == STATE_OFF
    assert state("binary_sensor.unas_2_storage_problem").state == STATE_OFF
    assert state("binary_sensor.unas_2_smb").state == STATE_ON
    assert state("binary_sensor.unas_2_nfs").state == STATE_OFF
    assert state("sensor.unas_2_timemachine_used").attributes["type"] == "shared"
    assert state("select.unas_2_fan_profile").state == "default"
    assert state("select.unas_2_fan_profile").attributes["options"] == ["quiet", "default", "cooling"]
    assert state("update.unas_2_unifi_os").state == STATE_OFF
    assert state("update.unas_2_unifi_drive").attributes["installed_version"] == "4.4.17"


async def test_shutdown_and_rates_disabled_by_default(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    ent_reg = er.async_get(hass)
    for entity_id in ("button.unas_2_shut_down", f"sensor.{DISK1}_read_rate", "sensor.unas_2_unifi_drive_version"):
        entry = ent_reg.async_get(entity_id)
        assert entry is not None and entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_degraded_pool_and_disk(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    storage = copy.deepcopy(fixture("storage"))
    storage["pools"][0]["status"] = "degraded"
    storage["disks"][0]["state"] = "attentionRequired"
    storage["disks"][0]["riskReasons"] = ["badSector"]
    mock_unas(aioclient_mock, storage=storage)
    await _tick(hass, setup)
    assert hass.states.get("sensor.unas_2_storage_status").state == "degraded"
    assert hass.states.get("binary_sensor.unas_2_storage_problem").state == STATE_ON
    problem = hass.states.get(f"binary_sensor.{DISK1}_problem")
    assert problem.state == STATE_ON and problem.attributes["risk_reasons"] == ["badSector"]


async def test_unreachable_and_back(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{V2}/systems/device-info", status=502)
    await _tick(hass, setup)
    assert hass.states.get("sensor.unas_2_storage_used").state == STATE_UNAVAILABLE
    mock_unas(aioclient_mock)
    await _tick(hass, setup)
    assert hass.states.get("sensor.unas_2_storage_used").state != STATE_UNAVAILABLE


async def test_session_still_rejected_after_relogin_is_unavailable(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """401 trotz frischer Anmeldung (Drive fährt nach Neustart/Update noch hoch): nicht verfügbar, kein Reauth."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{V2}/systems/device-info", status=401)
    mock_unas(aioclient_mock, clear=False)
    await _tick(hass, setup)
    assert len(calls(aioclient_mock, "POST", "/api/auth/login")) == 1
    assert hass.states.get("sensor.unas_2_storage_used").state == STATE_UNAVAILABLE
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_login_rejected_starts_reauth(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Sitzung abgelaufen und die neue Anmeldung wird abgelehnt (Passwort geändert): HA fragt nach neuen Daten."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{V2}/systems/device-info", status=401)
    mock_unas(aioclient_mock, clear=False, login_status=403)
    await _tick(hass, setup)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_redirect_while_booting_is_unavailable(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Beim Hochfahren leitet UniFi OS auf seine Oberfläche um: nicht verfügbar, kein Reauth."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{V2}/storage", status=302, headers={"Location": "/login"})
    mock_unas(aioclient_mock, clear=False)
    await _tick(hass, setup)
    assert hass.states.get("sensor.unas_2_storage_used").state == STATE_UNAVAILABLE
    assert "HTTP 302" in str(setup.runtime_data.last_exception)
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_new_disk_and_removed_disk(
    hass: HomeAssistant,
    setup: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
) -> None:
    storage = copy.deepcopy(fixture("storage"))
    storage["disks"][1]["serial"] = "SERIAL9"  # Platte 2 getauscht
    mock_unas(aioclient_mock, storage=storage)
    await _tick(hass, setup)
    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get_device_by_identifier((DOMAIN, f"{MAC}_disk_SERIAL9"), setup.entry_id) is not None
    assert hass.states.get("sensor.unas_2_disk_2_temperature_2").state == "50.0"
    assert hass.states.get("sensor.unas_2_disk_2_temperature").state == STATE_UNAVAILABLE

    # Die alte Platte darf aus HA entfernt werden, die UNAS und eingebaute Platten nicht
    old = dev_reg.async_get_device_by_identifier((DOMAIN, f"{MAC}_disk_SERIAL2"), setup.entry_id)
    unas = dev_reg.async_get_device_by_identifier((DOMAIN, MAC), setup.entry_id)
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)
    for device, allowed in ((old, True), (unas, False)):
        await client.send_json_auto_id(
            {
                "type": "config/device_registry/remove_config_entry",
                "config_entry_id": setup.entry_id,
                "device_id": device.id,
            }
        )
        response = await client.receive_json()
        assert response["success"] is allowed, response


async def test_unload(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(setup.entry_id)
    assert hass.states.get("sensor.unas_2_storage_used").state == STATE_UNAVAILABLE


async def test_parallel_401_logs_in_once(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Alle fünf Abfragen laufen parallel in ein 401 (Sitzung abgelaufen): genau eine neue Anmeldung."""
    first_calls: set[str] = set()

    def expire_once(name: str) -> Any:
        async def _side_effect(method: str, url: URL, _data: Any) -> AiohttpClientMockResponse:
            if name not in first_calls:
                first_calls.add(name)
                await asyncio.sleep(0)  # Antwort kommt erst, wenn alle fünf Anfragen unterwegs sind
                return AiohttpClientMockResponse(method, url, status=401)
            return AiohttpClientMockResponse(method, url, json=fixture(name))

        return _side_effect

    aioclient_mock.clear_requests()
    for name, path in (
        ("device_info", "/systems/device-info"),
        ("storage", "/storage"),
        ("drives", "/drives"),
        ("network_io", "/systems/network-io"),
        ("fan_control", "/systems/fan-control"),
    ):
        aioclient_mock.get(f"{V2}{path}", side_effect=expire_once(name))
    mock_unas(aioclient_mock, clear=False)
    await _tick(hass, setup)
    assert len(first_calls) == 5
    assert len(calls(aioclient_mock, "POST", "/api/auth/login")) == 1
    assert hass.states.get("sensor.unas_2_storage_used").state != STATE_UNAVAILABLE


async def test_disks_without_serial_are_kept_apart(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Melden Platten keine Seriennummer, zählt der Schacht: je Platte eigenes Gerät und eigene Werte."""
    storage = copy.deepcopy(fixture("storage"))
    for disk in storage["disks"]:
        disk["serial"] = ""
    second = storage["disks"].pop()
    mock_unas(aioclient_mock, storage=storage)
    entry = MockConfigEntry(domain=DOMAIN, title="UNAS 2", unique_id=MAC, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Zweite Platte ohne Seriennummer kommt im Betrieb dazu
    storage["disks"].append(second)
    mock_unas(aioclient_mock, storage=storage)
    await _tick(hass, entry)
    dev_reg = dr.async_get(hass)
    for slot in ("1", "2"):
        device = dev_reg.async_get_device_by_identifier((DOMAIN, f"{MAC}_disk_{slot}"), entry.entry_id)
        assert device is not None and device.serial_number is None
    assert hass.states.get(f"sensor.{DISK1}_temperature").state == "47.0"
    assert hass.states.get("sensor.unas_2_disk_2_temperature").state == "50.0"
    assert hass.states.get("sensor.unas_2_disk_2_temperature_2") is None
    assert hass.states.get("binary_sensor.unas_2_disk_2_problem").state == STATE_OFF
    assert hass.states.get("button.unas_2_disk_2_run_quick_smart_test") is not None

    # Platte 2 ausgebaut: ihre Werte sind nicht verfügbar, Platte 1 bleibt
    storage["disks"].pop()
    mock_unas(aioclient_mock, storage=storage)
    await _tick(hass, entry)
    assert hass.states.get("sensor.unas_2_disk_2_temperature").state == STATE_UNAVAILABLE
    assert hass.states.get(f"sensor.{DISK1}_temperature").state == "47.0"


async def test_system_endpoint_forbidden_keeps_fast_values(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Konto ohne Admin-Rechte: /api/system scheitert, Werte und Update-Entitäten bleiben (ohne neue Version)."""
    mock_unas(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/system", status=403)
    mock_unas(aioclient_mock, clear=False)
    entry = MockConfigEntry(domain=DOMAIN, title="UNAS 2", unique_id=MAC, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.unas_2_storage_used").state != STATE_UNAVAILABLE
    firmware = hass.states.get("update.unas_2_unifi_os")
    assert firmware.state == STATE_OFF
    assert firmware.attributes["latest_version"] == firmware.attributes["installed_version"] == "6.0.11"
    assert firmware.attributes["release_url"] is None
    assert entry.runtime_data.data.drive_update is None


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"exc": TimeoutError()}, "cannot connect"),
        ({"exc": aiohttp.ClientConnectionError("kaputt")}, "cannot connect"),
        ({"text": "<html>Anmeldeseite</html>"}, "no JSON response"),
    ],
)
async def test_request_failures(
    hass: HomeAssistant,
    setup: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    message: str,
) -> None:
    """Zeitüberschreitung, Verbindungsfehler und Nicht-JSON-Antworten machen die Werte nicht verfügbar."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{V2}/storage", **response)
    mock_unas(aioclient_mock, clear=False)
    await _tick(hass, setup)
    assert hass.states.get("sensor.unas_2_storage_used").state == STATE_UNAVAILABLE
    assert message in str(setup.runtime_data.last_exception)


@pytest.mark.parametrize("exc", [TimeoutError(), aiohttp.ClientConnectionError("kaputt")])
async def test_login_unreachable(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, exc: Exception) -> None:
    mock_unas(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/api/auth/login", exc=exc)
    mock_unas(aioclient_mock, clear=False)
    entry = MockConfigEntry(domain=DOMAIN, title="UNAS 2", unique_id=MAC, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
