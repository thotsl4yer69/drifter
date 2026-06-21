package com.mz1312.drifter.data.net

import com.mz1312.drifter.data.model.TelemetryEvent
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/** Connection state surfaced to the UI so the Telemetry tab is itself diagnostic. */
sealed interface SocketState {
    data object Connecting : SocketState
    data object Open : SocketState
    data class Closed(val reason: String) : SocketState
    data class Failed(val reason: String) : SocketState
}

sealed interface SocketSignal {
    data class State(val state: SocketState) : SocketSignal
    data class Frame(val event: TelemetryEvent) : SocketSignal
}

/**
 * Streams the Pi's telemetry fan-out (ws://host:8081). Each frame is
 * {"topic","data","ts"} (see src/web_dashboard.py ws_handler). Exposed as a
 * cold Flow so a screen subscribing/unsubscribing cleanly opens/closes the
 * socket — confirming live data flow is itself a connection diagnostic.
 */
class TelemetrySocket(private val wsUrl: String) {

    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    private val client = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .connectTimeout(4, TimeUnit.SECONDS)
        .build()

    fun stream(): Flow<SocketSignal> = callbackFlow {
        trySend(SocketSignal.State(SocketState.Connecting))
        val request = Request.Builder().url(wsUrl).build()

        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                trySend(SocketSignal.State(SocketState.Open))
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching {
                    val obj = json.parseToJsonElement(text) as JsonObject
                    val topic = obj["topic"]?.jsonPrimitive?.content ?: return
                    val data = obj["data"] ?: return
                    val ts = obj["ts"]?.jsonPrimitive?.doubleOrNull ?: 0.0
                    trySend(SocketSignal.Frame(TelemetryEvent(topic, data, ts)))
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                trySend(SocketSignal.State(SocketState.Closed(reason.ifBlank { "closed ($code)" })))
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                trySend(SocketSignal.State(SocketState.Failed(t.message ?: "socket failure")))
            }
        }

        val socket = client.newWebSocket(request, listener)
        awaitClose { socket.cancel() }
    }
}
