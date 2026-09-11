# Vehicle-test readiness review — 2026-09-11

**Status: software bench checks passed; target Pi and vehicle acceptance pending.**
The reference vehicle is the 2004 Jaguar X-Type 2.5 V6. Its negotiated OBD
protocol and the wireless readers' endpoints have not been confirmed by this
review. No ECU was connected and no vehicle commands were sent during testing.

## Scope

The starting point was PR #64, main commit
`93ee751d0fdcf4fbc09e07a00148e155b2cdf02f`. All **525 tracked files**, including
Git blob hashes and file modes, were compared with GitHub's main tree; the
review checkout matched exactly. Pre-existing work in another checkout was preserved.

Manual review followed the first-vehicle-test path: transport selection,
adapter setup, decoding, discovery, polling, VIN/DTC reads, MQTT delivery,
safety/diagnostic consumers, persistence, cockpit state, health reporting,
service modes, installation and recovery. Repository-wide lint and tests cover
the wider project. This is not a claim that every optional module, firmware
image or hardware integration has been physically qualified.

## Defects corrected

| Area | Finding | Result |
|---|---|---|
| ELM framing | PR #64 requested compact replies while decoding expected spaces; fixed sleeps could truncate replies | Prompt-delimited transactions handle fragmentation and compact/spaced/multiline output; timeouts invalidate the connection |
| Adapter qualification | Opening a socket/TTY was treated as a working ELM | Identity/setup responses must succeed; failed candidates close before fallback |
| Recovery/freshness | EOF and unanswered PIDs could leave stale values circulating | Reconnect on EOF; expire observations; preserve each field's original timestamp |
| Ownership | Process-local override did not stop the other bridge; CAN service omitted `.env` | Shared selection, exclusive file lease, consistent environment; CAN setup defers when ELM is selected |
| Discovery | Zero support bitmaps could trigger polling every PID | Distinguish empty from unanswered, follow advertised pages, reject malformed frames |
| PID identity | B2S1 oxygen voltage used bank-1 PID 15 | Use B2S1 PID 18 |
| Raw CAN | Unrelated traffic consumed the only receive attempt | Wait for the matching PID within a bounded transaction |
| ISO-TP | Incomplete/orphaned/out-of-order frames could be accepted | Require complete lengths, sequence order, ECU identity and successful flow control |
| VIN/DTC | Wireless path omitted reads; VIN service competed for CAN | Selected bridge owns read-only Mode 03/07/09; vehicle ID consumes its VIN message |
| Unknown DTCs | Missing replies could look like a successful empty scan | Availability flags and cockpit unknown/stale states |
| MQTT | Core subscriptions disappeared after broker restart; obsolete callbacks remained | Common v2 client factory and subscriptions restored on CONNACK |
| Safety | Tier-1 service was absent from monitored/default diagnostics set; braking rate assumed fixed intervals | Include safety in modes/install lists; use elapsed time; reject stale/duplicate samples |
| Logging/aggregation | Non-object messages could break ingestion; deque iteration raced MQTT | Payload validation and locked aggregation |
| Cockpit | Demo odometer/gear, zero readings before connection, GPS-gated OBD speed, incorrect 1% throttle scaling | Unknown values stay unknown; OBD speed works without GPS; core metrics expire separately |
| HTTPS cockpit | WSS targeted a plain WS listener | Same-origin REST fallback without renewing observation timestamps |
| Trip display | Configured AUD costs only appeared under a legacy GBP field | Currency-neutral cost/currency fields; unavailable instantaneous consumption stays blank |
| Health | Any MQTT traffic could suggest vehicle readiness | Separate `bus_fresh`, `telemetry_fresh`, `vehicle_ready` |
| Installation | Partial source manifest omitted config dependencies | Stage/compile/import the whole source tree; back up and restore replaced code, units and CLI on failure |
| Optional fleet API | Any credentials issued tokens; WS lacked authentication | Verify configured credentials, reject malformed claims, authorize WS; default bind is loopback |
| Lab tools | CAN experiments and playback could interfere with vehicle operation | Lab opt-in plus `foot` mode; CAN discovery takes the telemetry lease; recorded commands are not replayed |

## Verification

Local checks include the full Python suite, Ruff, shell syntax, six cockpit
state-regression tests and a production Vite build. Isolated installer tests
exercise success, preflight failure and restart failure. Copying, compilation,
imports and rollback are real; systemd and pip are substituted. Tests never
modify the host's `/opt/drifter` or systemd units.

Final local result: **1,411 Python tests passed**, **6 cockpit tests passed**,
**30 shell scripts passed syntax checks**, Ruff and Python compilation passed,
and the production cockpit build succeeded.

`scripts/bench-obd.py` launches **six actual service processes** (both bridges,
logger, alerts, safety and batcher) against a loopback MQTT broker and a TCP
ELM emulator. It passed twelve checks:

1. Fragmented/compact replies decode through TCP and MQTT.
2. K-line stored/pending DTCs and chunked VIN decode correctly.
3. `vehicle-check` passes sustained fresh observations.
4. Socket EOF produces a disconnected state.
5. Adapter reopening resumes telemetry.
6. Broker restart restores telemetry subscriptions.
7. Alert delivery resumes after broker restart.
8. ECU silence is reported.
9. No snapshots are fabricated during ECU silence.
10. Both running bridges still yield one telemetry producer.
11. Only adapter setup and read-only OBD requests are sent.
12. Logger persists the pipeline messages.

The bench VIN, P0171 and sensor values are synthetic, **not Jaguar observations**.
Raw CAN has frame/flow-control tests, not a physical CAN bus here. Direct
Bluetooth RFCOMM and real USB serial require target-device acceptance.

Reproduce on a development host with no vehicle connected:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,bench]'
.venv/bin/ruff check src tests scripts/bench-obd.py
.venv/bin/pytest -q
.venv/bin/python scripts/bench-obd.py --output-dir /tmp/drifter-bench
cd cockpit-v4
npm ci
npm test
npm run build
```

Installer tests require root and redirect all runtime/unit/bin paths into
pytest's temporary directory. CI runs them in a separate root step, and also
runs the ELM/MQTT bench.

## Physical acceptance still required

| Gate | Current evidence | Acceptance needed |
|---|---|---|
| Pi deployment | Staging/rollback tested in isolation; Pi unreachable here | Install revision, enter `diag`, check units and reboot/reconnect |
| Bluetooth | Configuration/failure handling tested | Confirm Classic RFCOMM, pairing, MAC/channel and ECU protocol |
| Wi-Fi | Actual TCP sockets against emulator | Confirm reader network/IP/port and Pi/hotspot connectivity |
| Vehicle protocol | Auto-detection tested in bench | Record protocol after ECU response; absent CAN replies alone do not prove K-line |
| Stationary telemetry | Synthetic continuity passed | Ignition-on and idling captures; compare RPM/speed/coolant with vehicle state |
| Fault codes | Decoder/availability tests | Compare independent scan in a separate session; record unsupported modes; do not erase codes |
| Power/reconnect | Software adapter/broker recovery passed | Actual reader/ignition/Pi power cycles while parked |
| Display/audio | State tests and build passed | Target touchscreen/phone readability, stale displays and audible alerts |
| Browser visual review | Local URL rejected with `ERR_BLOCKED_BY_CLIENT` | Visual inspection pending; no screenshot pass claimed |
| Short drive | No road test | Proceed after stationary gates; capture and review one monitored trip |
| Optional RF/GPS/voice/vision/mesh/Android/firmware | Existing tests and source inventory only | Qualify attached components separately |

Follow [FIRST_DRIVE.md](../FIRST_DRIVE.md). Generic emissions OBD does not
establish access to Jaguar ABS, SRS, body/security modules, actuators or coding.
Those capabilities are not qualified here. Trip/fuel calculations and diagnostic
advice are estimates, not a mechanical inspection.

## Operational limits

- `/opt/drifter/.env` controls connections; `obd.yaml` documents the settings.
- Start with one explicit reader. Test each independently before using `auto`.
- Default metric polling budget is 5 requests/second across all selected PIDs;
  actual ELM response times can lower it. DTC reads run approximately every
  60 seconds; VIN is attempted once per connection.
- Adapter connection is not ECU communication. Core readings expire after 15
  seconds. The capture requires RPM/speed/coolant continuity; voltage and
  DTC/VIN availability depend on ECU support.
- `vehicle_ready` indicates recent data. `vehicle-check` is the sustained
  acceptance gate. Neither is a mechanical all-clear.
- Automatic rollback covers replaced source/unit/CLI files and previously
  running services, not pip dependencies or the whole OS. Existing `.env`,
  profiles, calibration and logs are preserved. Cockpit deployment is separate.
- Fleet users must configure `FLEET_ADMIN_USERNAME`/`FLEET_ADMIN_PASSWORD`;
  authenticated WS clients send a Bearer header.
- Active experiments require `DRIFTER_LAB_MODE=1` and `foot` mode. Leave the
  variable unset for vehicle testing. Close other apps sharing the reader.

## Protocol references checked

- [ELM327 datasheet](https://www.elmelectronics.com/wp-content/uploads/2016/07/ELM327DS.pdf): prompt framing, spaces, protocol search and formatting.
- [python-OBD command table](https://python-obd.readthedocs.io/en/latest/Command%20Tables/): PID identities and bank/sensor ordering.
- [Python socket documentation](https://docs.python.org/3/library/socket.html): reads and disconnect handling.
- [Paho migration guide](https://eclipse.dev/paho/files/paho.mqtt.python/html/migrations.html): callback API v2.
