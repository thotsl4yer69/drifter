# DRIFTER Final Field Acceptance

Software review can only take DRIFTER to a release candidate. Final sign-off is evidence-backed on the physical Pi/Jaguar.

## Deploy the release candidate

```bash
cd /home/kali/drifter
git checkout main
git pull --ff-only
sudo ./scripts/oneshot.sh --skip-apt
```

## 1. Ten unique cold boots

For each boot, power DRIFTER from the intended vehicle power path. Do not replug it to rescue the boot. With ignition RUN and the ELM327 available:

```bash
drifter acceptance cold-boot --no-replug
```

The command records the kernel boot ID, strict ELM/ECU proof, display health, critical service state, Raspberry Pi throttle/undervoltage flags and a field-dump evidence bundle. Re-running it in the same boot does not increase the count.

Repeat until `drifter acceptance status` shows `cold_boots.passed = 10`.

## 2. ELM disconnect/reconnect

Disconnect the ELM327, confirm FIELD OPS reports an actionable adapter state, reconnect it and verify the bridge returns to online adapter+ECU state without rebooting DRIFTER. Record:

```bash
drifter acceptance mark elm-recovery --pass --note "ELM unplug/replug recovered without Pi reboot"
```

## 3. White/blank display recovery

Capture evidence first, then recover while Linux remains alive:

```bash
drifter field-dump
sudo drifter display recover
drifter acceptance mark display-recovery --pass --note "display recovered without Pi power cycle"
```

## 4. Physical RTL-SDR sequence

Complete `SURVEY -> finding -> HUNT -> ZOOM -> LISTEN -> IQ CAPTURE`, then unplug/replug the SDR and confirm recovery without unresolved `device busy` contention. Record:

```bash
drifter acceptance mark rf-sequence --pass --note "survey/hunt/zoom/listen/capture + unplug/replug passed"
```

## 5. Thirty-minute Jaguar telemetry soak

```bash
drifter acceptance soak
```

The default run is 1,800 seconds. It requires continuing RPM, coolant, speed and voltage flow and rejects recorded OBD adapter/bus failure states. Evidence is written under `/opt/drifter/logs/acceptance/`.

## Final gate

```bash
drifter acceptance status
```

DRIFTER is fully field-signed-off only when it reports `"signoff_ready": true`.

Issue #67 remains open until that state is achieved. A green software review alone is not sufficient.
