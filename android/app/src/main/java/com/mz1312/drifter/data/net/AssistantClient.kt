package com.mz1312.drifter.data.net

import com.mz1312.drifter.data.model.AssistantReply
import com.mz1312.drifter.data.model.ChatMessage
import com.mz1312.drifter.data.model.ChatRole
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Minimal Anthropic Messages API client (`POST /v1/messages`).
 *
 * Why raw HTTP and not the official SDK: the claude-api guidance maps Kotlin to
 * the Anthropic *Java* SDK, but that SDK targets the server JVM (java.net.http,
 * a large method count) and is a poor fit inside an Android app. The app already
 * depends on OkHttp, so we call the documented REST contract directly — the
 * supported path when the SDK doesn't fit the platform.
 *
 * Contract details that matter (and are easy to get wrong from memory):
 *   - headers: `x-api-key`, `anthropic-version: 2023-06-01`, `content-type`.
 *   - `thinking: {type: "adaptive"}` — the only thinking mode on Opus 4.8.
 *   - NO `temperature` / `top_p` / `budget_tokens` — all 400 on Opus 4.8.
 *   - a safety decline is HTTP 200 with `stop_reason: "refusal"`; check it
 *     before reading `content`, or you index into an empty array.
 */
class AssistantClient(
    private val apiKey: String,
    private val model: String,
) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .callTimeout(125, TimeUnit.SECONDS)
        .build()

    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    suspend fun ask(system: String, history: List<ChatMessage>): AssistantReply =
        withContext(Dispatchers.IO) {
            val messages = buildJsonArray {
                history.filter { it.text.isNotBlank() }.forEach { m ->
                    add(
                        buildJsonObject {
                            put("role", if (m.role == ChatRole.USER) "user" else "assistant")
                            put("content", m.text)
                        },
                    )
                }
            }
            val payload = buildJsonObject {
                put("model", model)
                put("max_tokens", MAX_TOKENS)
                put("system", system)
                put("thinking", buildJsonObject { put("type", "adaptive") })
                put("messages", messages)
            }.toString()

            val request = Request.Builder()
                .url(ENDPOINT)
                .addHeader("x-api-key", apiKey)
                .addHeader("anthropic-version", ANTHROPIC_VERSION)
                .addHeader("content-type", "application/json")
                .post(payload.toRequestBody(JSON_MEDIA))
                .build()

            try {
                client.newCall(request).execute().use { resp ->
                    val raw = resp.body?.string().orEmpty()
                    if (!resp.isSuccessful) httpError(resp.code, raw) else parse(raw)
                }
            } catch (e: IOException) {
                AssistantReply.Failed("Couldn't reach Claude: ${e.message ?: "network error"}")
            }
        }

    private fun parse(raw: String): AssistantReply {
        val root = runCatching { json.parseToJsonElement(raw).jsonObject }.getOrNull()
            ?: return AssistantReply.Failed("Claude returned a response that couldn't be parsed.")

        if (root["stop_reason"]?.jsonPrimitive?.contentOrNull == "refusal") {
            val why = root["stop_details"]?.jsonObject
                ?.get("explanation")?.jsonPrimitive?.contentOrNull
            return AssistantReply.Refused(why ?: "Claude declined this request for safety reasons.")
        }

        val blocks = root["content"] as? JsonArray ?: JsonArray(emptyList())
        val text = blocks.mapNotNull { block ->
            val o = block.jsonObject
            if (o["type"]?.jsonPrimitive?.contentOrNull == "text") {
                o["text"]?.jsonPrimitive?.contentOrNull
            } else {
                null
            }
        }.joinToString("\n").trim()

        return if (text.isBlank()) {
            AssistantReply.Failed("Claude returned an empty answer.")
        } else {
            AssistantReply.Ok(text, via = "Claude · $model")
        }
    }

    private fun httpError(code: Int, raw: String): AssistantReply {
        val apiMessage = runCatching {
            json.parseToJsonElement(raw).jsonObject["error"]
                ?.jsonObject?.get("message")?.jsonPrimitive?.contentOrNull
        }.getOrNull()
        val hint = when (code) {
            401 -> "Invalid Claude API key — check it in Settings."
            403 -> "This API key can't access model '$model'."
            404 -> "Unknown model '$model' — fix the model id in Settings."
            429 -> "Claude rate limit hit — wait a moment and retry."
            in 500..599 -> "Claude service error ($code) — retry shortly."
            else -> "Claude API error ($code)."
        }
        return AssistantReply.Failed(apiMessage?.let { "$hint  ($it)" } ?: hint)
    }

    private companion object {
        const val ENDPOINT = "https://api.anthropic.com/v1/messages"
        const val ANTHROPIC_VERSION = "2023-06-01"
        const val MAX_TOKENS = 4096
        val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    }
}
