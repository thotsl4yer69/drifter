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
git checkout claude/drifter-fleet-compliant-GSdKl
git pull --ff-only
```

## Step 3 — Run the contract deploy

```bash
sudo ./scripts/oneshot.sh
```

Expected tail of the log:

```
STAGE 40 OK
STAGE FINAL START — curl http://127.0.0.1:8080/healthz
{"status":"ok",…}
STAGE FINAL OK
DEPLOY: ok
```

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
drifter restart                   # restart all 15 drifter-* units
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

**`/healthz` returns 503** — one or more services failed. Run
`drifter status`. Likely candidates after a reboot: `drifter-canbridge`
(no CAN adapter plugged in) and `drifter-rf` (no RTL-SDR plugged in).
Both warn-only at the diagnose level.

**`STAGE 40 FAIL — systemctl restart drifter-X failed`** — that unit
file is broken or its dependency isn't met. Check
`systemctl status drifter-X` and the unit file in `/etc/systemd/system/`.

**MQTT broker not reachable** — `systemctl status nanomq` (preferred)
or `systemctl status mosquitto` (fallback). `install.sh` enables one
or the other depending on which apt source is reachable.

**`can0` doesn't exist** — USB CAN adapter not plugged in or kernel
module not loaded. `lsusb | grep -i CAN` and check
`/etc/udev/rules.d/80-can.rules`.
