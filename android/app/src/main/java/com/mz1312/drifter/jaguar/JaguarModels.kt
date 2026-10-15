package com.mz1312.drifter.jaguar

import kotlinx.serialization.Serializable
import kotlin.math.abs

@Serializable
data class LiveSnapshot(
    val ts: Long = System.currentTimeMillis(),
    val rpm: Double? = null,
    val speedKph: Double? = null,
    val coolantC: Double? = null,
    val mafGps: Double? = null,
    val stftB1: Double? = null,
    val ltftB1: Double? = null,
    val stftB2: Double? = null,
    val ltftB2: Double? = null,
    val throttlePct: Double? = null,
    val loadPct: Double? = null,
    val intakeC: Double? = null,
    val voltage: Double? = null,
    val o2B1s1V: Double? = null,
) {
    val engineRunning: Boolean get() = (rpm ?: 0.0) >= 450.0
    val warm: Boolean get() = (coolantC ?: 0.0) >= 70.0
}

@Serializable
data class CarEvent(
    val id: String,
    val date: String,
    val kind: String,
    val subsystem: String,
    val summary: String,
    val details: String = "",
    val status: String = "open",
)

@Serializable
data class IncidentSummary(
    val id: String,
    val triggerTs: Long,
    val reason: String,
    val fileName: String,
    val sampleCount: Int,
    val firstChange: String? = null,
)

@Serializable
data class EvidenceHit(
    val title: String,
    val page: Int? = null,
    val snippet: String,
    val applicability: String = "",
    val score: Double = 0.0,
)

@Serializable
data class DiagnosisLane(
    val priority: Int,
    val title: String,
    val reason: String,
    val checks: List<String>,
    val evidence: List<String> = emptyList(),
)

enum class LinkMode(val label: String) {
    NONE("OFFLINE"),
    BLUETOOTH_ELM("BT ELM327"),
    WIFI_ELM("WI-FI ELM327"),
    DRIFTER("DRIFTER NODE"),
}

data class LinkUiState(
    val mode: LinkMode = LinkMode.NONE,
    val label: String = "Offline",
    val connected: Boolean = false,
    val busy: Boolean = false,
    val error: String? = null,
)

data class BondedElmDevice(val name: String, val address: String)

object FactorySpecs {
    const val MAF_IDLE_MIN = 3.0
    const val MAF_IDLE_MAX = 6.0
    const val STFT_NORMAL_ABS = 7.0
    const val COOLANT_WARN = 105.0
    const val COOLANT_CRITICAL = 108.0
    const val VOLTAGE_LOW_RUNNING = 11.7

    val sourceMaf = "XT303-04 Diagnosis – Flowcharts.pdf · p.37"
    val sourceAirLeak = "XT303-04 Diagnosis – Flowcharts.pdf · pp.48–49"
    val sourceO2 = "XT303-04 Diagnosis – Flowcharts.pdf · p.68"
    val sourceMisfire = "XT303-04 Diagnosis – Flowcharts.pdf · p.67"
    val sourceFuelGauge = "XT310-05am Fuel Gauge Failure or Intermittent Operation.pdf · pp.1–4"
}

object DiagnosticEngine {
    fun diagnose(query: String, s: LiveSnapshot, dtcs: List<String>): List<DiagnosisLane> {
        val q = query.lowercase()
        val codes = dtcs.map { it.uppercase() }
        val out = mutableListOf<DiagnosisLane>()

        val mentionsO2 = listOf("o2", "oxygen", "b1s1", "p013", "lambda").any(q::contains) ||
            codes.any { it.startsWith("P013") || it.startsWith("P015") }
        val mentionsMaf = q.contains("maf") || q.contains("air flow") || q.contains("stutter") || q.contains("stall") || q.contains("idle")
        val lean = listOfNotNull(s.stftB1, s.stftB2, s.ltftB1, s.ltftB2).any { it > 12.0 } ||
            codes.any { it == "P0171" || it == "P0174" }
        val misfire = q.contains("misfire") || q.contains("stutter") || q.contains("stall") ||
            codes.any { it.startsWith("P030") || it.startsWith("P035") || it.startsWith("P136") }

        if (mentionsO2) out += DiagnosisLane(
            1,
            "Verify Bank 1 upstream O₂ installation",
            "Your current fault history and B1S1 concern make the sensor, connector and exhaust sealing worth proving before replacing anything else.",
            listOf(
                "Bank 1 upstream = right-hand bank HO2S 1/1 / UHEGO; Jaguar identifies its connector as grey.",
                "Inspect the sensor seat, bung/thread and nearby exhaust joints for leakage before the sensor.",
                "Check the grey connector for pin damage, tension and contamination; compare harness routing against the other bank.",
                "With warm closed-loop data, compare Bank 1 response against Bank 2 rather than judging a single voltage in isolation.",
            ),
            listOf(FactorySpecs.sourceO2),
        )

        val maf = s.mafGps
        if (mentionsMaf || (s.warm && (maf != null && (maf < FactorySpecs.MAF_IDLE_MIN || maf > FactorySpecs.MAF_IDLE_MAX)))) {
            out += DiagnosisLane(
                if (out.isEmpty()) 1 else out.size + 1,
                "MAF / intake integrity",
                if (maf != null && s.warm) "Current MAF is ${"%.1f".format(maf)} g/s; factory warm-idle target is 3.0–6.0 g/s." else "Jaguar gives a measurable MAF baseline for warm idle, so this can be tested instead of guessed.",
                listOf(
                    "Warm the engine fully and observe MAF at stable idle. Target: 3.0–6.0 g/s.",
                    "Inspect the complete intake path between air box, MAF and throttle for splits, loose clamps or an unplugged/poor connector.",
                    "Raise engine speed smoothly: MAF should rise with airflow; a flat or erratic signal is significant.",
                    "Do not condemn the MAF until unmetered-air leaks are excluded.",
                ),
                listOf(FactorySpecs.sourceMaf),
            )
        }

        val warmTrims = listOfNotNull(s.stftB1, s.stftB2)
        if (lean || (s.warm && warmTrims.any { abs(it) > FactorySpecs.STFT_NORMAL_ABS })) {
            out += DiagnosisLane(
                out.size + 1,
                "Unmetered-air / vacuum leak test",
                "Jaguar expects STFT on both banks to fluctuate around 0% ±7% when warm. Positive correction beyond that makes air leakage a direct test path.",
                listOf(
                    "Compare STFT Bank 1 vs Bank 2 at warm idle and again around 2,500 rpm.",
                    "Inspect both breather pipes, brake-servo vacuum pipe, EVAP purge line, IMT valves, MAP sensor seal and upper/lower inlet-manifold gaskets.",
                    "A large positive trim at idle that improves with rpm strongly supports an intake/vacuum leak.",
                    "If both banks move lean together, prioritise shared intake/MAF/fuel causes; if one bank diverges, prioritise that bank's leak/O₂ path.",
                ),
                listOf(FactorySpecs.sourceAirLeak),
            )
        }

        if (misfire) out += DiagnosisLane(
            out.size + 1,
            "Misfire / ignition correlation",
            "Stutter and stall can be ignition-related, but the test should follow DTC and live-data evidence rather than shotgun coil replacement.",
            listOf(
                "Read stored and pending DTCs; note whether the fault is random (P0300) or cylinder-specific (P0301–P0306).",
                "Inspect coil connectors and plug wells for oil/water; compare suspect cylinder hardware with a known-good cylinder.",
                "Resolve strong MAF/air-leak evidence first if trims show the engine is being driven lean.",
                "After a repair, clear only after recording the original codes and confirm with a repeat idle + road test.",
            ),
            listOf(FactorySpecs.sourceMisfire),
        )

        if (q.contains("fuel gauge") || q.contains("half tank") || q.contains("half full")) out += DiagnosisLane(
            out.size + 1,
            "Fuel-level sender / harness bulletin",
            "Jaguar issued a specific X-Type bulletin for fuel-gauge failure or intermittent operation, matching the half-scale symptom better than generic cluster guessing.",
            listOf(
                "Inspect both level sender circuits and in-tank harness before replacing the instrument cluster.",
                "Compare sender resistance/level movement and look for an open/intermittent harness connection.",
                "Use the Jaguar sender/harness repair bulletin as the primary procedure.",
            ),
            listOf(FactorySpecs.sourceFuelGauge),
        )

        if ((s.coolantC ?: 0.0) >= FactorySpecs.COOLANT_WARN) out += DiagnosisLane(
            out.size + 1,
            "Cooling system priority",
            "Coolant is ${"%.1f".format(s.coolantC)}°C, high enough to move cooling-fan operation ahead of non-critical drivability work.",
            listOf("Confirm both fan operation states.", "Inspect cooling-fan module power/ground and related fuses.", "Stop the diagnostic run if temperature continues rising."),
            listOf("XT303-S932 Cooling Fans Inoperative.pdf · p.1"),
        )

        if (out.isEmpty()) out += DiagnosisLane(
            1,
            "Capture evidence before replacing parts",
            "No single factory-backed branch is strong enough from the current input.",
            listOf(
                "Scan stored + pending DTCs.",
                "Capture warm idle RPM, MAF, STFT/LTFT both banks, coolant, throttle and voltage.",
                "Press CAPTURE INCIDENT when the fault happens so the preceding 90 seconds are preserved.",
                "Search the workshop library using the exact DTC or symptom after capture.",
            ),
        )
        return out.mapIndexed { i, lane -> lane.copy(priority = i + 1) }
    }
}

object OfflineKnowledge {
    val cards = listOf(
        EvidenceHit("XT303-04 Diagnosis – Flowcharts.pdf", 68, "Upstream sensor = UHEGO with grey connector; downstream sensor = HEGO with black connector. Use this to identify B1S1/B1S2 correctly.", "2002 MY-on X-Type"),
        EvidenceHit("XT303-04 Diagnosis – Flowcharts.pdf", 37, "Mass Air Flow Meter: run engine to normal operating temperature. Expected reading at idle: 3.0–6.0 grams/second.", "2002 MY-on X-Type"),
        EvidenceHit("XT303-04 Diagnosis – Flowcharts.pdf", 48, "Air leakage flowchart: STFT values should fluctuate around 0% ±7%; inspect vacuum/breather pipes and inlet plumbing.", "2002 MY-on X-Type"),
        EvidenceHit("XT303-04 Diagnosis – Flowcharts.pdf", 49, "Air-leak continuation checks air-cleaner outlet, bank breathers, brake-servo vacuum, EVAP purge, IMT valves, MAP seal and inlet-manifold gaskets.", "2002 MY-on X-Type"),
        EvidenceHit("XT310-05am Fuel Gauge Failure or Intermittent Operation.pdf", 2, "Factory fuel-level sender/harness repair bulletin for X-Type gauge failure/intermittent operation.", "X-Type"),
        EvidenceHit("XT303-02 Engine Misfire_MIL Illumination.pdf", 1, "Jaguar technical bulletin covering engine misfire and MIL illumination.", "X-Type"),
        EvidenceHit("XT303-05 Hesitation When Cold.pdf", 1, "Misfire/hesitation when cold with possible MIL illumination; factory bulletin diagnostic context.", "2002 MY X-Type"),
        EvidenceHit("XT303-S953 Driveline Shudder_Hesitation.pdf", 1, "Factory technical information for driveline shudder/hesitation complaints.", "X-Type"),
        EvidenceHit("XT303-S932 Cooling Fans Inoperative.pdf", 1, "Overheating / cooling fans inoperative; inspect cooling fan module path.", "2002–03 MY X-Type"),
        EvidenceHit("Additional Diagnostics for Instrument Cluster.pdf", 1, "Additional instrument-cluster diagnostics for electrical/display faults.", "X-Type"),
        EvidenceHit("2004XTypeElectrical.pdf", null, "2004 model-year electrical guide: circuits, connectors, fuses, grounds and component wiring.", "2004 X-Type"),
        EvidenceHit("X-Type2005ElecGuideE19616on.pdf", null, "2005 electrical guide, useful for MY05-upgrade wiring and connector cross-checks.", "2005 X-Type"),
        EvidenceHit("3a. Powertrain2.pdf", null, "Factory workshop powertrain procedures: engine mechanical, cooling, fuel and intake systems.", "X-Type workshop"),
        EvidenceHit("3b. Powertrain cont.pdf", null, "Factory workshop engine management, DTC pinpoint tests and powertrain continuation.", "X-Type workshop"),
        EvidenceHit("4. Electrical.pdf", null, "Factory electrical workshop volume including battery/charging and electrical diagnosis.", "X-Type workshop"),
        EvidenceHit("2. Chassis.pdf", null, "Factory chassis workshop volume: brakes, suspension, steering and driveline procedures.", "X-Type workshop"),
    )

    fun search(query: String): List<EvidenceHit> {
        val terms = query.lowercase().split(Regex("[^a-z0-9]+"))
            .filter { it.length >= 2 }.toSet()
        if (terms.isEmpty()) return cards.take(8)
        return cards.map { hit ->
            val hay = "${hit.title} ${hit.snippet} ${hit.applicability}".lowercase()
            val score = terms.count { hay.contains(it) }.toDouble() / terms.size
            hit.copy(score = score)
        }.filter { it.score > 0.0 }.sortedByDescending { it.score }.take(10)
    }
}
