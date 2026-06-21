package com.mz1312.drifter.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Typed mirrors of the Drifter Pi's HTTP contract. Field names match the
 * Python source exactly (snake_case) — see src/web_dashboard_health.py and
 * src/web_dashboard_handlers.py. Every field carries a default so a partial
 * or older-firmware payload still decodes.
 */

/** GET /healthz — the single most useful diagnostic probe. Not peer-gated. */
@Serializable
data class Healthz(
    val status: String = "unknown",
    val mode: String = "unknown",
    val ts: Double = 0.0,
    @SerialName("node_id") val nodeId: String = "",
    val services: Map<String, Boolean> = emptyMap(),
    @SerialName("services_failed") val servicesFailed: List<String> = emptyList(),
    @SerialName("services_hw_pending") val servicesHwPending: List<String> = emptyList(),
    @SerialName("mqtt_connected") val mqttConnected: Boolean = false,
    @SerialName("telemetry_fresh") val telemetryFresh: Boolean = false,
    @SerialName("ws_clients") val wsClients: Int = 0,
) {
    val activeCount: Int get() = services.values.count { it }
    val totalCount: Int get() = services.size

    enum class Health { OK, HW_PENDING, DEGRADED, UNKNOWN }

    val health: Health
        get() = when (status) {
            "ok" -> Health.OK
            "ok-hw-pending" -> Health.HW_PENDING
            "degraded" -> Health.DEGRADED
            else -> Health.UNKNOWN
        }
}

/** GET /api/mode — current persona + the switchable set. */
@Serializable
data class ModeInfo(
    val mode: String = "unknown",
    val choices: List<String> = emptyList(),
)

/** POST /api/mode/<name> response. */
@Serializable
data class ModeSwitchResult(
    val requested: String = "",
    val status: String = "",
    val rc: Int = -1,
    val stderr: String = "",
) {
    val dispatched: Boolean get() = status == "dispatched"
}

/** POST /api/service/<unit> response. */
@Serializable
data class ServiceActionResult(
    val ok: Boolean = false,
    val unit: String = "",
    val action: String = "",
    val rc: Int? = null,
    val error: String? = null,
)

/** GET /api/logs/<unit> — read-only journalctl tail (newest last). */
@Serializable
data class LogsResponse(
    val unit: String = "",
    val n: Int = 0,
    val ok: Boolean = false,
    val lines: List<String> = emptyList(),
    val error: String? = null,
)

/** POST /api/query — the Pi's own on-board LLM (the cloud-brain fallback). */
@Serializable
data class PiQueryResponse(
    val response: String = "",
    val model: String = "",
    val error: String? = null,
)

/** A single live telemetry frame off the ws://host:8081 fan-out. */
data class TelemetryEvent(
    val topic: String,
    val data: JsonElement,
    val ts: Double,
) {
    /** "drifter/engine/rpm" -> "engine/rpm" for compact display. */
    val shortTopic: String get() = topic.removePrefix("drifter/")
}
