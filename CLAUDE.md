# UniFi UNAS – Projektregeln

HA-Integration (`custom_components/unas`, Domain `unas`) für UniFi-NAS (UNAS 2, UNAS 4, UNAS Pro …) über UniFi OS und
UniFi Drive, entwickelt und geprüft an einer UNAS 2 (der Test-UNAS).

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten IPs/MACs/Seriennummern, Laufwerksnamen,
  Heimnetz-Details, lokalen Pfade). Testdaten bleiben fiktiv (192.0.2.x, aa:bb:cc:dd:ee:ff, SERIAL1, Zufalls-UUIDs).
- README, CHANGELOG, Release-Notizen, Issue-Texte und Commit-Texte auf Englisch, ohne Gedankenstriche im Fließtext.
  Code-Kommentare englisch (bestehende deutsche nur bei Änderungen an der Datei mit übersetzen).
- Lokal committen ok, Push und Releases nur nach Rückfrage. Prod-HA nie selbst anfassen (Update kommt über HACS).
- Qualität: Platin der HA Integration Quality Scale ist die Untergrenze (`quality_scale.yaml` mitpflegen).

## Version
Versionen über `tools/next-version` (globale Regeln); die manifest-Version steht bis zum Release auf genau dieser
Nummer. CHANGELOG `## YY.M.N – DD.MM.YYYY` (englisch), offene Punkte unter `## Unreleased`.

## Schnittstelle (13.10., 19.10. und 22.10.2026 aus UniFi OS / Drive ausgelesen, an der Test-UNAS geprüft)

Pfade und Formate stehen in `api.py`. Was dort nicht steht:

- Falsches Passwort = **403** (nicht 401). Parallele Anmeldungen scheitern, bei Fehlversuchen sperrt UniFi OS mit 429
  → Login-Lock im Client, bei 401 meldet sich nur die erste Anfrage neu an. Nur abgelehnter Login = Reauth.
  Kein UI-Konto mit 2FA.
- UniFi OS nutzt ein selbstsigniertes Zertifikat (CN unifi.local) → Standard ist Pinning, Option „Zertifikat prüfen“
  = CA-Prüfung.
- API-Key (19.10. geprüft) bewusst nicht unterstützt: drives 500, storage-io-stats 403, systems/snapshot 403,
  PUT fan-control 500, `/api/system` nur Kurzfassung (ohne firmware/apps).
- Pool-`status` nur `fullyOperational` je gesehen, andere Werte → Pool-Sensor „unbekannt“; `healthScore` 1–4 unbekannt.
- Display-Farbe ohne Wirkung, nur bei `features.hasBezel`. `PATCH /api/system` braucht Super Admin (sonst 403).
  `hardware.sysid` 60002 = ea62 = UNAS 2 weiß. UniFi-OS-Teil gleich wie am Dream Router 7, `catalog.py` wie dort.
- Update-Befehle (`/api/firmware/update`, `/api/controllers/<app>/update`): Pfade im UniFi-OS-Bundle bestätigt,
  Installation selbst noch ungeprüft.
- Erkennung umbenannter UNAS per DHCP-Matcher (Ubiquiti-OUIs ohne Hostname; Rückwärts-DNS der UNAS ist ihre
  `…id.ui.direct`-Adresse): HA lädt eine Custom-Integration ohne Eintrag nur über solche Matcher.
- Alle ~220 Drive-Pfade: im Bundle `/app-assets/drive/index.<hash>.js` (nur angemeldet), Muster
  `name:(0,x.fO)("/pfad")` (fO = v2, `_$` = v1). Weitere Kandidaten: snapshots, remote-backup/tasks, systems/logs.

## Prüfen

- `python -m pytest -q -p no:logging` in einer venv mit `requirements_test.txt` (nachgebaute UNAS in
  `tests/conftest.py`, Antworten in `tests/fixtures`, fiktiv)
- `uvx ruff@0.16.9 check . && uvx ruff@0.16.9 format --check .` (Version wie in pruefung.yml),
  `uvx mypy@2.3.1 --python-executable <venv>/bin/python`
- `python tools/pruefe-uebersetzungen.py` (Schlüssel und Platzhalter in strings.json und translations/*.json gleich)
- GitHub Actions: `.github/workflows/pruefung.yml` (ruff, mypy, Übersetzungen, pytest mit Abdeckungsgrenze 96 %,
  hassfest; montags zusätzlich gegen die neueste HA, darf rot sein), `.github/workflows/hacs.yml` (HACS-Validierung).
- hassfest: `docker run --rm -v "$PWD/custom_components/unas:/github/workspace/custom_components/unas" ghcr.io/home-assistant/hassfest`
- Live: in einer Test-HA mit einer echten UNAS. Neustart/Ausschalten nicht drücken, Lüfter danach auf „default“.
