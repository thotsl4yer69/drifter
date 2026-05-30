# DRIFTER — GPS source options

The cockpit map follows the vehicle when ANY publisher emits to
`drifter/gps/fix`. Three paths, listed cheapest-to-most-effort:

---

## Option 1 — USB GPS dongle on the Pi (recommended)

**What:** A ~$15 USB GPS receiver. Plug into a Pi USB port, gpsd
takes care of decoding, the new `drifter-gps` service reads gpsd
and publishes to MQTT.

**Hardware that works (any of these):**
- u-blox 7 chipset modules (VK-172, VK-162, GlobalSat ND-100S, generic "USB GPS receiver" on AliExpress) — most reliable, hot indoor reception
- Adafruit Ultimate GPS USB ($45 — overkill but bulletproof)
- USB-to-serial dongles attached to a NMEA receiver

**Setup:**
1. `sudo ./install.sh` — installs gpsd + gpsd-clients + the
   `drifter-gps.service` unit. (Already runs as part of the
   standard deploy.)
2. Plug in the dongle. `dmesg | tail` should show the device
   enumerate (commonly `/dev/ttyACM0` for u-blox).
3. Edit `/etc/default/gpsd`:
   ```
   START_DAEMON="true"
   DEVICES="/dev/ttyACM0"   # or whatever dmesg showed
   GPSD_OPTIONS="-n"        # don't wait for a client to start polling
   USBAUTO="true"
   ```
4. `sudo systemctl restart gpsd && sudo systemctl restart drifter-gps`
5. Verify with `cgps -s` — should show satellite count + lock + lat/lon
   within ~30s of getting a clear sky view.
6. Confirm MQTT publish: `mosquitto_sub -h localhost -t drifter/gps/fix -v`
   shows fixes flowing.

**What the publisher emits per fix (retained):**
```json
{"lat": -37.8136, "lng": 144.9631,
 "alt_m": 35.4, "speed_mps": 23.6, "track_deg": 178.0,
 "mode": 3, "ts": 1778300000.0}
```

**Failure modes the service handles:**
- gpsd not yet up → exponential backoff reconnect (1s → 30s)
- MQTT broker not yet up → 10× linear backoff at startup
- gpsd disconnect mid-stream → reconnect loop
- TPV with no fix (mode 0/1) → silently skipped, not published

---

## Option 2 — Phone tethered to the hotspot (works today, zero new code)

**What:** Render the dashboard on a phone instead of the Pi monitor.
The phone's browser geolocation API uses the phone's GPS chip, which
is already wired to the cockpit's `cockpitGeo()` JS handler.

**Setup:**
1. Phone joins `MZ1312_DRIFTER` Wi-Fi.
2. Open `http://10.42.0.1:8080/` in the phone browser.
3. Browser asks for location permission — accept.
4. Map follows the phone's GPS without any other configuration.

**Tradeoff:** the phone is the display, not the 15.6" monitor.
Useful when:
- You want a passenger holding the dashboard for situational awareness
- You're verifying the cockpit works before buying a GPS dongle
- The Pi is bench-mounted and only the phone is in the vehicle

---

## Option 3 — Phone GPS bridged to MQTT over Wi-Fi (operator-side, no DRIFTER code)

**What:** The phone publishes its GPS to `drifter/gps/fix` directly.
Useful when you want the 15.6" dashboard AND don't have a USB dongle.

**Android (Termux):**
1. Install Termux + Termux:API from F-Droid.
2. `pkg install mosquitto-clients termux-api`
3. Grant Termux location permission in Android settings.
4. Run a small loop on the phone (script saved as `gps-bridge.sh`):
   ```bash
   while true; do
     loc=$(termux-location -p gps -r last)
     lat=$(echo "$loc" | jq -r '.latitude')
     lng=$(echo "$loc" | jq -r '.longitude')
     if [ -n "$lat" ] && [ "$lat" != "null" ]; then
       mosquitto_pub -h 10.42.0.1 -t drifter/gps/fix \
         -m "{\"lat\":$lat,\"lng\":$lng,\"ts\":$(date +%s)}"
     fi
     sleep 2
   done
   ```
5. Run with phone tethered to `MZ1312_DRIFTER` hotspot.

**iOS:**
- No comparable stock-OS bridge. Workarounds via Pythonista / a-Shell
  exist but are operationally fragile. Use option 1 or 2 instead.

**Tradeoff:** Operationally fragile. Phone screen-locked? Bridge stops.
Termux killed by Android battery optimisation? Bridge stops. Wi-Fi
disconnect? Bridge stops. Use only when you can't get a USB dongle
and the phone-as-display option doesn't fit your setup.

---

## Decision matrix

| Path | Hardware buy | Operates in-vehicle | Operational fragility |
|------|---|---|---|
| 1. USB GPS dongle on Pi | ~$15 | Yes (15.6" + dongle) | Low — gpsd is rock-solid once configured |
| 2. Phone-as-display     | None | Yes (phone is the display) | Medium — phone battery + screen-on |
| 3. Phone-bridge to MQTT | None | Yes (15.6" + phone bridge) | High — Android battery / Termux quirks |

**Default recommendation: option 1 + option 2 as backup.** Both can
coexist; whichever publisher delivers a fresh fix to `drifter/gps/fix`
wins by recency on the cockpit side.
