# DRIFTER — Field Deploy Runbook

Literal copy-paste sequence for getting `drifter` onto the Pi in the
Jaguar X-Type and confirming it's live. Assumes you have the laptop and
the Pi on the same Wi-Fi (your home network *or* the Pi's own
`MZ1312_DRIFTER` hotspot once it's running).

> **Status meaning**
> - **yellow** — code on the Pi, services enabled, but no end-to-end
>   verification round-trip from the fleet orchestrator yet.
> - **green** — `mesh deploy drifter` returns exit 0 and `mesh status
>   drifter` returns `ok`.

---

## Prerequisites (one-time)

- A GitHub deploy key on the Pi for `git@github.com:thotsl4yer69/drifter.git`
  (or HTTPS PAT in `~/.netrc`). The repo is already at
  `https://github.com/thotsl4yer69/drifter`.
- The Pi's user is `kali` (Kali ARM64). If yours differs (`pi`, `ubuntu`),
  swap it everywhere below.

## Step 1 — Find the Pi

```bash
# From the laptop, on the same LAN as the Pi:
arp -a | grep -i 'b8:27\|dc:a6\|d8:3a'    # common Pi MAC OUIs
# …or, if the Pi's hotspot is up:
ssh kali@10.42.0.1
```

Last known IP: `10.246.228.156`. DHCP leases shift — `arp -a` is the
truth.

## Step 2 — Pull this branch onto the Pi

```bash
ssh kali@<pi-ip>
cd /home/kali/drifter || git clone git@github.com:thotsl4yer69/drifter.git /home/kali/drifter && cd /home/kali/drifter
git fetch origin
git checkout main
git pull --ff-only
```

## Step 3 — Run the contract deploy

```bash
sudo ./scripts/oneshot.sh
```

Expected tail of the log:

```
STAGE 40 OK
STAGE 45 START — settle into persona
  ✓ persona: diag
STAGE 45 OK
STAGE FINAL START — curl http://127.0.0.1:8080/healthz
{"status":"ok","mode":"diag",…}
STAGE FINAL OK
DEPLOY: ok
```

A fresh deploy enables + starts the whole service set (so the run proves every
unit launches), then **settles into the lean `diag` persona** — telemetry +
driver-safety only, no LLM/voice/recon. So `/healthz` reports `"mode":"diag"`
and the heavy services are *intentionally* inactive at first. Once the node is
stable, bring the assistant stack up with `sudo drifter mode drive` (or
`sudo drifter mode foot` for recon). A re-run of `oneshot.sh` respects
whatever mode you last set.

If a stage fails, the script exits with a numeric code:

| Exit | Meaning | First thing to try |
|---|---|---|
| 10 | apt + venv | `sudo apt-get update && sudo apt-get install -y python3-venv` |
| 20 | diagnose | `drifter diagnose` for the specific failure |
| 30 | smoke    | `bash scripts/post-deploy-check.sh` |
| 40 | enable   | `journalctl -u drifter-canbridge -n 50` (replace name) |
| 50 | /healthz | `drifter healthz` — dashboard probably didn't bind 8080 |

`sudo ./scripts/oneshot.sh --skip-apt` re-runs everything except `apt`,
which is the right choice when you're iterating on code over SSH.

## Step 4 — Verify with the operator CLI

These commands are installed by `oneshot.sh` to `/usr/local/bin/drifter`
and don't need `sudo`:

```bash
drifter status                    # one line per service
drifter healthz                   # dashboard contract probe
drifter diagnose                  # full fleet-contract probe
drifter logs canbridge            # last 50 lines for one service
drifter logs canbridge -f         # tail -F
drifter restart                   # restart every drifter-* unit in SERVICES
drifter restart dashboard         # one service
drifter version                   # deployed git rev / branch
```

`drifter status` is the fastest way to triage at the side of the road —
red bullet next to a service tells you exactly which `journalctl` to
read.

## Step 5 — Update the fleet inventory

This step is in the *other* repo, `thotsl4yer69/fleet`. Edit
`inventory.yaml` and paste the block from
[`docs/fleet-inventory-drifter.yaml`](fleet-inventory-drifter.yaml)
into the `nodes:` mapping. Set `ssh_host:` to whatever IP you got in
Step 1 (or pin the hotspot IP `kali@10.42.0.1` if you only ever
manage from inside the car's hotspot).

Commit + push that fleet repo change.

## Step 6 — Run the fleet deploy from a separate machine

```bash
# On the laptop, NOT on the Pi:
git clone git@github.com:thotsl4yer69/fleet.git
cd fleet
./mesh deploy drifter        # expect exit 0
./mesh status drifter        # expect: ok
```

Once both return clean, edit the **DEPLOY status** block at the bottom
of [`/CLAUDE.md`](../CLAUDE.md) from `needs-human` to `ok` and bump the
`status:` field in `inventory.yaml` from `yellow` to `green`.

---

## Side-of-the-road quick reference

You're at a service station, the Jag won't start, the HUD is dark.
Tether your phone to `MZ1312_DRIFTER` (PSK `uncaged1312`). You should
get DHCP'd into `10.42.0.x`. SSH from a phone terminal app:

```bash
ssh kali@10.42.0.1
sudo drifter status               # find the dead service
sudo drifter logs <name>          # find out why
sudo drifter restart <name>       # try to bring it back
```

If nothing's listening on the hotspot, the Pi probably didn't boot. Pull
power and reseat the SD card.

If the dashboard is up but no telemetry, suspect the OBD-II pigtail —
unplug-replug at the connector under the steering column.

## Troubleshooting

**`drifter: command not found`** — `oneshot.sh` didn't finish stage 10.
Re-run it.

**`/healthz` returns 503** — a *non-hardware* service is down. Run
`drifter status`. Note that hardware-optional units being inactive
(`canbridge`, `rf`, `vivi`, `voicein`, `flipper`, `bleconv`, `gps`,
`lcd`/`fbmirror`, `kismet`, `wifi-audit`, `fly-catcher`, `can-discovery`)
return **`ok-hw-pending` (200)**, not 503 — that's the dongle simply not being
plugged in, not a failure. A 503 means something like `dashboard`, `logger`,
`watchdog`, or `alerts` actually failed; check that unit's `journalctl`.

**`STAGE 40 FAIL — systemctl restart drifter-X failed`** — that unit
file is broken or its dependency isn't met. Check
`systemctl status drifter-X` and the unit file in `/etc/systemd/system/`.

**MQTT broker not reachable** — `systemctl status nanomq` (preferred)
or `systemctl status mosquitto` (fallback). `install.sh` enables one
or the other depending on which apt source is reachable.

**`can0` doesn't exist** — the bench-validated adapter is a **CANable/slcan**
(`0483:5740`), which comes up as **`slcan0`**, *not* `can0`. So `candump can0`
showing nothing usually means you're looking at the wrong interface, not that
CAN is dead. Check both: `ip -brief link show slcan0` / `ip -brief link show
can0`, and `lsusb | grep -i 0483:5740`. `drifter diagnose` already checks
`slcan0` as a fallback. If neither interface exists, the adapter isn't plugged
in or the kernel module/udev rule didn't load (`/etc/udev/rules.d/80-can.rules`).

## First-drive CAN decision (do this in the car, engine running)

The 2004 X-Type 2.5 is **not yet confirmed to speak CAN on the OBD-II pins** —
some of that era are **K-line** (ISO 9141 / KWP2000) instead. Settle it before
trusting empty gauges.

**`drifter diagnose` now settles this for you** — when `can0`/`slcan0` is up it
fires a read-only OBD query (mode 01 PID 05) and reports the result on the
`can0` check line: `CAN/ISO-15765 confirmed (ECU answered 7E8)` if the car
speaks CAN, or `no 7E8 OBD response — engine off, or K-line: use
drifter-obdbridge` if it's silent. Run it with the engine running. To confirm
by hand:

```bash
candump slcan0          # NOT can0 — see above
```

- **See `7E8` frames (ECU responses)?** ✅ CAN works — gauges will populate.
- **Only `7DF` requests, no `7E8` responses?** → the car is **K-line**, and the
  CANable **cannot** read OBD here. Switch to an **ELM327 (K-line)** adapter and
  run `drifter-obdbridge` (`src/obd_bridge.py`) instead of `drifter-canbridge`.

**Do not plug the Flipper Zero and the CANable in at the same time** — they
share USB ID `0483:5740`, so the bridges will grab the wrong device. Unplug one
before testing the other.

See [`FIRST_DRIVE.md`](../FIRST_DRIVE.md) for the full hardware-validated
walkthrough.
