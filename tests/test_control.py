"""Befehle: Lüfterprofil, Neustart, Ausschalten, Datenprüfung, SMART-Test, Updates."""

from __future__ import annotations

import aiohttp
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.unas.const import DOMAIN

from .conftest import BASE, CSRF, POOL_ID, V2, calls, mock_unas


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: entity_id}, blocking=True)


async def test_fan_profile(hass: HomeAssistant, setup: MockConfigEntry, unas: AiohttpClientMocker) -> None:
    await hass.services.async_call(
        "select", "select_option", {ATTR_ENTITY_ID: "select.unas_2_fan_profile", "option": "quiet"}, blocking=True
    )
    assert hass.states.get("select.unas_2_fan_profile").state == "quiet"
    [(body, headers)] = calls(unas, "PUT", "/systems/fan-control")
    assert body == {"profile": "quiet"}
    # Ändernde Anfragen brauchen Cookie und CSRF-Token aus der Anmeldung
    assert (headers["X-Csrf-Token"], headers["Cookie"]) == (CSRF, "TOKEN=tok")


@pytest.mark.parametrize(
    ("entity_id", "method", "path", "body"),
    [
        ("button.unas_2_restart", "POST", "/api/system/reboot", None),
        ("button.unas_2_start_data_scrubbing", "POST", f"/storage/pools/{POOL_ID}/data-scrubbing/start", None),
        ("button.unas_2_disk_1_run_quick_smart_test", "POST", "/storage/disks/1/smart-tests", {"type": "quick"}),
    ],
)
async def test_buttons(
    hass: HomeAssistant,
    setup: MockConfigEntry,
    unas: AiohttpClientMocker,
    entity_id: str,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    await _press(hass, entity_id)
    assert [b for b, _h in calls(unas, method, path)] == [body]


async def test_shutdown_once_enabled(hass: HomeAssistant, setup: MockConfigEntry, unas: AiohttpClientMocker) -> None:
    er.async_get(hass).async_update_entity("button.unas_2_shut_down", disabled_by=None)
    await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    await _press(hass, "button.unas_2_shut_down")
    assert len(calls(unas, "POST", "/api/system/poweroff")) == 1


async def test_command_error(hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/api/system/reboot", status=500)
    mock_unas(aioclient_mock, clear=False)
    with pytest.raises(HomeAssistantError, match="did not accept"):
        await _press(hass, "button.unas_2_restart")


async def test_command_without_admin_rights_no_reauth(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """403 bei einem Befehl = Konto ohne Admin-Rechte: klare Meldung, aber keine Frage nach dem Passwort."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/api/system/reboot", status=403)
    mock_unas(aioclient_mock, clear=False)
    with pytest.raises(HomeAssistantError, match="admin rights"):
        await _press(hass, "button.unas_2_restart")
    await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_command_login_rejected_starts_reauth(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Sitzung abgelaufen und neue Anmeldung abgelehnt (Passwort geändert): HA fragt nach neuen Zugangsdaten."""
    aioclient_mock.clear_requests()
    aioclient_mock.put(f"{V2}/systems/fan-control", status=401)
    mock_unas(aioclient_mock, clear=False, login_status=403)
    with pytest.raises(HomeAssistantError, match="rejected the login"):
        await hass.services.async_call(
            "select", "select_option", {ATTR_ENTITY_ID: "select.unas_2_fan_profile", "option": "cooling"}, blocking=True
        )
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_command_certificate_changed(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{BASE}/api/system/reboot", exc=aiohttp.ServerFingerprintMismatch(b"\xab" * 32, b"\xcd" * 32, "h", 443)
    )
    mock_unas(aioclient_mock, clear=False)
    with pytest.raises(HomeAssistantError, match="different certificate"):
        await _press(hass, "button.unas_2_restart")


async def test_updates(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    system = {
        "firmware": {
            "latest": {"version": "v6.1.2+abc", "_links": {"upload": [{"name": "changelog", "href": "https://x/c"}]}}
        },
        "apps": {"controllers": [{"name": "drive", "version": "4.4.17", "updateAvailable": "4.5.0"}]},
    }
    mock_unas(aioclient_mock, system=system)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="UNAS 2",
        unique_id="aa:bb:cc:dd:ee:ff",
        data={"host": "192.0.2.20", "username": "u", "password": "p"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    firmware = hass.states.get("update.unas_2_unifi_os")
    assert firmware.state == "on"
    assert (firmware.attributes["latest_version"], firmware.attributes["release_url"]) == ("6.1.2", "https://x/c")
    assert hass.states.get("update.unas_2_unifi_drive").attributes["latest_version"] == "4.5.0"

    for entity_id, path in (
        ("update.unas_2_unifi_os", "/api/firmware/update"),
        ("update.unas_2_unifi_drive", "/api/controllers/drive/update"),
    ):
        await hass.services.async_call("update", "install", {ATTR_ENTITY_ID: entity_id}, blocking=True)
        assert len(calls(aioclient_mock, "POST", path)) == 1
