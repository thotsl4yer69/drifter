package com.mz1312.drifter.data.net

import com.mz1312.drifter.data.model.ApiResult
import com.mz1312.drifter.data.model.FailureKind
import com.mz1312.drifter.data.model.Healthz
import com.mz1312.drifter.data.model.LogsResponse
import com.mz1312.drifter.data.model.ModeInfo
import com.mz1312.drifter.data.model.ModeSwitchResult
import com.mz1312.drifter.data.model.PiQueryResponse
import com.mz1312.drifter.data.model.ServiceActionResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

/**
 * Thin, coroutine-friendly HTTP client for one Drifter node.
 *
 * Everything the app needs lives on the plain-HTTP dashboard (port 8080):
 *   - GET  /healthz                     (not peer-gated — works off-subnet too)
 *   - GET  /api/mode, /api/state, /api/hardware, /api/arsenal, /api/<tool>/...
 *   - POST /api/mode/<name>             (switch persona)
 *   - POST /api/service/<unit>          (start|stop|restart arsenal unit; foot-mode)
 *
 * All /api/* (everything except /healthz) is gated server-side to
 * 127.0.0.1 + 10.42.0.0/24, so the phone must be on the MZ1312_DRIFTER
 * hotspot; a 403 here means "you're not on the hotspot subnet".
 */
class DrifterApi(
    private val baseUrl: String,
    timeoutMs: Long = DEFAULT_TIMEOUT_MS,
) {
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(timeoutMs, TimeUnit.MILLISECONDS)
        .readTimeout(timeoutMs, TimeUnit.MILLISECONDS)
        .callTimeout(timeoutMs + 1500, TimeUnit.MILLISECONDS)
        .retryOnConnectionFailure(false)
        .build()

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
    }

    // ── Typed endpoints ────────────────────────────────────────────────
    suspend fun healthz(): ApiResult<Healthz> =
        get("/healthz") { json.decodeFromString(Healthz.serializer(), it) }

    suspend fun mode(): ApiResult<ModeInfo> =
        get("/api/mode") { json.decodeFromString(ModeInfo.serializer(), it) }

    suspend fun setMode(name: String): ApiResult<ModeSwitchResult> =
        post("/api/mode/$name", "{}") { json.decodeFromString(ModeSwitchResult.serializer(), it) }

    suspend fun serviceAction(unit: String, action: String): ApiResult<ServiceActionResult> =
        post("/api/service/$unit", """{"action":"$action"}""") {
            json.decodeFromString(ServiceActionResult.serializer(), it)
        }

    /** Read-only journalctl tail for one unit — the assistant's evidence source. */
    suspend fun logs(unit: String, n: Int = 120): ApiResult<LogsResponse> =
        get("/api/logs/$unit?n=$n") { json.decodeFromString(LogsResponse.serializer(), it) }

    /** Query the Pi's own on-board LLM (cloud-brain fallback path). */
    suspend fun piQuery(query: String): ApiResult<PiQueryResponse> {
        val body = JsonObject(mapOf("query" to JsonPrimitive(query))).toString()
        return post("/api/query", body) {
            json.decodeFromString(PiQueryResponse.serializer(), it)
        }
    }

    // ── Loosely-typed endpoints (rendered generically by the UI) ───────
    suspend fun getObject(path: String): ApiResult<JsonObject> =
        get(path) { json.parseToJsonElement(it) as JsonObject }

    suspend fun getElement(path: String): ApiResult<JsonElement> =
        get(path) { json.parseToJsonElement(it) }

    suspend fun postElement(path: String, body: String): ApiResult<JsonElement> =
        post(path, body) { json.parseToJsonElement(it) }

    // ── Core verbs ─────────────────────────────────────────────────────
    private suspend fun <T> get(path: String, parse: (String) -> T): ApiResult<T> =
        execute(Request.Builder().url(baseUrl + path).get().build(), parse)

    private suspend fun <T> post(path: String, body: String, parse: (String) -> T): ApiResult<T> =
        execute(
            Request.Builder().url(baseUrl + path)
                .post(body.toRequestBody(JSON_MEDIA)).build(),
            parse,
        )

    private suspend fun <T> execute(request: Request, parse: (String) -> T): ApiResult<T> =
        withContext(Dispatchers.IO) {
            try {
                client.newCall(request).execute().use { resp ->
                    val text = resp.body?.string().orEmpty()
                    if (!resp.isSuccessful) {
                        return@use ApiResult.Err(
                            kind = when (resp.code) {
                                403 -> FailureKind.FORBIDDEN
                                503 -> FailureKind.DEGRADED
                                else -> FailureKind.HTTP_ERROR
                            },
                            message = "HTTP ${resp.code} ${resp.message}".trim(),
                            httpCode = resp.code,
                        )
                    }
                    try {
                        ApiResult.Ok(parse(text))
                    } catch (e: Exception) {
                        ApiResult.Err(FailureKind.BAD_RESPONSE, "Unexpected response: ${e.message}")
                    }
                }
            } catch (e: SocketTimeoutException) {
                ApiResult.Err(FailureKind.TIMEOUT, "Timed out talking to the node")
            } catch (e: ConnectException) {
                ApiResult.Err(FailureKind.UNREACHABLE, "Connection refused — node unreachable")
            } catch (e: IOException) {
                ApiResult.Err(FailureKind.UNREACHABLE, e.message ?: "Network error")
            }
        }

    companion object {
        const val DEFAULT_TIMEOUT_MS = 4000L
        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    }
}
