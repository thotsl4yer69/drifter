# DRIFTER Wiring Guide

## Connections (3 total)

### 1. CAN Bus (Car → Pi)

```
OBD-II Port (under steering column)
  Pin 6  (CAN-H) ──→ USB2CANFD screw terminal: H
  Pin 14 (CAN-L) ──→ USB2CANFD screw terminal: L

USB2CANFD ──USB──→ Pi 5 USB port
```

Use an OBD-II pigtail cable with bare wire ends. Screw CAN-H and CAN-L
into the green screw terminals on the USB2CANFD adapter. Plug the USB
end into any Pi 5 USB port.

The X-Type uses ISO 15765 CAN at 500 kbps. The USB2CANFD handles this
natively via the gs_usb driver — no configuration needed.

### 2. Audio (Pi → Pioneer)

```
Pi 5 (3.5mm headphone jack)
  ──→ Ground Loop Isolator (inline 3.5mm)
  ──→ Pioneer AUX input (3.5mm or RCA)
```

The ground loop isolator is critical. Without it, you'll hear engine RPM
whine through the speakers caused by ground potential differences between
the Pi's power supply and the car's electrical system.

### 3. Power (Whatever → Pi)

For bench testing or basic use:
- USB-C battery pack → Pi 5 USB-C power port
- Car USB port → Pi 5 (may brownout under load — test first)

For permanent install (future):
- 12V fuse tap → Geekworm X1205 UPS → Pi 5 (via pogo pins)
- Or: 12V fuse tap → buck converter (5V/5A USB-C) → Pi 5

## Phone Setup

```
Pi 5 Wi-Fi Hotspot (MZ1312_DRIFTER)
  ──Wi-Fi──→ Phone (RealDash app, MQTT to 10.42.0.1:1883)
  ──USB────→ Pioneer (Android Auto projects RealDash to screen)
```

Use WIRED Android Auto (USB cable from phone to Pioneer).
Do NOT use wireless AA — it conflicts with the Pi hotspot connection.

## Pin Reference (USB2CANFD V1 Screw Terminals)

```
┌─────────────────────┐
│  H    L    GND      │  ← Green screw terminals
│  │    │    │        │
│  CAN  CAN  (not     │
│  High Low  needed)  │
└─────────────────────┘
```

## OBD-II Port Pinout (Relevant Pins Only)

```
    ┌─────────────────┐
    │ 1  2  3  4  5   │
    │                  │  Standard OBD-II connector
    │ 6  7  8  9  10  │  (under driver dash)
    │    11 12 13 14  │
    │  15 16          │
    └─────────────────┘

Pin 6:  CAN-H (ISO 15765)  → Connect this
Pin 14: CAN-L (ISO 15765)  → Connect this
Pin 16: Battery positive    → (not needed, power via USB)
Pin 4:  Chassis ground      → (not needed)
Pin 5:  Signal ground       → (not needed)
```
