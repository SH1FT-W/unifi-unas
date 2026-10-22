"""UniFi-OS-Einstellungen der UNAS (Display, Status-LED, Nachtmodus), Produktbild und Erkennung per DHCP/ARP."""

from __future__ import annotations

from typing import Any

import aiohttp
from homeassistant.config_entries import SOURCE_DHCP
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.unas.catalog import CATALOG_URL
from custom_components.unas.const import DOMAIN

from .conftest import BASE, ENTRY_DATA, HOST, MAC, calls, fixture, mock_unas

DISPLAY = "light.unas_2_display"
LED = "light.unas_2_status_led"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="UNAS 2", unique_id=MAC, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_display_led_night_mode(hass: HomeAssistant, setup: MockConfigEntry, unas: AiohttpClientMocker) -> None:
    display = hass.states.get(DISPLAY)
    # Keine Farbe (kein „Bezel“): Display mit Helligkeit, LED nur an/aus
    assert (
        display is not None and display.state == "on" and display.attributes["supported_color_modes"] == ["brightness"]
    )
    assert display.attributes["brightness"] == 204
    led = hass.states.get(LED)
    assert led is not None and led.state == "on" and led.attributes["supported_color_modes"] == ["onoff"]
    assert hass.states.get("time.unas_2_night_mode_start").state == "22:00:00"
    assert hass.states.get("time.unas_2_night_mode_end").state == "08:00:00"

    for service, entity_id, data in (
        ("light.turn_on", DISPLAY, {"brightness": 128}),
        ("light.turn_off", LED, {}),
        ("time.set_value", "time.unas_2_night_mode_end", {"time": "07:30:00"}),
    ):
        domain, name = service.split(".")
        await hass.services.async_call(domain, name, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True)
    assert [body for body, _headers in calls(unas, "PATCH", "/api/system")] == [
        {"lcmSettings": {"enabled": True, "brightness": 50}},
        {"ledSettings": {"enabled": False}},
        {"nightMode": {"onMinute": 1320, "offMinute": 450}},
    ]


async def test_without_display(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Ohne Display und ohne Admin-Rechte (keine Einstellungen in /api/system): keine Entitäten dafür."""
    system = {k: v for k, v in fixture("system").items() if k not in ("lcmSettings", "ledSettings", "nightMode")}
    mock_unas(aioclient_mock, system=system)
    await _setup(hass)
    for entity_id in (DISPLAY, LED, "time.unas_2_night_mode_start"):
        assert hass.states.get(entity_id) is None


async def test_product_picture(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    catalog: dict[str, Any] = {
        "devices": [{"sysid": "ea62", "icon": {"id": "icon-unas2w", "resolutions": [[257, 257], [512, 512]]}}]
    }
    aioclient_mock.get(CATALOG_URL, json=catalog)
    mock_unas(aioclient_mock, clear=False)
    await _setup(hass)
    for entity_id in ("update.unas_2_unifi_os", "update.unas_2_unifi_drive"):
        assert hass.states.get(entity_id).attributes["entity_picture"].endswith("/icon-unas2w_257x257.png")


async def test_product_picture_offline(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(CATALOG_URL, exc=aiohttp.ClientConnectionError("offline"))
    mock_unas(aioclient_mock, clear=False)
    await _setup(hass)
    # Ohne Katalog bleibt das Logo der Integration
    assert "icon-" not in str(hass.states.get("update.unas_2_unifi_os").attributes.get("entity_picture"))


DHCP = DhcpServiceInfo(ip=HOST, hostname="aabbccddeeff", macaddress="aabbccddeeff")


async def test_dhcp_finds_renamed_unas(hass: HomeAssistant, unas: AiohttpClientMocker) -> None:
    """Umbenannt (mDNS heißt nicht mehr „UNAS-…“): die DHCP/ARP-Erkennung findet sie trotzdem."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "zeroconf_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "ha-user", "password": "geheim"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == MAC


@pytest.mark.parametrize("system", [{"hardware": {"shortname": "UDR7"}, "mac": "AABBCCDDEEFF"}, None])
async def test_dhcp_other_ubiquiti_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, system: Any
) -> None:
    """Router, Access Points und Switches haben dieselben MAC-Präfixe: sie werden still aussortiert."""
    if system is None:
        aioclient_mock.get(f"{BASE}/api/system", status=404)
    else:
        mock_unas(aioclient_mock, system=system)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP)
    assert result["reason"] == "not_unas"


async def test_dhcp_known_unas_updates_host(hass: HomeAssistant, unas: AiohttpClientMocker) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data={**ENTRY_DATA, "host": "192.0.2.99"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP)
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == HOST
