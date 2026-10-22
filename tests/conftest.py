"""Gemeinsame Test-Bausteine: eine nachgebaute UNAS 2 (Antworten aus einer echten, anonymisiert)."""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.unas.const import DOMAIN

HOST = "192.0.2.20"
BASE = f"https://{HOST}"
V1 = f"{BASE}/proxy/drive/api/v1"
V2 = f"{BASE}/proxy/drive/api/v2"
MAC = "aa:bb:cc:dd:ee:ff"
CSRF = "csrf-token"
ENTRY_DATA = {"host": HOST, "username": "ha-user", "password": "geheim"}
POOL_ID = "f92106bf-5fa4-4daa-aa69-ce16c5404e39"
# SHA-256 des Zertifikats, das die nachgebaute UNAS zeigt
FINGERPRINT = "ab" * 32


def fixture(name: str) -> Any:
    return json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text())


def mock_unas(mock: AiohttpClientMocker, *, login_status: int = 200, clear: bool = True, **overrides: Any) -> None:
    """Alle Endpunkte der UNAS; `overrides` ersetzt einzelne Antworten (Name wie die Fixture-Datei).

    Mit clear=False bleiben vorher registrierte Antworten vorne (der Mocker nimmt die erste passende)."""
    if clear:
        mock.clear_requests()
    data = {
        name: fixture(name)
        for name in ("device_info", "storage", "drives", "network_io", "fan_control", "system", "storage_io")
    }
    data.update(overrides)
    mock.post(
        f"{BASE}/api/auth/login", status=login_status, json={}, headers={"X-Csrf-Token": CSRF}, cookies={"TOKEN": "tok"}
    )
    mock.get(f"{V2}/systems/device-info", json=data["device_info"])
    mock.get(f"{V2}/storage", json=data["storage"])
    mock.get(f"{V2}/drives", json=data["drives"])
    mock.get(f"{V2}/systems/network-io", json=data["network_io"])
    mock.get(f"{V2}/systems/fan-control", json=data["fan_control"])
    mock.get(f"{BASE}/api/system", json=data["system"])
    mock.get(f"{V1}/services/smb/settings", json={"err": None, "type": "single", "data": {"enable": True}})
    mock.get(f"{V1}/services/time-machine/settings", json={"err": None, "type": "single", "data": {"enable": True}})
    mock.get(f"{V1}/services/nfs/settings", json={"err": None, "type": "single", "data": {"enable": False}})
    mock.get(f"{V1}/systems/storage-io-stats?interval=300", json=data["storage_io"])
    mock.put(f"{V2}/systems/fan-control", json={})
    mock.post(f"{V2}/storage/pools/{POOL_ID}/data-scrubbing/start", json={})
    mock.post(f"{V2}/storage/disks/1/smart-tests", json={})
    mock.post(f"{BASE}/api/system/reboot", json={})
    mock.post(f"{BASE}/api/system/poweroff", json={})
    mock.post(f"{BASE}/api/firmware/update", json={})
    mock.post(f"{BASE}/api/controllers/drive/update", json={})
    mock.patch(f"{BASE}/api/system", json={})
    # Produktbild: ohne eigene Antwort im Test kein Katalog (wie offline)
    mock.get("https://static.ui.com/fingerprint/ui/public.json", status=404)


def calls(mock: AiohttpClientMocker, method: str, path: str) -> list[tuple[Any, Any]]:
    """(Body, Header) aller Aufrufe einer Methode auf einen Pfad."""
    return [
        (data, headers) for m, url, data, headers in mock.mock_calls if m.upper() == method and str(url).endswith(path)
    ]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    return


@pytest.fixture(autouse=True)
def read_fingerprint() -> Generator[AsyncMock]:
    """Zertifikat lesen braucht eine echte TLS-Verbindung; hier liefert es immer FINGERPRINT."""
    with patch(
        "custom_components.unas.api.UnasClient.certificate_fingerprint", AsyncMock(return_value=FINGERPRINT)
    ) as mock:
        yield mock


@pytest.fixture
def unas(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    mock_unas(aioclient_mock)
    return aioclient_mock


@pytest.fixture
async def setup(hass: HomeAssistant, unas: AiohttpClientMocker) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="UNAS 2", unique_id=MAC, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
