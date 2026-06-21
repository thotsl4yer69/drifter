package com.mz1312.drifter.data

import com.mz1312.drifter.data.model.ApiResult
import com.mz1312.drifter.data.model.AssistantReply
import com.mz1312.drifter.data.model.ChatMessage
import com.mz1312.drifter.data.model.ChatRole
import com.mz1312.drifter.data.model.Healthz
import com.mz1312.drifter.data.model.LogsResponse
import com.mz1312.drifter.data.model.ModeInfo
import com.mz1312.drifter.data.model.ModeSwitchResult
import com.mz1312.drifter.data.model.ServiceActionResult
import com.mz1312.drifter.data.net.AssistantClient
import com.mz1312.drifter.data.net.ConnectionDoctor
import com.mz1312.drifter.data.net.DrifterApi
import com.mz1312.drifter.data.net.ProbeResult
import com.mz1312.drifter.data.net.SocketSignal
import com.mz1312.drifter.data.net.TelemetrySocket
import com.mz1312.drifter.data.store.AppSettings
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * Single seam between the UI and one Drifter node. Rebuilds its HTTP client
 * whenever the configured host/port changes; everything else is a thin pass-
 * through that keeps the ViewModel free of OkHttp/JSON details.
 */
class DrifterRepository {

    @Volatile
    private var settings: AppSettings = AppSettings()

    @Volatile
    private var cachedApi: DrifterApi? = null

    @Volatile
    private var cachedBase: String = ""

    fun updateSettings(next: AppSettings) {
        settings = next
    }

    private fun api(): DrifterApi {
        val base = settings.httpBaseUrl
        val existing = cachedApi
        if (existing != null && base == cachedBase) return existing
        return DrifterApi(base).also {
            cachedApi = it
            cachedBase = base
        }
    }

    // ── Health & mode ─────────────────────────────────────────────────
    suspend fun health(): ApiResult<Healthz> = api().healthz()
    suspend fun mode(): ApiResult<ModeInfo> = api().mode()
    suspend fun setMode(name: String): ApiResult<ModeSwitchResult> = api().setMode(name)

    // ── Service control ───────────────────────────────────────────────
    suspend fun serviceAction(unit: String, action: String): ApiResult<ServiceActionResult> =
        api().serviceAction(unit, action)

    /** Read-only journal tail for one unit (GET /api/logs/<unit>). */
    suspend fun logs(unit: String, lines: Int = 120): ApiResult<LogsResponse> =
        api().logs(unit, lines)

    // ── Arsenal / Carsenal read surface ───────────────────────────────
    suspend fun arsenal(): ApiResult<JsonObject> = api().getObject("/api/arsenal")
    suspend fun toolElement(path: String): ApiResult<JsonElement> = api().getElement(path)
    suspend fun postTool(path: String, body: String): ApiResult<JsonElement> =
        api().postElement(path, body)

    // ── Connection doctor ─────────────────────────────────────────────
    suspend fun runDoctor(): Pair<String, List<ProbeResult>> {
        val host = settings.host
        return host to ConnectionDoctor(api(), host).run()
    }

    // ── Live telemetry ────────────────────────────────────────────────
    fun telemetryStream(): Flow<SocketSignal> =
        TelemetrySocket(settings.telemetryWsUrl).stream()

    // ── AI assistant ──────────────────────────────────────────────────
    /**
     * Answer a troubleshooting turn. Brain selection per the user's setting:
     * a Claude API key means the cloud brain runs first (and keeps working even
     * when the Pi is unreachable); on any cloud failure — or when no key is set
     * — we fall back to the Pi's own on-board LLM. Either way the model reasons
     * over a freshly-gathered live snapshot (health + port probes + real logs).
     */
    suspend fun askAssistant(history: List<ChatMessage>): AssistantReply {
        val s = settings
        if (s.hasCloudBrain) {
            val system = AssistantEngine.systemPrompt(buildSnapshot())
            val reply = AssistantClient(s.claudeApiKey, s.claudeModel)
                .ask(system, history, AssistantEngine.tools()) { name, input ->
                    executeTool(name, input)
                }
            if (reply !is AssistantReply.Failed) return reply
            // Cloud brain failed — try the Pi's on-board LLM before giving up.
            piFallback(history)?.let { return it }
            return reply
        }
        return piFallback(history) ?: AssistantReply.Failed(
            "No Claude API key set (Settings → AI assistant), and the Pi's on-board " +
                "LLM didn't answer. Add a key to enable the cloud brain — it keeps " +
                "working even when the Pi itself is unreachable.",
        )
    }

    /** Execute one read-only assistant tool call against the node. */
    private suspend fun executeTool(name: String, input: JsonObject): String {
        val a = api()
        return when (name) {
            "get_logs" -> {
                val svc = input["service"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
                if (svc.isEmpty()) {
                    "error: missing 'service'"
                } else when (val r = a.logs(svc, 80)) {
                    is ApiResult.Ok ->
                        if (!r.value.ok) "error: ${r.value.error ?: "logs unavailable"}"
                        else r.value.lines.joinToString("\n").ifBlank { "(no recent log lines for $svc)" }
                    is ApiResult.Err -> "error: ${r.message}"
                }
            }
            "get_healthz" -> when (val r = a.healthz()) {
                is ApiResult.Ok -> {
                    val h = r.value
                    "status=${h.status} mode=${h.mode} node=${h.nodeId} " +
                        "failed=${h.servicesFailed} hw_pending=${h.servicesHwPending} " +
                        "mqtt_connected=${h.mqttConnected} telemetry_fresh=${h.telemetryFresh} " +
                        "active=${h.activeCount}/${h.totalCount}"
                }
                is ApiResult.Err -> "healthz error (the node may be degraded or unreachable): ${r.message}"
            }
            "get_telemetry" -> when (val r = a.getObject("/api/state")) {
                is ApiResult.Ok -> r.value.toString().take(4000)
                is ApiResult.Err -> "telemetry unavailable: ${r.message}"
            }
            else -> "error: unknown tool '$name'"
        }
    }

    /** Gather the live evidence the model reasons over. */
    private suspend fun buildSnapshot(): String {
        val a = api()
        val health = (a.healthz() as? ApiResult.Ok)?.value
        val doctor = ConnectionDoctor(a, settings.host).run()
        // Pull real journal logs for the genuinely-failed services (cap the
        // count + length so the snapshot stays bounded and the turn stays snappy).
        val logs = LinkedHashMap<String, List<String>>()
        health?.servicesFailed?.take(3)?.forEach { unit ->
            (a.logs(unit, 60) as? ApiResult.Ok)?.value
                ?.takeIf { it.ok }
                ?.let { logs[unit] = it.lines.takeLast(40) }
        }
        return AssistantEngine.snapshot(settings.host, health, doctor, logs)
    }

    /** Route the latest user question to the Pi's on-board LLM (POST /api/query). */
    private suspend fun piFallback(history: List<ChatMessage>): AssistantReply? {
        val question = history.lastOrNull { it.role == ChatRole.USER }?.text ?: return null
        return when (val r = api().piQuery(question)) {
            is ApiResult.Ok ->
                r.value.response.takeIf { it.isNotBlank() }?.let {
                    AssistantReply.Ok(it, via = "Pi on-board LLM (${r.value.model})")
                }
            is ApiResult.Err -> null
        }
    }
}
