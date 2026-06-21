package com.mz1312.drifter.data

/**
 * Static operator knowledge baked into the app so a headless node can be
 * triaged at the car without SSH. For each systemd unit: what it does, whether
 * it legitimately idles until a dongle is plugged in (hardware-pending), and
 * the concrete steps to bring it back. Sourced from CLAUDE.md, AGENTS.md and
 * src/config.py.
 */

enum class ServiceCategory(val label: String) {
    TELEMETRY("Vehicle telemetry"),
    SAFETY("Driver safety"),
    AI("AI / LLM"),
    VOICE("Voice"),
    RF("RF / SDR"),
    BLE("BLE"),
    NAV("Navigation"),
    INFRA("Infrastructure"),
    CARSENAL("Carsenal (CAN offense)"),
    RECON("Recon / Kali arsenal"),
    COUNTER("Counter-surveillance"),
    OTHER("Other"),
}

data class ServiceDoc(
    val unit: String,
    val title: String,
    val role: String,
    val category: ServiceCategory,
    /** True when "inactive" is fine until hardware is attached — never a fault on the bench. */
    val hardwarePending: Boolean = false,
    /** True when the unit is backed by the Kali userland in the node (recon/offense). */
    val kaliBacked: Boolean = false,
    val remediation: List<String> = emptyList(),
)

object Knowledge {

    private val DOCS: Map<String, ServiceDoc> = listOf(
        // ── Core telemetry ───────────────────────────────────────────
        ServiceDoc(
            "drifter-canbridge", "CAN Bridge", "OBD-II / CAN bus → MQTT telemetry",
            ServiceCategory.TELEMETRY, hardwarePending = true,
            remediation = listOf(
                "Needs the USB-CAN / OBD-II adapter plugged into the Jag's port.",
                "Check the can0 link: ip -details link show can0 (look for state UP).",
                "On the bench with no adapter this is EXPECTED to be inactive.",
                "Restart: sudo systemctl restart drifter-canbridge",
            ),
        ),
        ServiceDoc("drifter-alerts", "Alert Engine", "Threshold + safety alerting", ServiceCategory.SAFETY,
            remediation = listOf("Core safety service — should always be up.", "Restart: sudo systemctl restart drifter-alerts")),
        ServiceDoc("drifter-logger", "Logger", "Telemetry log writer (SQLite)", ServiceCategory.TELEMETRY),
        ServiceDoc("drifter-anomaly", "Anomaly Monitor", "Statistical telemetry anomaly detector", ServiceCategory.SAFETY),
        ServiceDoc("drifter-batcher", "Batcher", "Rolling telemetry stats publisher", ServiceCategory.TELEMETRY),
        ServiceDoc("drifter-trip", "Trip Computer", "Per-trip distance + fuel from MAF", ServiceCategory.TELEMETRY),
        ServiceDoc("drifter-thresholds", "Thresholds", "Adaptive baseline learner", ServiceCategory.SAFETY),
        ServiceDoc("drifter-realdash", "RealDash Bridge", "RealDash TCP feed on :35000", ServiceCategory.TELEMETRY,
            remediation = listOf("Feeds the RealDash phone app over TCP 35000.", "Restart: sudo systemctl restart drifter-realdash")),

        // ── AI / voice ───────────────────────────────────────────────
        ServiceDoc("drifter-analyst", "Analyst", "LLM session analyst", ServiceCategory.AI,
            remediation = listOf("Heavy (LLM). Only expected in drive/both mode.", "If OOM, the watchdog demotes to diag mode by design.")),
        ServiceDoc("drifter-reporter", "Reporter", "Post-drive markdown report via LLM", ServiceCategory.AI),
        ServiceDoc("drifter-vivi", "Vivi", "Voice-assistant LLM brain", ServiceCategory.AI, hardwarePending = true,
            remediation = listOf("Needs Ollama + Piper present.", "Heavy; only in drive/both. Inactive in diag is correct.")),
        ServiceDoc("drifter-voice", "Voice", "TTS for alerts", ServiceCategory.VOICE),
        ServiceDoc("drifter-voicein", "Voice In", "Wake-word + STT", ServiceCategory.VOICE, hardwarePending = true,
            remediation = listOf(
                "Needs a USB microphone (e.g. C-Media dongle on plughw:0,0).",
                "Writes /opt/drifter/voicein.heartbeat — /healthz flips it false if the mic drops.",
                "Plug in the mic, then: sudo systemctl restart drifter-voicein",
            )),

        // ── RF / BLE / nav ───────────────────────────────────────────
        ServiceDoc("drifter-rf", "RF / SDR", "RTL-SDR TPMS + spectrum", ServiceCategory.RF, hardwarePending = true,
            remediation = listOf(
                "Needs the RTL-SDR dongle in a USB port (lsusb: 0bda:2832/2838).",
                "Inactive with no dongle is expected.",
                "Restart: sudo systemctl restart drifter-rf",
            )),
        ServiceDoc("drifter-rfaudio", "RF Audio", "On-demand emergency-band receiver", ServiceCategory.RF, hardwarePending = true),
        ServiceDoc("drifter-bleconv", "BLE Scanner", "Passive BLE (tracker) scan", ServiceCategory.BLE, hardwarePending = true,
            remediation = listOf("Needs hci0 (Pi 5 onboard BLE) up.", "Check: hciconfig hci0 / bluetoothctl show")),
        ServiceDoc("drifter-gps", "GPS", "gpsd → MQTT position", ServiceCategory.NAV, hardwarePending = true,
            remediation = listOf("Needs a USB GPS dongle + gpsd.", "No dongle → inactive is fine; phone can inject a fix too.")),

        // ── Infrastructure ───────────────────────────────────────────
        ServiceDoc("drifter-dashboard", "Dashboard", "HTTP/WS HUD + /healthz", ServiceCategory.INFRA,
            remediation = listOf(
                "If this is down the app can't reach the node at all.",
                "On the Pi console: sudo systemctl restart drifter-dashboard",
            )),
        ServiceDoc("drifter-hotspot", "Hotspot", "MZ1312_DRIFTER Wi-Fi AP", ServiceCategory.INFRA,
            remediation = listOf("If down, your phone can't join the node's network.", "Recover from the Pi: sudo systemctl restart drifter-hotspot")),
        ServiceDoc("drifter-homesync", "Home Sync", "rsync to the home node", ServiceCategory.INFRA),
        ServiceDoc("drifter-watchdog", "Watchdog", "Service health + memory/thermal demotion", ServiceCategory.INFRA,
            remediation = listOf("Auto-demotes to diag mode under pressure — by design.")),
        ServiceDoc("drifter-weather", "Weather", "OpenWeatherMap poller", ServiceCategory.NAV,
            remediation = listOf("Idles when OWM_API_KEY is unset (no fault).")),
        ServiceDoc("drifter-location", "Location", "Google Elevation + Places", ServiceCategory.NAV,
            remediation = listOf("Idles when the Google key is unset (no fault).")),
        ServiceDoc("drifter-lcd", "LCD", "3.5\" SPI LCD dashboard", ServiceCategory.INFRA, hardwarePending = true,
            remediation = listOf("Needs /dev/fb1 (SPI LCD) wired. Inactive without it is fine.")),
        ServiceDoc("drifter-feeds", "Feeds", "ADS-B aircraft aggregator", ServiceCategory.RF),
        ServiceDoc("drifter-autoconnect", "Auto-connect", "Wi-Fi AP auto-connect", ServiceCategory.INFRA),

        // ── Carsenal (CAN offense) ───────────────────────────────────
        ServiceDoc("drifter-can-discovery", "CAN Discovery", "CaringCaribou UDS / fuzz discovery", ServiceCategory.CARSENAL,
            hardwarePending = true, kaliBacked = true,
            remediation = listOf(
                "Carsenal tool — needs the CAN adapter AND caringcaribou/urh installed (Kali userland).",
                "Drive-only; runs against can0. Inactive without deps is expected.",
            )),

        // ── Recon / Kali arsenal (foot mode) ─────────────────────────
        ServiceDoc("drifter-wardrive", "Wardrive", "Active Wi-Fi/BT recon", ServiceCategory.RECON, kaliBacked = true,
            remediation = listOf("Foot-mode only. Switch to foot mode to enable.")),
        ServiceDoc("drifter-flipper", "Flipper Bridge", "Flipper Zero serial bridge", ServiceCategory.RECON,
            hardwarePending = true, kaliBacked = true,
            remediation = listOf("Needs a Flipper Zero on USB serial.", "No device → inactive is fine.")),
        ServiceDoc("drifter-opsec", "OPSEC Dashboard", "Arsenal control UI on :8090", ServiceCategory.RECON, kaliBacked = true,
            remediation = listOf("Foot-mode only; runs the :8090 arsenal console.")),
        ServiceDoc("drifter-kismet", "Kismet", "Wi-Fi/BLE recon daemon", ServiceCategory.RECON, hardwarePending = true, kaliBacked = true,
            remediation = listOf("Needs kismet installed (Kali) + a capable Wi-Fi adapter.")),
        ServiceDoc("drifter-kismet-bridge", "Kismet Bridge", "Kismet REST → MQTT", ServiceCategory.RECON, hardwarePending = true, kaliBacked = true),
        ServiceDoc("drifter-wifi-audit", "Wi-Fi Audit", "bettercap PMKID/handshake", ServiceCategory.RECON, hardwarePending = true, kaliBacked = true,
            remediation = listOf("Needs bettercap installed (Kali) + monitor-mode adapter.")),
        ServiceDoc("drifter-marauder", "Marauder", "ESP32 Marauder bridge", ServiceCategory.RECON, hardwarePending = true, kaliBacked = true,
            remediation = listOf("Needs an ESP32 Marauder on serial.")),
        ServiceDoc("drifter-hid", "HID / BadUSB", "Rubber Ducky / HID injection", ServiceCategory.RECON, kaliBacked = true,
            remediation = listOf("ARM→CONFIRM→RUN gated. Foot-mode only.")),
        ServiceDoc("drifter-fly-catcher", "Fly Catcher", "ADS-B IMSI-catcher detector", ServiceCategory.RECON, hardwarePending = true, kaliBacked = true),

        // ── Counter-surveillance ─────────────────────────────────────
        ServiceDoc("drifter-ghost", "Ghost Protocol", "Counter-surveillance posture", ServiceCategory.COUNTER, kaliBacked = true),
        ServiceDoc("drifter-ghost-voice", "Ghost Voice", "Counter-surveillance alert TTS", ServiceCategory.COUNTER),
    ).associateBy { it.unit }

    fun docFor(unit: String): ServiceDoc = DOCS[unit] ?: ServiceDoc(
        unit = unit,
        title = unit.removePrefix("drifter-").replaceFirstChar { it.uppercase() },
        role = "Drifter service",
        category = ServiceCategory.OTHER,
        remediation = listOf("Inspect on the Pi: journalctl -u $unit -n 100"),
    )

    fun categoryOf(unit: String): ServiceCategory = docFor(unit).category
}
