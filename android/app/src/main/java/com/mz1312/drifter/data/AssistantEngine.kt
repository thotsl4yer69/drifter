package com.mz1312.drifter.data

import com.mz1312.drifter.data.model.Healthz
import com.mz1312.drifter.data.net.ProbeResult
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Builds the prompt the cloud brain reasons over. The whole point of the
 * assistant — versus the fixed Knowledge table the Doctor/Services screens use
 * — is that it isn't a lookup table: it gets the Drifter architecture as a
 * system prompt plus a LIVE snapshot of the node (health, port probes, real
 * journal logs) and reasons about whatever it finds, including faults nobody
 * anticipated. This object owns that prompt assembly; the network calls live in
 * the repository.
 */
object AssistantEngine {

    /** Static architecture knowledge — sourced from CLAUDE.md / AGENTS.md. */
    private val ARCHITECTURE = """
        You are the on-call troubleshooting assistant embedded in an Android app
        that monitors a single DRIFTER node over its Wi-Fi hotspot. Your job: help
        the operator diagnose connection problems, explain what's wrong in plain
        English, and give concrete next steps — including faults that aren't in any
        predefined checklist. Reason over the LIVE SNAPSHOT below; never invent
        readings it doesn't contain.

        ## What the node is
        DRIFTER is a Raspberry Pi 5 (8 GB) telemetry + assistant node bolted into a
        2004 Jaguar X-Type 2.5 V6. It runs headless. The phone reaches it over the
        `MZ1312_DRIFTER` Wi-Fi hotspot; the Pi is `10.42.0.1` on the `10.42.0.0/24`
        subnet. The app you live in talks to its dashboard over HTTP.

        ## Network surface (what the app can reach)
        - 8080  HTTP dashboard — `/healthz` (ungated) + `/api/*` (gated to
          127.0.0.1 + 10.42.0.0/24, so the phone must be on the hotspot).
        - 8443  HTTPS (self-signed) — browser geolocation only.
        - 8081  WebSocket telemetry fan-out (live MQTT as JSON frames).
        - 8082  WebSocket audio (TTS WAV to the phone speaker).
        - 35000 RealDash TCP bridge.
        - 1883  Mosquitto MQTT — **bound to loopback (127.0.0.1) since the
          2026-05-18 hardening**. From the phone it is *correctly* closed; a
          refused 1883 is NOT a fault. Hotspot clients use HTTP/WS, never raw MQTT.

        ## /healthz contract
        `status` ∈ {ok, ok-hw-pending, degraded}. `ok-hw-pending` (HTTP 200) means
        only hardware-dependent services are inactive because their dongle isn't
        plugged in — that is healthy, not broken. `degraded` (HTTP 503) means a
        non-hardware service is actually down. Also reports `mode`, `node_id`,
        `services{}`, `services_failed[]`, `services_hw_pending[]`,
        `mqtt_connected`, `telemetry_fresh`, `ws_clients`.

        ## Modes (persona — decides which services are "expected")
        - diag  — lean default floor: vehicle telemetry + driver-safety only.
        - drive — telemetry stack + LLM/voice assistant (heavier).
        - foot  — recon/offsec persona.
        - both  — everything (bench/lab only; won't fit 8 GB comfortably).
        A drive-only service being inactive in foot mode is correct, not a fault.

        ## Hardware-pending services (inactive == waiting for a dongle, not broken)
        canbridge (USB-OBD/CANFD adapter), rf (RTL-SDR), vivi (Ollama+Piper),
        voicein (USB mic), flipper (Flipper Zero), bleconv (onboard BLE), gps
        (USB GPS+gpsd), lcd (SPI LCD /dev/fb1), plus community tools
        (can-discovery, fly-catcher, kismet, kismet-bridge, wifi-audit).

        ## Actions the operator can take FROM THE APP
        - Restart any service (Services tab → Restart).
        - Switch mode (Overview/Services). Arsenal service control is refused
          (409) while in `drive` mode by design — switch to `foot` first.
        - Re-run the Connection Doctor (port probes from the phone).
        When you recommend one of these, say exactly which (e.g. "restart
        drifter-dashboard", "switch to foot mode").

        ## Tools — investigate, don't guess
        You can call these read-only tools to gather evidence yourself:
        - get_logs(service): last ~80 journal lines for a unit (short or full
          name, e.g. "canbridge"). Pull the logs of ANY service you suspect —
          don't limit yourself to what's in the snapshot.
        - get_healthz(): re-fetch current status / failed vs hw-pending / mqtt.
        - get_telemetry(): latest live vehicle signals.
        Prefer pulling a service's logs over speculating about it. Investigate
        first, then answer.

        ## How to answer
        Lead with the likely cause in one sentence, then the fix as numbered
        steps. Distinguish "broken" from "waiting for hardware" and from
        "expected for this mode". Be concrete and brief. Quote the telling log
        line. You cannot physically touch the car; for hands-on fixes (plug in a
        dongle, reseat a cable) give exact instructions for the operator to do.
    """.trimIndent()

    /** Read-only diagnostic tools the assistant can call to investigate. */
    fun tools(): JsonArray = buildJsonArray {
        add(
            buildJsonObject {
                put("name", "get_logs")
                put(
                    "description",
                    "Read the last ~80 journald log lines for one drifter service unit " +
                        "(e.g. 'drifter-canbridge' or just 'canbridge'). Use this to see " +
                        "WHY a service is failing.",
                )
                put(
                    "input_schema",
                    buildJsonObject {
                        put("type", "object")
                        put(
                            "properties",
                            buildJsonObject {
                                put(
                                    "service",
                                    buildJsonObject {
                                        put("type", "string")
                                        put("description", "unit name, short or full form")
                                    },
                                )
                            },
                        )
                        put("required", buildJsonArray { add("service") })
                    },
                )
            },
        )
        add(
            buildJsonObject {
                put("name", "get_healthz")
                put(
                    "description",
                    "Re-fetch the node's current /healthz: status, mode, which services " +
                        "are failed vs hardware-pending, and mqtt/telemetry freshness.",
                )
                put(
                    "input_schema",
                    buildJsonObject {
                        put("type", "object")
                        put("properties", buildJsonObject {})
                    },
                )
            },
        )
        add(
            buildJsonObject {
                put("name", "get_telemetry")
                put(
                    "description",
                    "Get the node's latest live telemetry snapshot (engine/vehicle signals) as JSON.",
                )
                put(
                    "input_schema",
                    buildJsonObject {
                        put("type", "object")
                        put("properties", buildJsonObject {})
                    },
                )
            },
        )
    }

    fun systemPrompt(snapshot: String): String =
        "$ARCHITECTURE\n\n## LIVE NODE SNAPSHOT (captured just now)\n$snapshot"

    /** Format the gathered live evidence into the snapshot block. */
    fun snapshot(
        host: String,
        health: Healthz?,
        doctor: List<ProbeResult>,
        logs: Map<String, List<String>>,
    ): String = buildString {
        appendLine("Node host: $host")
        if (health == null) {
            appendLine("/healthz: UNREACHABLE — the dashboard did not answer on 8080.")
        } else {
            appendLine("/healthz status: ${health.status}  (mode=${health.mode}, node=${health.nodeId})")
            appendLine("mqtt_connected=${health.mqttConnected}  telemetry_fresh=${health.telemetryFresh}  ws_clients=${health.wsClients}")
            appendLine("services active: ${health.activeCount}/${health.totalCount}")
            if (health.servicesFailed.isNotEmpty()) {
                appendLine("services_failed (genuinely down): ${health.servicesFailed.joinToString(", ")}")
            }
            if (health.servicesHwPending.isNotEmpty()) {
                appendLine("services_hw_pending (waiting on hardware): ${health.servicesHwPending.joinToString(", ")}")
            }
            val inactive = health.services.filterValues { !it }.keys
            if (inactive.isNotEmpty()) {
                appendLine("all inactive units: ${inactive.joinToString(", ")}")
            }
        }

        if (doctor.isNotEmpty()) {
            appendLine()
            appendLine("Connection Doctor (port probes from the phone):")
            doctor.forEach { p ->
                val lat = p.latencyMs?.let { " ${it}ms" } ?: ""
                appendLine("  [${p.status}] ${p.name} (${p.target})$lat — ${p.detail}")
            }
        }

        if (logs.isNotEmpty()) {
            appendLine()
            appendLine("Recent journal logs for failing services:")
            logs.forEach { (unit, lines) ->
                appendLine("--- $unit ---")
                lines.forEach { appendLine("  $it") }
            }
        }
    }.trim()
}
