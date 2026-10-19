"""Einrichtung, Neu anmelden, Neu konfigurieren."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.unas.api import UnasError
from custom_components.unas.const import DOMAIN

from .conftest import BASE, ENTRY_DATA, FINGERPRINT, HOST, MAC, V2, fixture, mock_unas


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return str(result["flow_id"])


async def test_user_flow(hass: HomeAssistant, unas: AiohttpClientMocker) -> None:
    result = await hass.config_entries.flow.async_configure(await _start(hass), ENTRY_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "UNAS 2"
    assert result["data"] == {**ENTRY_DATA, "fingerprint": FINGERPRINT}
    assert result["result"].unique_id == MAC


@pytest.mark.parametrize(
    ("login_status", "error"),
    [(403, "invalid_auth"), (500, "cannot_connect")],
)
async def test_user_flow_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login_status: int, error: str
) -> None:
    mock_unas(aioclient_mock, login_status=login_status)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_unas(aioclient_mock)  # danach klappt es
    result = await hass.config_entries.flow.async_configure(flow_id, ENTRY_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_not_a_unas(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_unas(aioclient_mock, device_info={"name": "Irgendwas"})
    result = await hass.config_entries.flow.async_configure(await _start(hass), ENTRY_DATA)
    assert result["errors"] == {"base": "not_unas"}


async def test_already_configured_updates_host(hass: HomeAssistant, unas: AiohttpClientMocker) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data={**ENTRY_DATA, "host": "192.0.2.99"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure(await _start(hass), ENTRY_DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == ENTRY_DATA["host"]


async def test_reauth(hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker) -> None:
    result = await setup.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_unas(aioclient_mock, login_status=403)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "ha-user", "password": "falsch"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_unas(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "ha-user", "password": "neu"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup.data["password"] == "neu"
    await hass.async_block_till_done()  # Neuladen abwarten, dann sauber entladen
    await hass.config_entries.async_unload(setup.entry_id)


async def test_reconfigure(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker, read_fingerprint: AsyncMock
) -> None:
    result = await setup.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    # Unter der Adresse antwortet eine andere UNAS
    other = fixture("device_info")
    other["networkInterfaces"][0]["mac"] = "11:22:33:44:55:66"
    mock_unas(aioclient_mock, device_info=other)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["reason"] == "wrong_device"

    result = await setup.start_reconfigure_flow(hass)
    mock_unas(aioclient_mock, login_status=500)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["errors"] == {"base": "cannot_connect"}
    mock_unas(aioclient_mock)
    # Neu aufgesetzte UNAS: das Zertifikat wird beim Neu konfigurieren neu gemerkt
    read_fingerprint.return_value = "cd" * 32
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**ENTRY_DATA, "password": "neu"})
    assert result["reason"] == "reconfigure_successful"
    assert setup.data["password"] == "neu"
    assert setup.data["fingerprint"] == "cd" * 32
    await hass.async_block_till_done()  # Neuladen abwarten, dann sauber entladen
    await hass.config_entries.async_unload(setup.entry_id)


async def test_v1_error_is_ignored(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Ein Dienst, den die Firmware nicht kennt, darf die Einrichtung nicht verhindern."""
    aioclient_mock.get(f"{BASE}/proxy/drive/api/v1/services/nfs/settings", json={"err": {"msg": "nope"}})
    mock_unas(aioclient_mock, clear=False)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.unas_2_nfs") is None
    assert hass.states.get("binary_sensor.unas_2_smb") is not None
    assert V2  # gleiche Basis wie in den anderen Tests


def _zeroconf(name: str = "UNAS-2._smb._tcp.local.") -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address(HOST),
        ip_addresses=[ip_address(HOST)],
        hostname="UNAS-2.local.",
        name=name,
        port=445,
        type="_smb._tcp.local.",
        properties={},
    )


async def test_zeroconf(hass: HomeAssistant, unas: AiohttpClientMocker) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf())
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {"name": "UNAS 2", "host": HOST}

    mock_unas(unas, login_status=403)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "ha-user", "password": "falsch"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    mock_unas(unas)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "ha-user", "password": "geheim"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {**ENTRY_DATA, "fingerprint": FINGERPRINT}
    assert result["result"].unique_id == MAC


async def test_zeroconf_known_unas_updates_host(hass: HomeAssistant, unas: AiohttpClientMocker) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data={**ENTRY_DATA, "host": "192.0.2.99"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf())
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == HOST


@pytest.mark.parametrize("system", [{"hardware": {"shortname": "UDR7"}, "mac": "AABBCCDDEEFF"}, None])
async def test_zeroconf_not_unas(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, system: Any) -> None:
    if system is None:
        aioclient_mock.get(f"{BASE}/api/system", status=404)
    else:
        mock_unas(aioclient_mock, system=system)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf())
    assert result["reason"] == "not_unas"


async def test_user_flow_verify_ssl(hass: HomeAssistant, unas: AiohttpClientMocker) -> None:
    """„Zertifikat prüfen“ landet in den Optionen; der Client nutzt dann HAs prüfende Session."""
    with patch("custom_components.unas.async_get_clientsession", wraps=async_get_clientsession) as session:
        result = await hass.config_entries.flow.async_configure(await _start(hass), {**ENTRY_DATA, "verify_ssl": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA
    assert result["options"] == {"verify_ssl": True}
    assert session.call_args.kwargs == {"verify_ssl": True}
    await hass.async_block_till_done()
    assert result["result"].runtime_data.client.verify_ssl is True
    assert result["result"].runtime_data.client.fingerprint is None


async def test_options_flow_verify_ssl(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    assert setup.runtime_data.client.verify_ssl is False
    assert setup.runtime_data.client.fingerprint == FINGERPRINT
    result = await hass.config_entries.options.async_init(setup.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"

    # Mit Prüfung lehnt die UNAS ab (hier: Anmeldung schlägt fehl) → Fehler, Option bleibt aus
    mock_unas(aioclient_mock, login_status=500)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"verify_ssl": True})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert setup.options == {}

    mock_unas(aioclient_mock)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"verify_ssl": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # Neuladen mit der neuen Option
    assert setup.options == {"verify_ssl": True}
    assert setup.runtime_data.client.verify_ssl is True
    assert setup.runtime_data.client.fingerprint is None

    # Wieder aus: es gilt wieder das gemerkte Zertifikat
    result = await hass.config_entries.options.async_init(setup.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"verify_ssl": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup.runtime_data.client.fingerprint == FINGERPRINT


async def test_user_flow_account_without_drive_access(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Anmeldung klappt, Drive verweigert (403): eigener Hinweis statt „Passwort falsch“."""
    aioclient_mock.get(f"{V2}/storage", status=403)
    mock_unas(aioclient_mock, clear=False)
    result = await hass.config_entries.flow.async_configure(await _start(hass), ENTRY_DATA)
    assert result["errors"] == {"base": "no_permission"}


async def test_user_flow_certificate_unreadable(
    hass: HomeAssistant, unas: AiohttpClientMocker, read_fingerprint: AsyncMock
) -> None:
    read_fingerprint.side_effect = UnasError("weg")
    result = await hass.config_entries.flow.async_configure(await _start(hass), ENTRY_DATA)
    assert result["errors"] == {"base": "cannot_connect"}
    # Zugangsdaten gingen nirgendwohin
    assert not [c for c in unas.mock_calls if str(c[1]).endswith("/api/auth/login")]


async def test_reauth_certificate_changed(
    hass: HomeAssistant, setup: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    result = await setup.start_reauth_flow(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{BASE}/api/auth/login", exc=aiohttp.ServerFingerprintMismatch(b"\xab" * 32, b"\xcd" * 32, HOST, 443)
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "ha-user", "password": "neu"}
    )
    assert result["errors"] == {"base": "cert_changed"}
    await hass.config_entries.async_unload(setup.entry_id)
