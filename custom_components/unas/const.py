"""Konstanten der UNAS-Integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "unas"
MANUFACTURER = "Ubiquiti"

UPDATE_INTERVAL = timedelta(seconds=30)
# Firmware-Stand, Snapshots und Dienste ändern sich selten: nur jede 10. Runde (alle 5 Minuten)
SLOW_EVERY = 10
HTTP_TIMEOUT = 20

# SHA-256 des bei der Einrichtung gemerkten Zertifikats (entry.data)
CONF_FINGERPRINT = "fingerprint"

FAN_PROFILES = ["quiet", "default", "cooling"]
SMART_TEST_QUICK = "quick"
