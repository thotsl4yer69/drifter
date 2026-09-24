# MAZLABZ DRIFTER VIM — Founding-Beta Hardware Authority

**Stage:** reference-node capture + product BOM validation  
**Rule:** only exact hardware observed on the physical node is labelled reference hardware. Candidate parts are not silently promoted into the BOM.

## Reference development node — currently documented

| Function | Current evidence | Product status |
|---|---|---|
| Compute | Raspberry Pi 5; fleet inventory records 16 GB RAM | Reference development compute |
| Storage | Fleet inventory records 128 GB microSD, no NVMe | Development storage; endurance/product choice not locked |
| Vehicle interface | ELM327 path is primary Jaguar acceptance transport | Exact adapter model/firmware still needs physical capture |
| Raw CAN | CANable/SocketCAN architecture supported | Optional; not primary Jaguar beta transport |
| Display | Local SPI framebuffer display is present in software/service architecture | Exact panel/controller/revision still needs physical capture |
| Power | Vehicle-power cold-boot gate exists | Permanent power hardware not locked |
| Audio | Linux audio path supported | Optional for core VIM; Pi 5 has no built-in analogue headphone jack |
| GPS | Supported as hardware-optional service | Optional for core VIM |
| RTL-SDR / RF | Wider R&D platform capability | Not required for VIM product acceptance |

## Founding-beta minimum

A tester needs:

1. Raspberry Pi capable of running the current DRIFTER deployment;
2. reliable storage;
3. one OBD transport for the test;
4. local/browser access to FIELD OPS;
5. power suitable for the test;
6. a vehicle they own or are authorised to test.

The beta issue form records the exact Pi, adapter and display so compatibility evidence stays attributable.

## Reference-node capture required before hardware pricing

Capture these from the physical node:

### Pi / OS

```bash
cat /proc/device-tree/model; echo
getconf LONG_BIT
free -h
lsblk -o NAME,SIZE,TYPE,MODEL
uname -a
```

### USB / OBD interfaces

```bash
lsusb
ls -l /dev/serial/by-id/ 2>/dev/null || true
bluetoothctl devices 2>/dev/null || true
nmcli -f NAME,TYPE,DEVICE connection show
```

For a USB/serial OBD adapter:

```bash
udevadm info -a -n /dev/ttyUSB0 | grep -E 'idVendor|idProduct|serial' | head -20
```

Use the actual device path returned on the node rather than assuming `ttyUSB0`.

### Display

```bash
ls -l /dev/fb* 2>/dev/null || true
for f in /sys/class/graphics/fb*/name; do echo "$f: $(cat "$f" 2>/dev/null)"; done
dmesg | grep -Ei 'drm|framebuffer|fb[0-9]|spi|ili|st77|display' | tail -100
```

Then photograph the rear PCB/label so the exact model and controller can be recorded.

### Power

```bash
vcgencmd get_throttled
vcgencmd measure_volts core 2>/dev/null || true
```

Also record the actual:
- vehicle source/fuse circuit;
- converter/UPS make and model;
- output rating;
- cable;
- shutdown/ride-through behaviour.

## Product BOM lock criteria

Do not call a hardware list the **DRIFTER VIM BOM** until:

- [ ] exact reference adapter is identified;
- [ ] exact reference display is identified;
- [ ] exact vehicle power path is identified;
- [ ] 10/10 cold boots pass on that power path;
- [ ] 30-minute telemetry soak passes;
- [ ] power-loss recovery passes;
- [ ] thermals are measured in the intended enclosure;
- [ ] second vehicle + adapter combination is validated;
- [ ] clean install is reproduced from blank storage;
- [ ] unit cost and assembly time are measured.

Until then, purchasing documents should say **development/reference hardware**, not production BOM.

## Commercial packaging candidates

These remain decisions, not promises:

### Software beta
User supplies Pi/display/adapter.

### Founding kit
MAZLABZ supplies a tested parts bundle after BOM lock; customer assembles/installs.

### Preconfigured node
MAZLABZ supplies configured compute + storage + display/power enclosure; vehicle-specific OBD adapter remains selectable.

### Finished appliance
Only after automotive power, enclosure, thermal, support and compatibility evidence justify it.

The commercial path should advance in that order unless field evidence proves a simpler path.
