#!/usr/bin/env bash
# MZ1312 DRIFTER — zram compressed-swap OOM backstop.
#
# A car-mounted 8GB Pi runs no disk swap (SD-card wear / corruption risk), so a
# transient memory spike hard-kills a service via the OOM killer instead of
# paging out. A zstd zram device provides compressed in-RAM swap (~2-3x), giving
# the kernel a cushion to ride out a spike before it has to kill anything — and
# the OOMScoreAdjust tiers (heavy=+500, diag-core=-800) decide who dies if it
# still must. Sized to a fraction of RAM. Fully graceful: if the zram module or
# zramctl is unavailable it no-ops (exit 0) rather than failing the boot.
set -u

FRACTION="${DRIFTER_ZRAM_FRACTION:-50}"   # percent of MemTotal
ALGO="${DRIFTER_ZRAM_ALGO:-zstd}"

start() {
    command -v zramctl >/dev/null 2>&1 || { echo "zramctl absent — skipping zram"; exit 0; }
    modprobe zram 2>/dev/null || { echo "zram module unavailable — skipping"; exit 0; }
    if swapon --show=NAME --noheadings 2>/dev/null | grep -q '/dev/zram'; then
        echo "zram swap already active"; exit 0
    fi
    local mem_kb size_kb dev
    mem_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)
    size_kb=$(( mem_kb * FRACTION / 100 ))
    dev=$(zramctl --find --size "${size_kb}KiB" --algorithm "$ALGO" 2>/dev/null) \
        || { echo "zramctl failed — skipping"; exit 0; }
    mkswap "$dev" >/dev/null 2>&1
    swapon -p 100 "$dev" && echo "zram swap up: $dev (${size_kb}KiB ${ALGO})"
}

stop() {
    local d
    for d in /dev/zram*; do swapoff "$d" 2>/dev/null || true; done
}

case "${1:-start}" in
    start) start ;;
    stop)  stop ;;
    *) echo "usage: $0 {start|stop}" >&2; exit 2 ;;
esac
