# MAZLABZ DRIFTER VIM — Founding Beta Quick Start

This is the shortest supported path from a Raspberry Pi checkout to a field-testable DRIFTER node.

## 0. Before starting

Recommended first-beta hardware:

- Raspberry Pi 5;
- suitable storage and a stable Pi power path;
- touchscreen or browser access to the cockpit;
- one compatible OBD interface at a time for first validation;
- a vehicle you own or are authorised to test.

The primary acceptance vehicle remains the 2004 Jaguar X-Type. Other vehicle/adapter combinations are beta evidence, not assumed compatibility.

## 1. Get the code

On the Pi:

```bash
cd /home/kali
git clone https://github.com/thotsl4yer69/drifter.git
cd drifter
git checkout main
```

If the repository already exists:

```bash
cd /home/kali/drifter
git checkout main
git pull --ff-only
```

## 2. Deploy

Fresh/first deployment:

```bash
cd /home/kali/drifter
sudo ./scripts/oneshot.sh
```

Subsequent code-only deployment when system packages are already installed:

```bash
sudo ./scripts/oneshot.sh --skip-apt
```

The deploy contract must end successfully. If it does not, stop and capture the failure instead of repeatedly power-cycling.

## 3. Confirm the node

```bash
curl -fsS http://127.0.0.1:8080/healthz | python3 -m json.tool
drifter diagnose
```

A bench node without optional hardware may report hardware-pending/degraded states. Record them; do not invent missing hardware as READY.

## 4. Keep the beta in the vehicle profile

A fresh node resolves to the lean `diag` mode. That is the recommended founding-beta profile:

```bash
sudo drifter mode diag
```

The broader research `foot` / `both` personas are not part of the DRIFTER VIM product beta.

## 5. Connect the OBD adapter

Normal operator path:

**FIELD OPS → VEHICLE → AUTO DETECT**

Then use:

**TEST ECU**

The UI must distinguish:

1. adapter unreachable;
2. adapter connected / ECU waiting;
3. ECU online;
4. live PID flow.

Engineering fallback:

```bash
drifter obd status
drifter obd test
```

## 6. Run the acceptance sequence

The CLI exposes the physical acceptance harness:

```bash
drifter acceptance --help
```

For the primary vehicle gate, record:

- cold-start result;
- live RPM/coolant/speed/voltage continuity;
- adapter unplug/replug behaviour;
- display recovery;
- incident capture;
- field evidence bundle.

See [BETA_TESTER_GUIDE.md](BETA_TESTER_GUIDE.md) and [FIRST_DRIVE.md](../FIRST_DRIVE.md) before the road session.

## 7. Capture a failure correctly

If anything fails:

```bash
drifter field-dump
```

Do not post VINs, API keys, Wi-Fi passwords or hotspot credentials. Redact them before attaching evidence to a public issue.

## 8. Submit the vehicle test

Open the repository issue form:

**Issues → New issue → DRIFTER VIM founding beta vehicle test**

One issue = one unique vehicle + adapter combination.

The compatibility matrix advances only from physical evidence:

**Target → Observed → Functional → Validated**

## 9. Update behaviour

The deployed node includes a conservative updater that follows `origin/main`, refuses dirty/non-main checkouts, preflights candidates, defers while live telemetry indicates the vehicle is active, and rolls back on failed deployment.

For beta debugging, always include the running commit:

```bash
cd /home/kali/drifter
git rev-parse HEAD
```
