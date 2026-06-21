package com.mz1312.drifter.data.net

import com.mz1312.drifter.data.model.ApiResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket

/**
 * The diagnostic that the web dashboard can never run: when the Pi is
 * headless and "not loading", this probes each port FROM THE PHONE and tells
 * the operator which layer is broken and what to do about it.
 *
 * It mirrors the port map in CLAUDE.md / src/web_dashboard.py and bakes in the
 * subtle truths a naive port scan would get wrong — e.g. MQTT 1883 is bound to
 * loopback only since the 2026-05-18 hardening, so "closed from the phone" is
 * the CORRECT state, not a fault.
 */
enum class ProbeStatus { PASS, WARN, FAIL, SKIP }

data class ProbeResult(
    val name: String,
    val target: String,
    val status: ProbeStatus,
    val detail: String,
    val latencyMs: Long? = null,
    val remediation: List<String> = emptyList(),
)

data class DoctorReport(
    val host: String,
    val results: List<ProbeResult> = emptyList(),
    val ranAt: Long = 0L,
    val running: Boolean = false,
) {
    val ok: Boolean get() = results.none { it.status == ProbeStatus.FAIL }
    val headline: String
        get() = when {
            results.isEmpty() -> "Not run yet"
            results.any { it.status == ProbeStatus.FAIL } -> "Connection problems found"
            results.any { it.status == ProbeStatus.WARN } -> "Reachable, with warnings"
            else -> "All probes green"
        }
}

/** One TCP port the Pi is expected to expose, with its meaning + fix-it copy. */
private data class PortSpec(
    val name: String,
    val port: Int,
    /** When true, a closed/refused port is EXPECTED (not a failure). */
    val expectedClosedFromPhone: Boolean = false,
    val whatItIs: String,
    val ifDown: List<String>,
)

class ConnectionDoctor(private val api: DrifterApi, private val host: String) {

    suspend fun run(): List<ProbeResult> = coroutineScope {
        // Probe TCP ports in parallel, then the HTTP healthz handshake last
        // (it depends on 8080 being open and tells us the node's own view).
        val portJobs = PORTS.map { spec -> async(Dispatchers.IO) { probePort(spec) } }
        val portResults = portJobs.awaitAll()
        val healthResult = probeHealthz()
        portResults + healthResult
    }

    private fun probePort(spec: PortSpec): ProbeResult {
        val start = System.nanoTime()
        return try {
            Socket().use { sock ->
                sock.connect(InetSocketAddress(host, spec.port), CONNECT_TIMEOUT_MS)
            }
            val ms = (System.nanoTime() - start) / 1_000_000
            if (spec.expectedClosedFromPhone) {
                // Open but we expected it closed — e.g. MQTT exposed to the LAN.
                ProbeResult(
                    name = spec.name,
                    target = "$host:${spec.port}",
                    status = ProbeStatus.WARN,
                    detail = "Open — but ${spec.name} should be loopback-only. " +
                        "Broker may be bound to 0.0.0.0.",
                    latencyMs = ms,
                    remediation = listOf(
                        "Expected closed from the phone since the 2026-05-18 hardening.",
                        "Re-check mosquitto bind address (should be 127.0.0.1:1883).",
                    ),
                )
            } else {
                ProbeResult(spec.name, "$host:${spec.port}", ProbeStatus.PASS, spec.whatItIs, ms)
            }
        } catch (e: IOException) {
            if (spec.expectedClosedFromPhone) {
                ProbeResult(
                    name = spec.name,
                    target = "$host:${spec.port}",
                    status = ProbeStatus.PASS,
                    detail = "Closed from the phone — correct (loopback-only).",
                )
            } else {
                ProbeResult(
                    name = spec.name,
                    target = "$host:${spec.port}",
                    status = ProbeStatus.FAIL,
                    detail = "No TCP connection — ${spec.whatItIs.lowercase()} is not reachable.",
                    remediation = spec.ifDown,
                )
            }
        }
    }

    private suspend fun probeHealthz(): ProbeResult = withContext(Dispatchers.IO) {
        val start = System.nanoTime()
        when (val r = api.healthz()) {
            is ApiResult.Ok -> {
                val ms = (System.nanoTime() - start) / 1_000_000
                val h = r.value
                val (status, detail) = when (h.health) {
                    com.mz1312.drifter.data.model.Healthz.Health.OK ->
                        ProbeStatus.PASS to "Node healthy — ${h.activeCount}/${h.totalCount} services up, mode ${h.mode}."
                    com.mz1312.drifter.data.model.Healthz.Health.HW_PENDING ->
                        ProbeStatus.WARN to "Node up, ${h.servicesHwPending.size} hardware-pending (dongles not plugged in)."
                    com.mz1312.drifter.data.model.Healthz.Health.DEGRADED ->
                        ProbeStatus.FAIL to "Degraded — failed: ${h.servicesFailed.joinToString()}"
                    else -> ProbeStatus.WARN to "Reachable, status ${h.status}."
                }
                ProbeResult(
                    name = "Dashboard /healthz",
                    target = "$host:8080/healthz",
                    status = status,
                    detail = detail,
                    latencyMs = ms,
                    remediation = if (h.health == com.mz1312.drifter.data.model.Healthz.Health.DEGRADED) {
                        h.servicesFailed.map { "Restart $it (see Services tab) and check its logs." }
                    } else emptyList(),
                )
            }
            is ApiResult.Err -> ProbeResult(
                name = "Dashboard /healthz",
                target = "$host:8080/healthz",
                status = ProbeStatus.FAIL,
                detail = r.message,
                remediation = remediationFor(r),
            )
        }
    }

    private fun remediationFor(err: ApiResult.Err): List<String> = when (err.kind) {
        com.mz1312.drifter.data.model.FailureKind.FORBIDDEN -> listOf(
            "403 means you're not on the 10.42.0.0/24 hotspot subnet.",
            "Join the MZ1312_DRIFTER Wi-Fi, then re-run.",
        )
        com.mz1312.drifter.data.model.FailureKind.TIMEOUT -> listOf(
            "Port 8080 answered but the response stalled.",
            "The dashboard may be wedged on an LLM stream — restart drifter-dashboard.",
        )
        else -> listOf(
            "drifter-dashboard service is down or the IP is wrong.",
            "Verify the host on the Settings tab and that the Pi has booted.",
        )
    }

    companion object {
        const val CONNECT_TIMEOUT_MS = 1500

        private val PORTS = listOf(
            PortSpec(
                name = "HTTP dashboard",
                port = 8080,
                whatItIs = "HTTP dashboard + /healthz + /api",
                ifDown = listOf(
                    "drifter-dashboard isn't serving — the whole app depends on this.",
                    "On the Pi: sudo systemctl restart drifter-dashboard",
                    "Confirm the Pi booted and you're on the MZ1312_DRIFTER hotspot.",
                ),
            ),
            PortSpec(
                name = "Telemetry WebSocket",
                port = 8081,
                whatItIs = "Live MQTT fan-out (ws)",
                ifDown = listOf(
                    "Live telemetry won't stream to the phone.",
                    "Usually recovers when drifter-dashboard restarts.",
                ),
            ),
            PortSpec(
                name = "Audio WebSocket",
                port = 8082,
                whatItIs = "TTS alert audio (ws, binary WAV)",
                ifDown = listOf(
                    "Spoken alerts won't reach the phone speaker.",
                    "Non-fatal; restart drifter-dashboard if you want voice.",
                ),
            ),
            PortSpec(
                name = "HTTPS dashboard",
                port = 8443,
                whatItIs = "Self-signed HTTPS (browser geolocation)",
                ifDown = listOf(
                    "Only used by the browser cockpit for geolocation.",
                    "openssl may have failed at first boot — non-fatal for this app.",
                ),
            ),
            PortSpec(
                name = "RealDash bridge",
                port = 35000,
                whatItIs = "RealDash TCP feed (CAN 0x44)",
                ifDown = listOf(
                    "The RealDash app won't get vehicle telemetry.",
                    "On the Pi: sudo systemctl restart drifter-realdash",
                ),
            ),
            PortSpec(
                name = "MQTT broker",
                port = 1883,
                expectedClosedFromPhone = true,
                whatItIs = "Mosquitto (internal bus)",
                ifDown = emptyList(),
            ),
        )
    }
}
