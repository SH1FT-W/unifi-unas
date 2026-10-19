# UniFi UNAS – Projektregeln

HA-Integration (`custom_components/unas`, Domain `unas`) für UniFi-NAS (UNAS 2, UNAS 4, UNAS Pro …) über UniFi OS und
UniFi Drive, entwickelt und geprüft an einer UNAS 2 (der Test-UNAS).

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten IPs/MACs/Seriennummern, Laufwerksnamen,
  Heimnetz-Details, lokalen Pfade). Testdaten bleiben fiktiv (192.0.2.x, aa:bb:cc:dd:ee:ff, SERIAL1, Zufalls-UUIDs).
- README, CHANGELOG, Release-Notizen, Issue-Texte und Commit-Texte auf Englisch, ohne Gedankenstriche im Fließtext.
  Code-Kommentare deutsch.
- Lokal committen ok, Push und Releases nur nach Rückfrage. Prod-HA nie selbst anfassen (Update kommt über HACS).
- Qualität: Platin der HA Integration Quality Scale ist die Untergrenze (`quality_scale.yaml` mitpflegen).

## Version
Kalender-Schema **YY.M.N** ohne v (Jahr zweistellig, Monat ohne führende Null, N = laufende Nummer im Monat ab 1;
es zählen nur echte, veröffentlichte GitHub-Releases des Monats, auch alte X.Y.Z; bloße Tags nicht; ohne Release im
Monat → .1). Nächste Nummer immer aus `tools/next-version`, nie von Hand hochzählen; die manifest-Version steht bis zum
Release auf genau dieser Nummer. CHANGELOG `## YY.M.N – DD.MM.YYYY` (englisch), offene Punkte unter `## Unreleased`.

## Schnittstelle (13.10. und 19.10.2026 aus UniFi OS / Drive ausgelesen, an der Test-UNAS geprüft)

- Anmeldung `POST /api/auth/login` `{username, password, rememberMe}` → Cookie `TOKEN` + Header `X-Csrf-Token`.
  Falsches Passwort = **403** (nicht 401). Abgelaufene Sitzung = 401 → einmal neu anmelden (nur die erste der
  parallelen Anfragen; die anderen wiederholen mit dem neuen Stand, sonst 429). Parallele Anmeldungen scheitern →
  Login-Lock im Client. Nur abgelehnter Login = Reauth; 401 trotz frischer Anmeldung, 403 (Rechte) und 3xx (Umleitung
  beim Hochfahren, `allow_redirects=False`) = nicht verfügbar bzw. `no_permission`. Kein UI-Konto mit 2FA.
- Zertifikat: Standard = Pinning (SHA-256 in `entry.data["fingerprint"]`, `aiohttp.Fingerprint` je Anfrage, beim Setup
  gelesen falls fehlend, Reparatur `cert_changed_<entry_id>` bei Wechsel); Option „Zertifikat prüfen“ (entry.options)
  = CA-Prüfung. UniFi OS nutzt ein selbstsigniertes Zertifikat (CN unifi.local).
- API-Key (`X-API-Key`, an der Test-UNAS 19.10. geprüft): liest storage, device-info, network-io, fan-control,
  v1 services/*, `/api/system` nur Kurzfassung (ohne firmware/apps); drives 500, storage-io-stats 403, systems/snapshot
  403, PUT fan-control 500 → bewusst nicht unterstützt.
- Drive: `/proxy/drive/api/v2/...` direkt, `/proxy/drive/api/v1/...` in `{"err", "data"}` verpackt.
  Gelesen: `systems/device-info` (cpu.currentload = Anteil 0–1, memory in KiB, `version` = Drive-App,
  `firmwareVersion` = UniFi OS), `storage` (pools mit raidGroups[].currentProtection/expectedProtection + disks;
  leere Schächte `state: "empty"`; `healthScore` 5 = gesund, 1–4 unbekannt), `drives` (quota -1 = unbegrenzt,
  memberCount, protections), `systems/network-io` (KB/s), `systems/fan-control`, v1 `services/{smb,time-machine,nfs}/settings`,
  v1 `systems/storage-io-stats?interval=300&start&end` (Unix-s, `series.readKBPS/writeKBPS`, letzter Wert = jüngstes
  volles 5-Min-Mittel). UniFi OS: `/api/system` (`firmware.latest`, `apps.controllers[name=drive].updateAvailable`).
- Erkennung: mDNS `_smb._tcp` Name „UNAS-…“ (= Gerätename, auch `_adisk`); `GET /api/system` ohne Anmeldung
  liefert `mac` (12 Hex) + `hardware.shortname` (z. B. UNAS2W).
- Befehle: `PUT v2/systems/fan-control {profile}`, `POST v2/storage/pools/<id>/data-scrubbing/start`,
  `POST v2/storage/disks/<slot>/smart-tests {type: quick|extended}`, `POST /api/system/reboot|poweroff`,
  `POST /api/firmware/update`, `POST /api/controllers/<app>/update` (Pfade im UniFi-OS-Bundle bestätigt,
  Installation selbst noch ungeprüft).
- Alle ~220 Drive-Pfade: im Bundle `/app-assets/drive/index.<hash>.js` (nur angemeldet), Muster
  `name:(0,x.fO)("/pfad")` (fO = v2, `_$` = v1). Weitere Kandidaten: snapshots, remote-backup/tasks, systems/logs.

## Prüfen

- `python -m pytest -q -p no:logging` in einer venv mit `requirements_test.txt` (nachgebaute UNAS in
  `tests/conftest.py`, Antworten in `tests/fixtures`, fiktiv)
- `uvx ruff check . && uvx ruff format --check .`, `uvx mypy@2.3.1 --python-executable <venv>/bin/python`
- `python tools/pruefe-uebersetzungen.py` (Schlüssel und Platzhalter in strings.json und translations/*.json gleich)
- GitHub Actions: `.github/workflows/pruefung.yml` (ruff, mypy, Übersetzungen, pytest mit Abdeckungsgrenze 96 %,
  hassfest; montags zusätzlich gegen die neueste HA, darf rot sein), `.github/workflows/hacs.yml` (HACS-Validierung).
- hassfest: `docker run --rm -v "$PWD/custom_components/unas:/github/workspace/custom_components/unas" ghcr.io/home-assistant/hassfest`
- Live: in einer Test-HA mit einer echten UNAS. Neustart/Ausschalten nicht drücken, Lüfter danach auf „default“.
