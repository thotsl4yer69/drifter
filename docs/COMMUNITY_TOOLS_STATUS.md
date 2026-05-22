# Community-tool integration status

Snapshot of which of the Wave-1 community tools are live, deferred, or
gated on hardware that hasn't been added yet.

## Installed

| Tool | Source | Verified |
|---|---|---|
| `caringcaribou` 0.7 | `pip install caringcaribou` into `/opt/drifter/venv` | `import caringcaribou` OK; `drifter-can-discovery` active + accepting commands on `drifter/can/command` |
| `urh` 2.10.0 | `pip install --no-deps urh` into `/opt/drifter/venv` (no PyQt5) | `import urh` OK; consumed by `src/urh_classifier.py` |
| `bettercap` | `apt install bettercap` | `/usr/bin/bettercap` present |
| `kismet` | `apt install kismet` | `/usr/bin/kismet` present |

## Service activation gated on hardware

### Kismet + bettercap need a monitor-mode-capable Wi-Fi adapter

The Pi 5's built-in `wlan0` (`iw list`) only supports **IBSS** and
**managed** modes — **not monitor**. Without monitor mode, Kismet
can't passively capture frames and bettercap can't see PMKID/handshake
traffic. Both services are installed but the systemd units stay
inactive on the bench.

To activate, plug in a USB Wi-Fi adapter that supports monitor mode.
Tested options:

- **Alfa AWUS036ACH** (Realtek RTL8812AU, 2.4/5 GHz) — well-supported
  by Kali ARM64
- **Alfa AWUS036AXML** (MediaTek MT7921AU, Wi-Fi 6) — newer, also
  good monitor-mode support
- Anything based on **Atheros AR9271** (e.g. TP-Link TL-WN722N v1)
  for cheap 2.4 GHz only

After plugging the adapter in:

```bash
# 1. Find the new interface (usually wlan1 or wlanX)
iw dev

# 2. Update /etc/kismet/kismet_site.conf so source= points at the
#    new interface, then:
sudo systemctl enable --now drifter-kismet drifter-kismet-bridge

# 3. For bettercap: edit /opt/drifter/etc/audit_targets.yaml and add
#    at least one allowlist entry under `allowed:` for a network you
#    own. Then:
sudo systemctl enable --now drifter-wifi-audit

# 4. Confirm:
curl -fsS http://127.0.0.1:8080/healthz
#    services_hw_pending should drop the three drifter-kismet/audit entries
```

### Fly Catcher needs the operator to resolve the model source

The original build brief referenced "Angelina Tsuboi's Fly Catcher repo"
for ghost ADS-B detection. GitHub search for that exact name returned
no matching account or repo. The `drifter-fly-catcher` service unit
+ `src/fly_catcher.py` skeleton are in place; the service sits
inactive (reported as `services_hw_pending` in healthz) until the
operator points us at:

- The real GitHub repo URL (if it exists publicly)
- Or a local path to a model checkpoint (`.pkl` / `.pt` / `.h5`)

Drop the model file at `/opt/drifter/state/fly_catcher/model.{pkl,pt,h5}`
and `drifter-fly-catcher` will pick it up.

## Live on bench right now

| Service | State |
|---|---|
| `drifter-can-discovery` | active · enabled for boot · subscribed to `drifter/can/command` |
| `drifter-session-recorder` | active · enabled for boot · writing JSONL to `/opt/drifter/state/sessions/` |
| `drifter-rf-baseline` | active · enabled for boot · waiting on first GPS fix to start baseline capture |

## healthz contract behaviour

All four pending services (`drifter-kismet`, `drifter-kismet-bridge`,
`drifter-wifi-audit`, `drifter-fly-catcher`) sit in `_HW_OPTIONAL` per
`src/web_dashboard_handlers.py` so they're reported as
`services_hw_pending` rather than dragging the contract to HTTP 503.
Current `/healthz` returns `200 · ok-hw-pending`.
