# RF Panels — field operator reference

The primary RTL-SDR interface is now **FIELD OPS → RF**. The older cockpit RF tiles remain useful as secondary visualisations, but they are no longer the operator control contract. Normal use must not require a terminal or knowledge of `rtl_power`, `rtl_433`, `rtl_fm`, sample rates or USB ownership.

## RF operator status

The top card reports:

- RTL-SDR hardware state: **READY / MISSING / UNKNOWN**;
- current field operation: idle, survey, hunt, capture, zoom or listen;
- current cross-process SDR owner;
- rfaudio state;
- nearby Wi-Fi/BLE counts when those feeds are available;
- progress and an actionable error if an operation fails.

Unknown hardware is never rendered as READY.

## SURVEY

`SURVEY` is the normal entry point. It:

1. stops any active rfaudio stream;
2. asks the mature RF monitor for one broad 24–1766 MHz sweep;
3. calculates a broad noise floor and ranks peaks above it;
4. de-duplicates adjacent broad peaks;
5. pauses normal `rtl_433` ownership;
6. acquires the shared SDR lease;
7. performs targeted fine scans around the strongest candidates;
8. publishes ranked findings;
9. releases the SDR and resumes normal monitoring.

The broad sweep has a timeout guard. If no forced summary arrives, the mission exits with an explicit error instead of remaining stuck in “surveying”.

## Findings

A finding may contain:

- frequency;
- peak power and local noise estimate;
- delta above local noise;
- rough occupied bandwidth;
- context for the frequency range;
- source (`adaptive-survey`, `rtl_433`, classifier);
- NEW / KNOWN / UNKNOWN status.

Frequency context is not transmitter identification. A signal inside a cellular allocation is labelled **cellular-band energy**, not “IMSI catcher”. A spectrum-only observation cannot support that stronger claim.

## HUNT

Select a finding and tap `HUNT`.

The operator screen repeatedly scans a narrow span around the selected frequency and reports:

- current relative power;
- local noise;
- delta above noise;
- recent samples;
- **STRONGER / WEAKER / STEADY** trend.

This is a proximity-hunting aid with one omnidirectional receiver. It is not true direction finding.

## ZOOM

`ZOOM ±250K` runs a targeted fine sweep around the selected signal and renders that narrow result directly in the investigation card. Use it before capture when the broad finding needs more spectral context.

## LISTEN

`LISTEN` hands the receiver to rfaudio through the same SDR lease. The UI suggests a conservative demodulation mode from frequency context and allows manual AM/NFM/WFM/USB/LSB override.

The field stack never starts a second listen/hunt/capture operation while survey still owns the mission.

## CAPTURE IQ

Choose 5, 15 or 30 seconds and tap `CAPTURE IQ`.

DRIFTER saves:

- `.sigmf-data` raw IQ;
- `.sigmf-meta` metadata;
- center frequency;
- 250 ksps sample rate;
- UTC capture time;
- configured region;
- recent GPS coordinates/accuracy when a fresh fix is available.

Captures live under `/opt/drifter/state/rf_captures/`.

## SAVE BASELINE

`SAVE BASELINE` stores the current ranked findings. A later adaptive survey treats close frequency matches as KNOWN and highlights other candidates as NEW.

The baseline is an RF-environment comparison aid, not an identity database.

## RESET RF

`RESET RF` stops the current field mission, stops rfaudio, clears pending hunt/survey state, forces the mature RF monitor to release an in-flight scan, then resumes normal passive monitoring.

Use this instead of unplugging the RTL-SDR when the receiver becomes stuck.

## Shared SDR ownership

One physical RTL-SDR cannot simultaneously be tuned to arbitrary hunt/listen/capture frequencies and also remain on every monitoring workload.

Field operations use `/opt/drifter/state/.rtl_sdr.lock` and expose the current owner in `/opt/drifter/state/rtl_sdr_owner.json`. `field_rfaudio.py` uses the same lease. Existing `rf_monitor.py` is cooperatively paused/resumed around an on-demand mission.

Expected owner examples:

- `idle`
- `rf-survey`
- `rf-hunt`
- `rf-zoom`
- `rf-capture`
- `rfaudio`

A busy receiver should therefore appear as an explicit owner/state, not a mysterious USB claim error.

## Legacy cockpit RF tiles

The older RF/spectrum, ADS-B, wardrive and BLE surfaces remain useful for situational awareness. They are **secondary views**. Any old decorative `force sweep`, `scan emergency` or similar labels must not be treated as the field control contract; the functional controls live in FIELD OPS.

## Missing hardware

When the RTL-SDR is absent, FIELD OPS shows **MISSING** once the hardware probe has reported it. Before any probe has reported, the state is **UNKNOWN**, not READY.

The acceptance requirement is that unplug/replug either recovers automatically or leaves a specific missing/busy/error state that the operator can act on without opening a shell.
