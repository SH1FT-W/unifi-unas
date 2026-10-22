"""Produktbilder der UniFi-Geräte aus Ubiquitis öffentlichem Gerätekatalog (dieselbe Quelle wie die Oberfläche).

Die UNAS meldet in /api/system ihre `hardware.sysid` (z. B. 60002 = 0xea62 bei der UNAS 2 weiß); der Katalog ordnet
ihr ein Icon zu. HA lädt den Katalog höchstens einmal pro Woche und merkt sich nur die Zuordnung sysid → Bild-Adresse.
Ohne Internet bleiben die Entitäten ohne Bild; sonst ändert sich nichts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, HTTP_TIMEOUT, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

CATALOG_URL = "https://static.ui.com/fingerprint/ui/public.json"
ICON_URL = "https://static.ui.com/fingerprint/ui/icons/{id}_{size}x{size}.png"
ICON_SIZE = 257
MAX_AGE = 7 * 24 * 3600


def parse_catalog(body: Any) -> dict[str, str]:
    """sysid (hex, klein) → Bild-Adresse; Einträge ohne Icon oder in anderer Form werden übersprungen."""
    devices = body.get("devices") if isinstance(body, dict) else None
    icons: dict[str, str] = {}
    for device in devices if isinstance(devices, list) else ():
        if not isinstance(device, dict):
            continue
        sysid = str(device.get("sysid") or "").lower()
        icon: dict[str, Any] = device["icon"] if isinstance(device.get("icon"), dict) else {}
        sizes = [r[0] for r in icon.get("resolutions") or () if isinstance(r, list) and r and isinstance(r[0], int)]
        if not sysid or not icon.get("id") or not sizes:
            continue
        size = ICON_SIZE if ICON_SIZE in sizes else max(sizes)
        icons[sysid] = ICON_URL.format(id=icon["id"], size=size)
    return icons


async def async_load_pictures(hass: HomeAssistant) -> dict[str, str]:
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.catalog")
    cached = await store.async_load() or {}
    icons: dict[str, str] = cached["icons"] if isinstance(cached.get("icons"), dict) else {}
    if icons and time.time() - float(cached.get("fetched") or 0) < MAX_AGE:
        return dict(icons)
    try:
        async with asyncio.timeout(HTTP_TIMEOUT):
            resp = await async_get_clientsession(hass).get(CATALOG_URL)
            async with resp:
                resp.raise_for_status()
                body = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as err:
        _LOGGER.debug("Device catalog not available, keeping %d cached pictures: %s", len(icons), err)
        return dict(icons)
    fresh = parse_catalog(body)
    if fresh:
        await store.async_save({"fetched": time.time(), "icons": fresh})
        return fresh
    return dict(icons)
