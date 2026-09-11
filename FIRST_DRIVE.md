# First vehicle test — Jaguar X-Type

Start with stationary, read-only diagnostics. See [the readiness review](docs/VEHICLE_TEST_READINESS.md)
for software evidence and remaining hardware gates. The previous June hardware
notes are historical; this update has not been deployed to the Pi or Jaguar.

## 1. Deploy on the Pi

Use a clean checkout. Until the readiness PR is merged, use its branch below;
after merge, replace that branch with `main`.

```bash
cd /home/kali/drifter
git status --short
git fetch origin
git switch fix/vehicle-test-readiness
git pull --ff-only
git rev-parse HEAD
sudo ./scripts/install-obd.sh
sudo ./scripts/deploy-cockpit-v4.sh
sudo drifter mode diag
```

Keep the commit and printed backup path with the test notes. The updater needs
an existing `/opt/drifter/venv`; a clean OS still needs the full installer.
Existing connection settings and vehicle data are preserved. An installation
failure after file replacement restores the previous runtime and services.

## 2. Configure one identified reader

Use the reader's label/documentation and the Pi's Bluetooth/Wi-Fi device list.
A Bluetooth name does not prove Classic RFCOMM support; BLE-only readers are
not supported by this transport. Confirm the channel or TCP port rather than
assuming a common default. Pair/join through the Pi's OS first.

```bash
sudoedit /opt/drifter/.env
```

Keep unrelated settings. Replace angle-bracket placeholders before saving.
For a paired Bluetooth Classic reader:

```dotenv
DRIFTER_TRANSPORT=elm327
DRIFTER_ELM_LINK=bluetooth
ELM_BT_MAC=<confirmed adapter MAC>
ELM_BT_CHANNEL=<confirmed RFCOMM channel>
OBD_POLL_HZ=5
```

Or for a reachable Wi-Fi reader:

```dotenv
DRIFTER_TRANSPORT=elm327
DRIFTER_ELM_LINK=wifi
ELM_WIFI_HOST=<confirmed reader address>
ELM_WIFI_PORT=<confirmed TCP port>
OBD_POLL_HZ=5
```

Keep wired/USB console access during Wi-Fi-reader setup if joining its network
would interrupt the hotspot or SSH. Leave `DRIFTER_LAB_MODE` unset and close
other scan apps sharing the reader.

```bash
sudo systemctl restart drifter-canbridge drifter-obdbridge
sudo systemctl is-active drifter-obdbridge drifter-safety drifter-alerts drifter-logger drifter-dashboard
journalctl -u drifter-obdbridge -n 60 --no-pager
```

`hw_pending` before adapter/ECU connection is expected. `connecting` means
qualification/search; only valid ECU metrics produce `online`. CAN should
defer to the explicitly selected ELM.

## 3. Stationary acceptance

1. Connect the reader to the diagnostic socket. Put ignition in RUN, engine off,
   and allow protocol search to finish.
2. Open `http://10.42.0.1:8080/` on the tethered phone or
   `http://127.0.0.1:8080/` on the Pi. Do not add `?sim=1`; that is demo data.
   GPS is optional for OBD speed.
3. Confirm speed 0, RPM 0, plausible coolant and the negotiated protocol.
   Unsupported voltage must remain blank. Gear/odometer also remain blank
   without a real producer.
4. Capture one minute:

   ```bash
   sudo drifter vehicle-check --seconds 60
   ```

5. Start the engine and idle while parked. Compare RPM with the tachometer and
   confirm speed stays zero. Capture two minutes:

   ```bash
   sudo drifter vehicle-check --seconds 120 --engine-running
   ```

The command prints PASS/FAIL, returns exit code 0/1 and saves JSON under
`/opt/drifter/logs/vehicle-check-*.json`. It observes MQTT only; it never opens
or writes to the ECU. RPM, speed and coolant must start within 15 seconds,
continue without gaps over 15 seconds and remain fresh at the end. Exactly one
measured source is required. Voltage and DTC availability are reported separately.

Compare DTCs with an independent reader/application in a separate session.
Unavailable is not a successful empty scan. Do not erase codes, code modules
or actuate components during this test. If VIN reading is unsupported, confirm
the active profile is the actual Jaguar independently.

## 4. Recovery while parked

- Disconnect the reader: gauges should disappear within 15 seconds, online
  status must clear, and the Pi must remain running.
- Reconnect: protocol negotiation and fresh values should return. Repeat a
  60-second capture.
- Test an ignition cycle and Pi restart. Confirm persisted link settings,
  `diag` mode, logging and safety service, then repeat the capture.
- Repeat stationary tests independently with the other wireless reader.
  Only then test `DRIFTER_ELM_LINK=auto` if fallback is wanted.

## 5. Short monitored drive

Proceed after stationary/recovery gates pass. Start recording before moving;
have a passenger observe the display and review the recording afterward.
Use regular vehicle instruments as the reference. This project is not qualified
as a replacement instrument cluster or certified driver-safety system.

```bash
sudo drifter vehicle-check --seconds 600 --engine-running
```

Record vehicle/profile, reader model, link/protocol, software commit and result.
Inspect the continuity JSON and daily `/opt/drifter/logs/drive_*.jsonl`. Investigate
failures before calling that combination tested. Fuel/cost are estimates; set
the actual fuel price in the trip configuration.

## Troubleshooting

```bash
drifter diagnose
curl -fsS http://127.0.0.1:8080/healthz
journalctl -u drifter-obdbridge -u drifter-canbridge -u drifter-safety -n 100 --no-pager
mosquitto_sub -h localhost -t 'drifter/obd/status' -t 'drifter/snapshot' -t 'drifter/diag/dtc' -v
```

`ok-hw-pending` and `bus_fresh` do not prove a responding vehicle.
`vehicle_ready` indicates recent data; the capture verifies continuity.
Absent raw CAN replies do not establish the physical protocol. Use the ELM's
negotiated protocol after an ECU response.

The lean `diag` mode excludes heavy AI/recon services. RF, TPMS, GPS, voice,
vision and additional Jaguar modules require separate qualification.
