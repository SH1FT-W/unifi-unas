# Changelog

## 26.10.2 – 19.10.2026

- **New: the certificate is remembered.** At setup the integration remembers the certificate the UNAS presents and from then on sends the password only to exactly that one. Existing entries remember it on their next start. If the UNAS later presents a different one (for example after a reset), Settings → Repairs shows a notice with the old and the new fingerprint; once confirmed, the new one applies.
- **New:** "Storage read" and "Storage write" (all disks, average of the last 5 minutes), "Disks at risk" and "Hottest disk".
- **New:** "Storage problem" also turns on when a pool has lost its redundancy (attribute `redundancy`). Drives additionally show quota, members and remote backup as attributes.
- **Changed:** "Health score" is now called "Health" and shows **Good** (Drive value 5) or **Check** (below) instead of the bare number; the number is kept as "5 / 5" in the attribute `score`. The entity ID stays the same.
- **Fixed:** If the account lacked UniFi OS admin rights, Home Assistant asked for a new password after restart, shutdown or an update. Now it says that the account lacks the rights.
- **Fixed:** While the UNAS was restarting or installing an update, Home Assistant could wrongly ask for new credentials and stop polling. Now the values are briefly unavailable; Home Assistant only asks for the password when the UNAS itself rejects the login.
- **Fixed:** Empty bays (UNAS models with free slots) would have shown up as a disk with a problem.
- **Fixed:** When the session expired, all five parallel requests logged in again at the same time and UniFi OS briefly locked the account (HTTP 429). Now only the first one logs in, the others use the new session.
- **Fixed:** Disks without a serial number added while running got no entities; now the bay is used, as for the device.
- **New:** Option "Verify certificate" (off by default) at setup and under Configure, for a UNAS with a certificate from a trusted authority.

## 26.10.1 – 16.10.2026

- **New version numbers:** versions now count **year.month.release of the month**: `26.10.1` is the first release in October 2026, the next month starts again at `.1` (for example `26.11.1`). The jump from `1.0.1` to `26.10.1` is intended, Home Assistant recognises `26.10.1` as newer.

## 1.0.1 – 13.10.2026

- Display name "UniFi UNAS" (domain `unas`, entity IDs and device name stay the same).

## 1.0.0 – 13.10.2026

First version: UNAS 2 locally through UniFi OS and UniFi Drive.

- Storage (used, free, capacity, usage, status, problem), data scrubbing, usage per Drive share
- System: CPU, temperature, memory, start time, network throughput, link speed, services
- One sub-device per disk: temperature, power-on hours, sectors, health score, problem, SMART quick test
- Fan profile, restart, shut down, start data scrubbing, updates for UniFi OS and UniFi Drive
- Reauthentication, reconfigure, diagnostics, quality scale (self-assessed)
