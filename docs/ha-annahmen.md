# HA-Annahmen

<!-- Vorlage aus repo-vorlagen/vorlagen/ha-annahmen.md. Liegt im Repo als docs/ha-annahmen.md.
     Der HA-Beta-Bericht (repo-vorlagen/ha-beta-bericht.md) gleicht diese Liste jeden Monat mit den
     Neuerungen der HA-Beta ab. Nur festhalten, was bei einer Änderung in HA still kaputtginge. -->

Worauf sich dieses Repo bei Home Assistant verlässt. Bei jeder Änderung am Code mitpflegen.

**Getestet mit:** HA 2026.9.3 (Tests), 2026.10 · **Mindestversion:** 2026.9.0 (`hacs.json`)

## Annahmen

| Annahme | Wo im Code | Abgesichert durch |
|---|---|---|
| `DeviceInfo` kennt `via_device_id` (Geräte-ID) und `translation_key`/`translation_placeholders` für Gerätenamen | `entity.py` `UnasDiskEntity` | `tests/test_setup.py::test_devices_and_values` |
| Zeroconf-Matcher `_smb._tcp` mit `name: unas*` startet `async_step_zeroconf` | `manifest.json`, `config_flow.py` | `tests/test_config_flow.py::test_zeroconf` |
| DHCP-Matcher (nur `macaddress`) startet `async_step_dhcp` auch aus HAs ARP-Scan; HA lädt die Integration dafür ohne Eintrag | `manifest.json`, `config_flow.py` | `tests/test_os_features.py::test_dhcp_finds_renamed_unas` (der ARP-Scan selbst nur am echten HA) |
| `_abort_if_unique_id_configured(updates=…)` übernimmt eine neue Adresse | `config_flow.py` | `test_config_flow.py`, `test_os_features.py` |
| Update-Entitäten zeigen `entity_picture`; ohne eigenes Bild das Logo der Integration | `update.py` | `test_os_features.py::test_product_picture` |
| `async_get_clientsession(verify_ssl=False)` liefert eine Session ohne CA-Prüfung, `aiohttp.Fingerprint` pinnt je Anfrage | `__init__.py`, `api.py` | `test_features.py` (Zertifikat) |
| `RepairsFlow` mit `async_create_fix_flow(hass, issue_id, data)` | `repairs.py` | `test_features.py` |
| `ConfigEntry.async_create_background_task` für den Bilder-Katalog | `__init__.py` | `test_os_features.py` |

## Fremde Integrationen

Keine; Entitäten anderer Integrationen werden nicht gelesen.

## Prüfbefehl

```sh
.venv/bin/python -m pytest -q -p no:logging
```
