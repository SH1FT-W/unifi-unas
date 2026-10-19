"""Quality Scale: Diagnose ohne Zugang/Adressen, Übersetzungen vollständig."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unas.diagnostics import async_get_config_entry_diagnostics

from .conftest import HOST

COMPONENT = Path(__file__).parent.parent / "custom_components" / "unas"


async def test_diagnostics_redacted(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    data = await async_get_config_entry_diagnostics(hass, setup)
    text = json.dumps(data, default=str)
    assert data["entry"]["password"] == "**REDACTED**"
    assert data["data"]["system"]["mac"] == "**REDACTED**"
    assert data["data"]["disks"]["1"]["serial"] == "**REDACTED**"
    assert data["data"]["disks"]["1"]["temperature"] == 47
    for secret in (HOST, "geheim", "SERIAL1", "aa:bb:cc"):
        assert secret not in text


def _keys(tree: object, prefix: str = "") -> set[str]:
    if not isinstance(tree, dict):
        return {prefix}
    return {k for key, value in tree.items() for k in _keys(value, f"{prefix}.{key}")}


def test_translations_complete() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())
    for language in ("en", "de"):
        translated = json.loads((COMPONENT / "translations" / f"{language}.json").read_text())
        assert _keys(translated) == _keys(strings), language
