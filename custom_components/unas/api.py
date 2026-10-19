"""Lokaler Zugriff auf UniFi OS + UniFi Drive einer UNAS (ohne HA, testbar).

Anmeldung wie die Web-Oberfläche: POST /api/auth/login → Cookie `TOKEN` + Header `X-Csrf-Token`, den jede
ändernde Anfrage mitschicken muss. Beides schickt der Client selbst als Header, damit er HAs gemeinsame
Session nutzen kann (ohne eigenen Cookie-Speicher). Drive-Endpunkte liegen unter /proxy/drive/api/v1 (Antwort in `data`
verpackt) und /proxy/drive/api/v2 (direkt). Die Pfade stammen aus der Drive-Oberfläche (Stand Drive 4.4.17 auf
UniFi OS 6.0.11).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from json import loads as json_loads
import logging
import ssl
import time
from typing import Any

import aiohttp

from .const import HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)

V1 = "/proxy/drive/api/v1"
V2 = "/proxy/drive/api/v2"


class UnasError(Exception):
    """UNAS nicht erreichbar oder unerwartete Antwort."""


class UnasAuthError(UnasError):
    """Anmeldung abgelehnt: Benutzername oder Passwort falsch."""


class UnasPermissionError(UnasError):
    """Angemeldet, aber dem Konto fehlen die Rechte (403), z. B. Admin-Rechte in UniFi OS für Neustart/Updates."""


class UnasCertificateError(UnasError):
    """Die UNAS zeigt ein anderes Zertifikat als das bei der Einrichtung gemerkte."""

    def __init__(self, fingerprint: str) -> None:
        super().__init__("UNAS certificate changed")
        self.fingerprint = fingerprint


def format_fingerprint(fingerprint: str) -> str:
    """SHA-256 als „AB:CD:…“ wie in Browsern."""
    return ":".join(fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2)).upper()


@dataclass(frozen=True, slots=True)
class Disk:
    slot: str
    model: str
    serial: str
    firmware: str
    type: str
    state: str
    size: int
    temperature: float | None
    power_on_hours: int | None
    bad_sectors: int | None
    uncorrectable_sectors: int | None
    health_score: int | None
    read_kbps: float | None
    write_kbps: float | None
    risk_reasons: tuple[str, ...]
    smart_test_supported: bool

    @property
    def at_risk(self) -> bool:
        return self.state != "optimal" or bool(self.risk_reasons)


@dataclass(frozen=True, slots=True)
class Pool:
    id: str
    number: int
    status: str
    raid_level: str
    capacity: int
    used: int
    scrub_status: str
    scrub_last_run: datetime | None
    scrub_next_run: datetime | None
    # Redundanz der aktiven RAID-Gruppe: wie viele Platten ausfallen dürfen (jetzt / eingerichtet)
    protection: int | None = None
    expected_protection: int | None = None

    @property
    def redundancy_lost(self) -> bool:
        return (
            self.protection is not None
            and self.expected_protection is not None
            and (self.protection < self.expected_protection)
        )


@dataclass(frozen=True, slots=True)
class Drive:
    """Laufwerk in Drive (geteilt oder persönlich) mit seiner Belegung."""

    id: str
    name: str
    type: str
    status: str
    usage: int
    snapshot_enabled: bool
    quota: int | None = None
    members: int | None = None
    remote_backup_enabled: bool = False


@dataclass(frozen=True, slots=True)
class SystemInfo:
    name: str
    model: str
    mac: str
    ip: str
    drive_version: str
    firmware: str
    status: str
    cpu_load: float | None
    cpu_temperature: float | None
    memory_total: int | None
    memory_available: int | None
    started: datetime | None
    link_speed: str
    max_link_speed: str


@dataclass(frozen=True, slots=True)
class Firmware:
    version: str
    changelog_url: str | None


@dataclass(slots=True)
class UnasData:
    system: SystemInfo
    pools: dict[str, Pool]
    disks: dict[str, Disk]
    drives: dict[str, Drive]
    receive_kbps: float | None
    transmit_kbps: float | None
    fan_profile: str | None
    fan_profiles: list[str]
    # Langsame Werte (nur jede SLOW_EVERY-te Runde neu geholt)
    latest_firmware: Firmware | None = None
    drive_update: str | None = None
    services: dict[str, bool] = field(default_factory=dict)
    # Lese-/Schreibrate aller Platten, Mittel der letzten vollen 5 Minuten (wie die Drive-Grafik)
    storage_read_kbps: float | None = None
    storage_write_kbps: float | None = None


def _dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def parse_disk(raw: dict[str, Any]) -> Disk:
    return Disk(
        slot=str(raw.get("slotId", "")),
        model=raw.get("model") or "",
        serial=raw.get("serial") or "",
        firmware=raw.get("firmware") or "",
        type=raw.get("type") or "",
        state=raw.get("state") or "unknown",
        size=_int(raw.get("size")) or 0,
        temperature=_num(raw.get("temperature")),
        power_on_hours=_int(raw.get("powerOnHours")),
        bad_sectors=_int(raw.get("badSectorCount")),
        uncorrectable_sectors=_int(raw.get("uncorrectableSectorCount")),
        health_score=_int(raw.get("healthScore")),
        read_kbps=_num(raw.get("readKBPS")),
        write_kbps=_num(raw.get("writeKBPS")),
        risk_reasons=tuple(str(r) for r in raw.get("riskReasons") or ()),
        smart_test_supported=bool(raw.get("smartTestSupported")),
    )


def parse_pool(raw: dict[str, Any]) -> Pool:
    scrub = raw.get("dataScrubbing") or {}
    last = scrub.get("lastTaskRun")
    groups = [g for g in raw.get("raidGroups") or () if isinstance(g, dict) and not g.get("isSSDCache")]
    active = [g for g in groups if g.get("id") == raw.get("activeRaidGroupId")]
    group: dict[str, Any] = (active or groups or [{}])[0]
    return Pool(
        id=raw["id"],
        number=_int(raw.get("number")) or 1,
        status=raw.get("status") or "unknown",
        raid_level=raw.get("preferLevel") or "",
        capacity=_int(raw.get("capacity")) or 0,
        used=_int(raw.get("usage")) or 0,
        scrub_status=scrub.get("status") or "idle",
        scrub_last_run=_dt(last.get("endTime") or last.get("startTime")) if isinstance(last, dict) else _dt(last),
        scrub_next_run=_dt(scrub.get("nextRun")),
        protection=_int(group.get("currentProtection")),
        expected_protection=_int(group.get("expectedProtection")),
    )


def parse_drive(raw: dict[str, Any]) -> Drive:
    protections = raw.get("protections") or {}
    quota = _int(raw.get("quota"))
    return Drive(
        id=raw["id"],
        name=raw.get("name") or "",
        type=raw.get("type") or "",
        status=raw.get("status") or "",
        usage=_int(raw.get("usage")) or 0,
        snapshot_enabled=bool(protections.get("snapshotEnabled")),
        # -1 = ohne Begrenzung
        quota=quota if quota is not None and quota > 0 else None,
        members=_int(raw.get("memberCount")),
        remote_backup_enabled=bool(protections.get("remoteBackupEnabled")),
    )


def _last_sample(values: Any) -> float | None:
    if not isinstance(values, list):
        return None
    return next((n for v in reversed(values) if (n := _num(v)) is not None), None)


def parse_system(raw: dict[str, Any]) -> SystemInfo:
    cpu = raw.get("cpu") or {}
    memory = raw.get("memory") or {}
    nic: dict[str, Any] = next((n for n in raw.get("networkInterfaces") or () if n.get("connected")), {})
    load = _num(cpu.get("currentload"))
    total = _int(memory.get("total"))
    available = _int(memory.get("available"))
    return SystemInfo(
        name=raw.get("name") or "UNAS",
        model=raw.get("model") or "",
        mac=(nic.get("mac") or "").lower(),
        ip=nic.get("address") or "",
        # device-info.version ist die Version der Drive-App, firmwareVersion die von UniFi OS
        drive_version=raw.get("version") or "",
        firmware=raw.get("firmwareVersion") or "",
        status=raw.get("status") or "",
        # Anteil 0–1 (die Oberfläche rechnet ×100)
        cpu_load=round(load * 100, 1) if load is not None else None,
        cpu_temperature=_num(cpu.get("temperature")),
        # KiB wie in /proc/meminfo
        memory_total=total * 1024 if total is not None else None,
        memory_available=available * 1024 if available is not None else None,
        started=_dt(raw.get("startupTime")),
        link_speed=nic.get("linkSpeed") or "",
        max_link_speed=nic.get("maxSpeed") or "",
    )


def parse_firmware(raw: dict[str, Any]) -> Firmware | None:
    """Neueste Firmware laut UniFi OS, z. B. "v6.0.11+f63375a" → "6.0.11"."""
    version = str(raw.get("version") or "").removeprefix("v").split("+", 1)[0]
    if not version:
        return None
    links = (raw.get("_links") or {}).get("upload") or []
    changelog = next((link.get("href") for link in links if link.get("name") == "changelog"), None)
    return Firmware(version=version, changelog_url=changelog)


class UnasClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = False,
        fingerprint: str | None = None,
    ) -> None:
        self._session = session
        self.host = host
        # UniFi OS kommt mit selbstsigniertem Zertifikat. Standard: nur das bei der Einrichtung gemerkte Zertifikat
        # annehmen (SHA-256, „trust on first use“); mit der Option „Zertifikat prüfen“ gegen die CA-Liste.
        self.verify_ssl = verify_ssl
        self.fingerprint = None if verify_ssl else fingerprint
        self._ssl: bool | aiohttp.Fingerprint = (
            True if verify_ssl else aiohttp.Fingerprint(bytes.fromhex(fingerprint)) if fingerprint else False
        )
        self.username = username
        self._password = password
        # Header aus der Anmeldung (Cookie TOKEN + X-Csrf-Token); None = nicht angemeldet
        self._auth: dict[str, str] | None = None
        self._login_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"

    async def login(self, *, force: bool = False) -> None:
        async with self._login_lock:
            if self._auth is not None and not force:
                return  # parallele Anfrage hat sich schon angemeldet
            try:
                async with asyncio.timeout(HTTP_TIMEOUT):
                    resp = await self._session.post(
                        f"{self.base_url}/api/auth/login",
                        json={"username": self.username, "password": self._password, "rememberMe": True},
                        ssl=self._ssl,
                        allow_redirects=False,
                    )
                    async with resp:
                        # Falsches Passwort meldet UniFi OS mit 403 (nicht 401), Sperre nach Fehlversuchen mit 429
                        if resp.status in (400, 401, 403):
                            raise UnasAuthError("login rejected")
                        if resp.status != 200:
                            # 429 = Sperre nach Fehlversuchen, 3xx/5xx = UNAS startet gerade
                            raise UnasError(f"login: HTTP {resp.status}")
                        auth: dict[str, str] = {}
                        if token := resp.cookies.get("TOKEN"):
                            auth["Cookie"] = f"TOKEN={token.value}"
                        if csrf := resp.headers.get("X-Csrf-Token") or resp.headers.get("X-Updated-Csrf-Token"):
                            auth["X-Csrf-Token"] = csrf
                        self._auth = auth
            except aiohttp.ServerFingerprintMismatch as err:
                raise UnasCertificateError(err.got.hex()) from err
            except (TimeoutError, aiohttp.ClientError) as err:
                raise UnasError(f"cannot connect: {err}") from err

    async def _request(self, method: str, path: str, json: Any = None, *, relogin: bool = True) -> Any:
        if self._auth is None:
            await self.login()
        # Den beim Senden benutzten Anmeldestand merken: nur er darf bei 401 verworfen werden
        used = self._auth
        headers = dict(used or {})
        try:
            async with asyncio.timeout(HTTP_TIMEOUT):
                resp = await self._session.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json,
                    headers=headers,
                    ssl=self._ssl,
                    # Startet die UNAS gerade, leitet UniFi OS auf seine Oberfläche um: das ist „nicht erreichbar“,
                    # keine abgelaufene Sitzung
                    allow_redirects=False,
                )
                async with resp:
                    if resp.status == 401 and relogin:
                        # Sitzung abgelaufen (z. B. nach Neustart der UNAS): einmal neu anmelden. Laufen mehrere
                        # Anfragen parallel in den 401, meldet sich nur die erste neu an; die anderen sehen schon
                        # den neuen Stand und wiederholen nur (sonst sperrt UniFi OS mit 429).
                        if self._auth is used:
                            self._auth = None
                        return await self._request(method, path, json, relogin=False)
                    if resp.status == 401:
                        # Die Anmeldung selbst klappte gerade (sonst UnasAuthError dort), nur die Anfrage nicht:
                        # typisch, solange Drive nach einem Neustart oder Update noch hochfährt
                        raise UnasError(f"{path}: session not accepted (HTTP 401)")
                    if resp.status == 403:
                        raise UnasPermissionError(f"{path}: permission denied (HTTP 403)")
                    if resp.status >= 300:
                        raise UnasError(f"{path}: HTTP {resp.status}")
                    if (updated := resp.headers.get("X-Updated-Csrf-Token")) and self._auth is not None:
                        self._auth["X-Csrf-Token"] = updated
                    text = await resp.text()
        except aiohttp.ServerFingerprintMismatch as err:
            raise UnasCertificateError(err.got.hex()) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UnasError(f"cannot connect: {err}") from err
        if not text.strip():
            return None
        try:
            return json_loads(text)
        except ValueError as err:
            raise UnasError(f"{path}: no JSON response") from err

    async def identify(self) -> tuple[str, str, str]:
        """Ohne Anmeldung: (MAC, Name, Modell) aus /api/system – für die Erkennung im Netz."""
        try:
            async with asyncio.timeout(HTTP_TIMEOUT):
                resp = await self._session.get(f"{self.base_url}/api/system", ssl=self._ssl)
                async with resp:
                    if resp.status != 200:
                        raise UnasError(f"/api/system: HTTP {resp.status}")
                    text = await resp.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UnasError(f"cannot connect: {err}") from err
        try:
            body = json_loads(text)
        except ValueError as err:
            raise UnasError("/api/system: no JSON response") from err
        if not isinstance(body, dict):
            raise UnasError("/api/system: unexpected response")
        hardware = body.get("hardware") or {}
        model = str(hardware.get("shortname") or "")
        mac = str(body.get("mac") or hardware.get("mac") or "")
        if not model.upper().startswith("UNAS") or len(mac) != 12:
            raise UnasError("not a UniFi NAS")
        mac = ":".join(mac[i : i + 2] for i in range(0, 12, 2)).lower()
        return mac, str(body.get("name") or "UNAS"), model

    async def certificate_fingerprint(self, context: ssl.SSLContext) -> str:
        """SHA-256 des Zertifikats, das die UNAS gerade zeigt (ohne Prüfung gelesen, nur zum Merken)."""
        try:
            async with asyncio.timeout(HTTP_TIMEOUT):
                _reader, writer = await asyncio.open_connection(self.host, 443, ssl=context)
        except (TimeoutError, OSError) as err:
            raise UnasError(f"cannot connect: {err}") from err
        try:
            ssl_object = writer.get_extra_info("ssl_object")
            der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        finally:
            writer.close()
        if not der:
            raise UnasError("UNAS presented no certificate")
        return hashlib.sha256(der).hexdigest()

    async def _get_v1(self, path: str) -> Any:
        body = await self._request("GET", f"{V1}{path}")
        if not isinstance(body, dict) or body.get("err"):
            raise UnasError(f"{path}: {body.get('err') if isinstance(body, dict) else 'no data'}")
        return body.get("data")

    async def fetch(self) -> UnasData:
        """Schneller Teil: System, Speicher, Laufwerke, Netzwerk, Lüfter."""
        system, storage, drives, network, fan = await asyncio.gather(
            self._request("GET", f"{V2}/systems/device-info"),
            self._request("GET", f"{V2}/storage"),
            self._request("GET", f"{V2}/drives"),
            self._request("GET", f"{V2}/systems/network-io"),
            self._request("GET", f"{V2}/systems/fan-control"),
        )
        pools = [parse_pool(p) for p in (storage or {}).get("pools") or ()]
        # Leere Schächte führt Drive mit state „empty“; sie sind keine Platte
        disks = [parse_disk(d) for d in (storage or {}).get("disks") or () if d.get("state") != "empty"]
        drive_list = [parse_drive(d) for d in (drives or {}).get("drives") or ()]
        return UnasData(
            system=parse_system(system or {}),
            pools={p.id: p for p in pools},
            disks={d.slot: d for d in disks},
            drives={d.id: d for d in drive_list},
            receive_kbps=_num((network or {}).get("receiveKBPS")),
            transmit_kbps=_num((network or {}).get("transmitKBPS")),
            fan_profile=(fan or {}).get("currentProfile"),
            fan_profiles=list((fan or {}).get("availableProfiles") or ()),
        )

    async def fetch_slow(self, data: UnasData) -> None:
        """Firmware-Stand und Dienste; Fehler hier lassen die schnellen Werte unberührt."""
        try:
            system = await self._request("GET", "/api/system") or {}
        except UnasError as err:
            _LOGGER.debug("UniFi OS status not available: %s", err)
        else:
            data.latest_firmware = parse_firmware((system.get("firmware") or {}).get("latest") or {})
            drive: dict[str, Any] = next(
                (c for c in (system.get("apps") or {}).get("controllers") or () if c.get("name") == "drive"), {}
            )
            # updateAvailable ist null oder die neue Versionsnummer
            data.drive_update = str(drive["updateAvailable"]) if drive.get("updateAvailable") else None
        for service in ("smb", "time-machine", "nfs"):
            try:
                result = await self._get_v1(f"/services/{service}/settings")
            except UnasError:
                continue
            if isinstance(result, dict) and "enable" in result:
                data.services[service] = bool(result["enable"])
        # Neuester volle 5-Minuten-Wert; die UNAS rundet das Fenster auf volle Abschnitte
        end = int(time.time())
        try:
            stats = await self._get_v1(f"/systems/storage-io-stats?interval=300&start={end - 600}&end={end}")
        except UnasError as err:
            _LOGGER.debug("Storage throughput not available: %s", err)
        else:
            series = (stats.get("series") if isinstance(stats, dict) else None) or {}
            data.storage_read_kbps = _last_sample(series.get("readKBPS"))
            data.storage_write_kbps = _last_sample(series.get("writeKBPS"))

    async def set_fan_profile(self, profile: str) -> None:
        await self._request("PUT", f"{V2}/systems/fan-control", {"profile": profile})

    async def start_scrub(self, pool_id: str) -> None:
        await self._request("POST", f"{V2}/storage/pools/{pool_id}/data-scrubbing/start")

    async def run_smart_test(self, slot: str, test_type: str) -> None:
        await self._request("POST", f"{V2}/storage/disks/{slot}/smart-tests", {"type": test_type})

    async def update_firmware(self) -> None:
        await self._request("POST", "/api/firmware/update")

    async def update_drive(self) -> None:
        await self._request("POST", "/api/controllers/drive/update")

    async def reboot(self) -> None:
        await self._request("POST", "/api/system/reboot")

    async def power_off(self) -> None:
        await self._request("POST", "/api/system/poweroff")
