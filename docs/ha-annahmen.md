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
| `async_create_clientsession(verify_ssl=…)` liefert eine eigene Session (ohne CA-Prüfung bei `False`), `aiohttp.Fingerprint` pinnt je Anfrage; das Lesen des Zertifikats (`certificate_fingerprint`) geht über eine eigene Socket-Verbindung, nicht über die Session | `__init__.py` `create_client`, `async_read_fingerprint`, `api.py` | `test_features.py` (Zertifikat), `tests/test_config_flow.py::test_user_flow_verify_ssl` |
| HAs gemeinsame Session hat einen normalen Cookie-Speicher: das `TOKEN` einer Anmeldung bliebe dort für Hostnamen (nicht für IP-Adressen) und ginge mit jeder Anfrage mit, eine zweite Anmeldung am selben Host tauschte die Sitzung des laufenden Eintrags. Darum je Client eine eigene Session (`async_create_clientsession`) mit `aiohttp.DummyCookieJar`. Im Setup angelegt, löst HA sie beim Entladen des Eintrags selbst (`current_entry`, auch nach fehlgeschlagenem Setup); im Config-Flow mit `auto_cleanup=False` und danach `detach()`, denn HA ersetzt `close()` dieser Sessions durch eine Warnung. Eine Anfrage auf einer gelösten Session (auch mitten im Verbindungsaufbau) endet als eigener Fehler statt aiohttps `RuntimeError`/`AttributeError` (HACS-Review hacs/default#13228) | `__init__.py` `create_client`, `api.py` `detach`/`_check_open`, `config_flow.py` | `tests/test_setup.py::test_login_cookie_stays_out_of_shared_session`, `::test_flow_session_released_and_closed_session_reported`, `::test_session_detached_while_connecting_gives_own_error`, `tests/test_config_flow.py::test_user_flow_verify_ssl` (Flow `auto_cleanup=False`, Setup `True`), `::test_entry_session_released_on_unload_and_failed_setup` (führt HAs Aufräum-Haken selbst aus, den `aioclient_mock` sonst verwirft). Grenzfall: scheitert das Entladen der Plattformen (FAILED_UNLOAD), arbeitet HA die Entlade-Haken nicht ab, die Session bleibt dann bis zum Stopp |
| `RepairsFlow` mit `async_create_fix_flow(hass, issue_id, data)` | `repairs.py` | `test_features.py` |
| Nicht dauerhafte Reparaturhinweise (`is_persistent=False`) überleben das Neuladen eines Eintrags bis zum HA-Neustart; der erste erfolgreiche Abruf nach dem Laden löscht sie deshalb | `coordinator.py` `_async_update_data` | `test_features.py::test_stale_certificate_repair_removed_after_reload` |
| Nicht dauerhafte Reparaturhinweise ohne Fix-Flow (`is_fixable=False`, `is_persistent=False`) je beschädigtem Pool; `ir.async_get(hass).issues` ist ein Dict mit Schlüsseln `(domain, issue_id)`, über das veraltete Hinweise (Pool gesund, gelöscht oder vor dem Neuladen angelegt) gefunden und gelöscht werden | `coordinator.py` `_update_pool_issues` | `tests/test_pool_health.py` |
| `async_remove_entry` läuft beim Entfernen eines Eintrags; nicht dauerhafte Hinweise blieben sonst bis zum HA-Neustart stehen | `__init__.py` `async_remove_entry` | `tests/test_pool_health.py::test_damaged_pool_keeps_notice_across_reload_and_removal_clears_it` |
| Binärsensor `device_class: problem` mit `is_on = None` erscheint als „unbekannt“ (unbekannter Pool-Zustand löst keinen Alarm aus) | `binary_sensor.py` `PoolProblem` | `tests/test_pool_health.py::test_unknown_status_is_no_alarm` |
| Entitätsname aus `translation_key` mit `translation_placeholders` („Storage pool {pool}“) | `binary_sensor.py` `PoolProblem` | `tests/test_pool_health.py::test_healthy_pool` |
| `ConfigEntry.async_create_background_task` für den Bilder-Katalog | `__init__.py` | `test_os_features.py` |
| Die Update-Liste in HA (Einstellungen › Updates) zeigt oben den Gerätenamen, darunter `Bereich · title latest_version` (Frontend `ha-config-updates.ts`); Casora nimmt `title` als Namen. Darum `title` „UniFi OS“ und „UniFi Drive“, sonst sähe das Firmware-Update aus wie das gleichnamige Update der Integration | `update.py` `FirmwareUpdate`, `DriveUpdate` | `tests/test_control.py::test_updates` (nur das Attribut, die Anzeige liegt im Frontend) |

## Fremde Integrationen

Keine; Entitäten anderer Integrationen werden nicht gelesen.

## Prüfbefehl

```sh
.venv/bin/python -m pytest -q -p no:logging
```
