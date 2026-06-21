package com.mz1312.drifter.data.net

import com.mz1312.drifter.data.model.AssistantReply
import com.mz1312.drifter.data.model.ChatMessage
import com.mz1312.drifter.data.model.ChatRole
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
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
 * Anthropic Messages API client with an agentic tool-use loop. Claude can call
 * the read-only diagnostic tools we declare (pull a specific service's logs,
 * re-check /healthz, read live telemetry) to investigate on its own, instead of
 * being limited to whatever snapshot we pre-bundled — the full realisation of
 * "assist with anything."
 *
 * Raw HTTP over OkHttp (the official Java SDK targets the server JVM and is a
 * poor fit on Android). Contract details that matter: `x-api-key` +
 * `anthropic-version`, adaptive thinking, no sampling params. When `stop_reason`
 * is `tool_use` we echo the assistant's content back verbatim (so the required
 * thinking blocks survive), run each tool, and return the results.
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

    /** Carries an early-exit reply (HTTP error, refusal, parse failure) out of
     *  the loop without unwinding through every call site. */
    private class StopWithReply(val reply: AssistantReply) : Exception()

    suspend fun ask(
        system: String,
        history: List<ChatMessage>,
        tools: JsonArray,
        executeTool: suspend (name: String, input: JsonObject) -> String,
    ): AssistantReply = withContext(Dispatchers.IO) {
        val messages = mutableListOf<JsonElement>()
        history.filter { it.text.isNotBlank() }.forEach { m ->
            messages += buildJsonObject {
                put("role", if (m.role == ChatRole.USER) "user" else "assistant")
                put("content", m.text)
            }
        }

        try {
            repeat(MAX_ITERATIONS) {
                val root = postRaw(buildPayload(system, tools, messages))

                if (root["stop_reason"]?.jsonPrimitive?.contentOrNull == "refusal") {
                    val why = root["stop_details"]?.jsonObject
                        ?.get("explanation")?.jsonPrimitive?.contentOrNull
                    return@withContext AssistantReply.Refused(
                        why ?: "Claude declined this request for safety reasons.",
                    )
                }

                val content = root["content"] as? JsonArray ?: JsonArray(emptyList())

                if (root["stop_reason"]?.jsonPrimitive?.contentOrNull == "tool_use") {
                    // Echo the assistant turn verbatim (keeps thinking blocks intact).
                    messages += buildJsonObject {
                        put("role", "assistant")
                        put("content", content)
                    }
                    // Run each tool here in the coroutine body (executeTool is
                    // suspend — it can't be called inside a buildJsonArray lambda).
                    val resultBlocks = mutableListOf<JsonElement>()
                    for (block in content) {
                        val o = block.jsonObject
                        if (o["type"]?.jsonPrimitive?.contentOrNull != "tool_use") continue
                        val id = o["id"]?.jsonPrimitive?.contentOrNull ?: continue
                        val name = o["name"]?.jsonPrimitive?.contentOrNull ?: continue
                        val input = o["input"] as? JsonObject ?: JsonObject(emptyMap())
                        val out = runCatching { executeTool(name, input) }
                            .getOrElse { "tool error: ${it.message}" }
                        resultBlocks += buildJsonObject {
                            put("type", "tool_result")
                            put("tool_use_id", id)
                            put("content", out)
                        }
                    }
                    messages += buildJsonObject {
                        put("role", "user")
                        put("content", JsonArray(resultBlocks))
                    }
                    // …and loop for the next turn.
                } else {
                    val text = content.mapNotNull { b ->
                        val o = b.jsonObject
                        if (o["type"]?.jsonPrimitive?.contentOrNull == "text") {
                            o["text"]?.jsonPrimitive?.contentOrNull
                        } else {
                            null
                        }
                    }.joinToString("\n").trim()
                    return@withContext if (text.isBlank()) {
                        AssistantReply.Failed("Claude returned an empty answer.")
                    } else {
                        AssistantReply.Ok(text, via = "Claude · $model")
                    }
                }
            }
            AssistantReply.Failed(
                "The assistant kept investigating past its step limit — try a narrower question.",
            )
        } catch (e: StopWithReply) {
            e.reply
        }
    }

    private fun buildPayload(system: String, tools: JsonArray, messages: List<JsonElement>): String =
        buildJsonObject {
            put("model", model)
            put("max_tokens", MAX_TOKENS)
            put("system", system)
            put("thinking", buildJsonObject { put("type", "adaptive") })
            if (tools.isNotEmpty()) put("tools", tools)
            put("messages", JsonArray(messages))
        }.toString()

    /** POST one turn; returns the parsed root or throws [StopWithReply]. */
    private fun postRaw(payload: String): JsonObject {
        val request = Request.Builder()
            .url(ENDPOINT)
            .addHeader("x-api-key", apiKey)
            .addHeader("anthropic-version", ANTHROPIC_VERSION)
            .addHeader("content-type", "application/json")
            .post(payload.toRequestBody(JSON_MEDIA))
            .build()
        val response = try {
            client.newCall(request).execute()
        } catch (e: IOException) {
            throw StopWithReply(
                AssistantReply.Failed("Couldn't reach Claude: ${e.message ?: "network error"}"),
            )
        }
        response.use { resp ->
            val raw = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) throw StopWithReply(httpError(resp.code, raw))
            return runCatching { json.parseToJsonElement(raw).jsonObject }.getOrElse {
                throw StopWithReply(
                    AssistantReply.Failed("Claude returned a response that couldn't be parsed."),
                )
            }
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
        const val MAX_ITERATIONS = 6
        val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    }
}
