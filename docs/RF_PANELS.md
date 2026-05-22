# RF Panels — Operator Walk-Through

This is the operator-facing reference for every RF surface in the DRIFTER cockpit. Each section says what the panel does, when to use it, what data it shows, and what to expect when the hardware behind it is missing.

## RF Intelligence tile (center column)

The big tile in the middle of the cockpit. It's your top-level read on the RF environment around the car. Four pipeline pills along the top — `rtl_433`, `dump1090`, `spectrum`, `rfaudio` — show which RF workers are currently active. Below that, a row of band rows (PMR446, ISM-433, Marine VHF-16, Airband Guard, etc.) each shows a human-readable label and the most recent power-band measurement in dBm, drawn as a horizontal meter. A heatmap strip underneath visualises the latest full spectrum sweep across 24 MHz–1766 MHz; the peak frequency is marked with a triangle. Use this tile to spot at a glance which bands have activity right now without diving into a tab. If no RTL-SDR is plugged in, the tile header reads "PLUG IN RTL-SDR — no signal until dongle present" and the live dot stays off. The ops strip at the bottom carries the three operator buttons: `PAUSE/RESUME RTL_433` (toggle), `FORCE SPECTRUM SCAN` (runs an out-of-band rtl_power sweep, ~90 s), and `SCAN EMERGENCY` (toggles rfaudio scan across the emergency band list).

## Signal Intel sub-tile

Sits inside the RF tile, fed by `drifter/rf/classification` from the URH-NG classifier. Every row is one signal the classifier has labeled in the last few minutes: frequency in MHz, modulation chip (OOK/ASK/FSK/etc.), a guessed protocol family translated to a human brand name (Came, Nice, Princeton, Schrader, KeeLoq, Somfy, etc. — falls back to the raw classifier token when unknown), and a confidence bar with a percent label. Rows flagged `NOVEL` get an amber border and a `NOVEL` badge — those are signals not seen in the baseline for the current GPS-hashed location, which is exactly what you want to notice when you're parked somewhere unfamiliar. Empty state: "AWAITING UNKNOWN SIGNAL" — that's normal until the classifier has had a few minutes of sweep data to chew on.

## RF drawer tab (quick-tune / rfaudio)

Drawer surface listed as `rfaudio · quick tune`. Tap any band button (UHF-CB Ch5, Marine VHF-16, PMR446 Ch1, Airband-Guard, etc.) and the rfaudio worker retunes the RTL-SDR to that frequency and starts demodulating audio out the USB audio dongle. The common-name (UHF-CB Ch5) is the primary label, the literal frequency (476.425 MHz · nfm) sits underneath as secondary text. The currently active band gets an amber-live border and a pulsing dot. The `STOP` button at the bottom is rendered in red (`crit`) to make it visually distinct from the band buttons. Empty state: when no SDR is present, all bands appear idle until the worker comes online. Use this when you want to listen to a specific service rather than scan emergency bands.

## v3sper drawer tab (Flipper add-on)

The Flipper Zero side of the cockpit, surfaced under the `V3SPER` drawer tab. The MODULE pill at the top of the panel tells you what add-on the Flipper is reporting over GPIO: `WIFI` (ESP32 Marauder), `SUBGHZ` (CC1101 board), or `NONE`. The cockpit only shows the panel that matches the detected hardware — no stacks of dim, useless buttons. When no add-on is detected, you get a single explicit message: "NO ADD-ON DETECTED — plug in the Wi-Fi (ESP32 Marauder) or sub-GHz (CC1101) board". With the sub-GHz board attached you get `FREQ ANALYZER`, `RAW CAPTURE` (with a capture-freq dropdown for 433.92/315/868/915 MHz), and `READ PROTOCOL`. With the Wi-Fi board attached you get `WIFI SCAN AP`, `WIFI SCAN STA`, `BLE SCAN`, `PACKET MONITOR`, `PROBE REQUEST CAPTURE`, and `PWNAGOTCHI MODE` — all passive; the deauth/beacon-spam/evil-twin Marauder commands are deliberately not wired. The rfaudio quick-tune section also lives at the bottom of this drawer for one-tap access while the Flipper is doing something else.

## CAN DISCOVERY tab

Drawer tab labeled `CAN DISC`. This is the CaringCaribou bridge for poking the vehicle's CAN bus. Four preset buttons drive `drifter/can/command` via the dashboard backend: `DISCOVER ECUs` (tooltip: "UDS scan: probes every CAN ID for an ECU response"), `LIST SERVICES` (enumerates UDS services on the ECU selected in the dropdown next to it), `DUMP IDENTIFIERS` ("Read every DID the ECU exposes"), and `FUZZ ID RANGE` ("Brute-force a range of CAN IDs and log replies"). The fuzz range bounds are picked from preset dropdowns — no free-text command inputs to mistype. Every CaringCaribou run lands a row in the results table below, sorted newest-first. FUZZ runs also drop a SavvyCAN-compatible CSV into the captures list with a one-click download link. Empty state: when `can0` is absent or down, the panel collapses to a single message — "NO CAN INTERFACE — connect OBD-II dongle" — instead of letting the operator press buttons that will time out.

## AIRSPACE tab

Drawer tab labeled `AIRSPACE`. Embedded iframe of the tar1090 web UI running locally on port 8504, fed by the dump1090 ADS-B receiver. Use this when you want the full tar1090 map instead of the lightweight radar in the `ADSB` tab. The iframe sizes to fill the drawer body (`100vh - 220px`, minimum 480 px). `OPEN FULL SCREEN` opens the same URL in a new tab. The cockpit probes `:8504/favicon.ico` once per drawer mount — if tar1090 isn't installed or isn't running, the iframe is replaced by the explicit message "tar1090 NOT INSTALLED — see docs/COMMUNITY_TOOLS_STATUS.md" rather than a broken or blank frame.

## Hardware-missing summary

| Panel | When hardware absent, you see |
| --- | --- |
| RF Intelligence tile | "PLUG IN RTL-SDR — no signal until dongle present" |
| Signal Intel | "AWAITING UNKNOWN SIGNAL" (also normal during warmup) |
| RF quick-tune | Bands remain idle, no live border |
| v3sper drawer | "NO ADD-ON DETECTED — plug in the Wi-Fi (ESP32 Marauder) or sub-GHz (CC1101) board" |
| CAN DISCOVERY | "NO CAN INTERFACE — connect OBD-II dongle" |
| AIRSPACE | "tar1090 NOT INSTALLED — see docs/COMMUNITY_TOOLS_STATUS.md" |

If any panel shows the empty state while the hardware is plugged in, check `drifter diagnose` first and then `journalctl -u drifter-<name> -f` for the relevant service (`drifter-rf`, `drifter-rfaudio`, `drifter-canbridge`, `drifter-flipper`).
