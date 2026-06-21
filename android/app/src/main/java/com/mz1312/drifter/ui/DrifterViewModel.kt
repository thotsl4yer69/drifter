package com.mz1312.drifter.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.mz1312.drifter.AppContainer
import com.mz1312.drifter.DrifterApp
import com.mz1312.drifter.data.DrifterRepository
import com.mz1312.drifter.data.model.ApiResult
import com.mz1312.drifter.data.model.AssistantReply
import com.mz1312.drifter.data.model.ChatMessage
import com.mz1312.drifter.data.model.ChatRole
import com.mz1312.drifter.data.model.Healthz
import com.mz1312.drifter.data.model.ModeInfo
import com.mz1312.drifter.data.model.TelemetryEvent
import com.mz1312.drifter.data.net.DoctorReport
import com.mz1312.drifter.data.net.SocketSignal
import com.mz1312.drifter.data.net.SocketState
import com.mz1312.drifter.data.store.AppSettings
import com.mz1312.drifter.data.store.SettingsStore
import com.mz1312.drifter.ui.common.Loadable
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Live telemetry view: socket state + the latest value per topic + a feed. */
data class TelemetryUiState(
    val socket: SocketState = SocketState.Closed("idle"),
    val signals: Map<String, SignalValue> = emptyMap(),
    val frameCount: Long = 0,
) {
    val connected: Boolean get() = socket is SocketState.Open
}

data class SignalValue(val display: String, val ts: Double)

/**
 * One ViewModel for the whole app. A diagnostics tool is read-mostly and
 * everything shares the same node + poll loop, so centralising state keeps tab
 * switches instant and the polling logic in one place.
 */
class DrifterViewModel(
    app: Application,
    private val repo: DrifterRepository,
    private val settingsStore: SettingsStore,
) : AndroidViewModel(app) {

    // ── Settings ───────────────────────────────────────────────────────
    val settings: StateFlow<AppSettings> = settingsStore.settings
        .stateIn(viewModelScope, SharingStarted.Eagerly, AppSettings())

    // ── Health & mode ──────────────────────────────────────────────────
    private val _health = MutableStateFlow<Loadable<Healthz>>(Loadable.Idle)
    val health: StateFlow<Loadable<Healthz>> = _health.asStateFlow()

    private val _mode = MutableStateFlow<ModeInfo?>(null)
    val mode: StateFlow<ModeInfo?> = _mode.asStateFlow()

    private val _lastRefresh = MutableStateFlow(0L)
    val lastRefresh: StateFlow<Long> = _lastRefresh.asStateFlow()

    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing.asStateFlow()

    // ── Doctor ─────────────────────────────────────────────────────────
    private val _doctor = MutableStateFlow(DoctorReport(host = ""))
    val doctor: StateFlow<DoctorReport> = _doctor.asStateFlow()

    // ── Arsenal / Carsenal ─────────────────────────────────────────────
    private val _arsenal = MutableStateFlow<Loadable<List<ToolStatus>>>(Loadable.Idle)
    val arsenal: StateFlow<Loadable<List<ToolStatus>>> = _arsenal.asStateFlow()

    // ── Telemetry ──────────────────────────────────────────────────────
    private val _telemetry = MutableStateFlow(TelemetryUiState())
    val telemetry: StateFlow<TelemetryUiState> = _telemetry.asStateFlow()
    private var telemetryJob: Job? = null

    // ── AI assistant ───────────────────────────────────────────────────
    private val _chat = MutableStateFlow<List<ChatMessage>>(emptyList())
    val chat: StateFlow<List<ChatMessage>> = _chat.asStateFlow()

    private val _assistantBusy = MutableStateFlow(false)
    val assistantBusy: StateFlow<Boolean> = _assistantBusy.asStateFlow()

    // ── One-shot messages (snackbars) ──────────────────────────────────
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages = _messages.asSharedFlow()

    private var pollJob: Job? = null

    init {
        // Keep the repository's host in sync and (re)start polling on change.
        viewModelScope.launch {
            settings.collect { s ->
                repo.updateSettings(s)
                restartPolling(s)
            }
        }
    }

    // ── Polling ─────────────────────────────────────────────────────────
    private fun restartPolling(s: AppSettings) {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            while (isActive) {
                refreshNow()
                if (!s.autoRefresh) break
                delay(s.pollSeconds * 1000L)
            }
        }
    }

    fun refreshNow() {
        viewModelScope.launch {
            _refreshing.value = true
            when (val r = repo.health()) {
                is ApiResult.Ok -> _health.value = Loadable.Success(r.value)
                is ApiResult.Err -> _health.value = Loadable.Error(r.kind, r.message)
            }
            (repo.mode() as? ApiResult.Ok)?.let { _mode.value = it.value }
            _lastRefresh.value = System.currentTimeMillis()
            _refreshing.value = false
        }
    }

    // ── Doctor ──────────────────────────────────────────────────────────
    fun runDoctor() {
        viewModelScope.launch {
            _doctor.value = _doctor.value.copy(running = true)
            val (host, results) = repo.runDoctor()
            _doctor.value = DoctorReport(
                host = host,
                results = results,
                ranAt = System.currentTimeMillis(),
                running = false,
            )
        }
    }

    // ── Actions ─────────────────────────────────────────────────────────
    fun setMode(name: String) {
        viewModelScope.launch {
            when (val r = repo.setMode(name)) {
                is ApiResult.Ok ->
                    _messages.tryEmit(
                        if (r.value.dispatched) "Mode switch to '$name' dispatched"
                        else "Mode switch failed (rc ${r.value.rc}) ${r.value.stderr}",
                    )
                is ApiResult.Err -> _messages.tryEmit("Mode switch error: ${r.message}")
            }
            // Give systemd-run a beat, then refresh.
            delay(1200)
            refreshNow()
        }
    }

    fun serviceAction(unit: String, action: String) {
        viewModelScope.launch {
            when (val r = repo.serviceAction(unit, action)) {
                is ApiResult.Ok -> {
                    val res = r.value
                    _messages.tryEmit(
                        if (res.ok) "$action $unit ✓"
                        else "$action $unit failed (rc ${res.rc}) ${res.error.orEmpty()}",
                    )
                }
                is ApiResult.Err -> _messages.tryEmit(
                    when (r.httpCode) {
                        409 -> "Refused: arsenal control is disabled in drive mode. Switch to foot mode first."
                        403 -> "Refused: '$unit' is not an arsenal-controllable unit (or you're off-subnet)."
                        else -> "$action $unit error: ${r.message}"
                    },
                )
            }
            delay(800)
            refreshNow()
            refreshArsenal()
        }
    }

    // ── Arsenal status fetch ────────────────────────────────────────────
    fun refreshArsenal() {
        viewModelScope.launch {
            _arsenal.value = Loadable.Loading
            val tools = ARSENAL_ENDPOINTS.map { (label, path) ->
                when (val r = repo.toolElement(path)) {
                    is ApiResult.Ok -> ToolStatus(label, path, prettyJson(r.value), reachable = true)
                    is ApiResult.Err -> ToolStatus(label, path, r.message, reachable = false)
                }
            }
            _arsenal.value = Loadable.Success(tools)
        }
    }

    // ── Telemetry lifecycle (driven by the Telemetry screen) ────────────
    fun connectTelemetry() {
        if (telemetryJob?.isActive == true) return
        _telemetry.value = TelemetryUiState(socket = SocketState.Connecting)
        telemetryJob = viewModelScope.launch {
            repo.telemetryStream().collect { signal ->
                when (signal) {
                    is SocketSignal.State -> _telemetry.value =
                        _telemetry.value.copy(socket = signal.state)
                    is SocketSignal.Frame -> applyFrame(signal.event)
                }
            }
        }
    }

    fun disconnectTelemetry() {
        telemetryJob?.cancel()
        telemetryJob = null
        _telemetry.value = _telemetry.value.copy(socket = SocketState.Closed("disconnected"))
    }

    private fun applyFrame(event: TelemetryEvent) {
        val prev = _telemetry.value
        val next = LinkedHashMap(prev.signals)
        next[event.shortTopic] = SignalValue(displayValue(event.data), event.ts)
        _telemetry.value = prev.copy(
            signals = next,
            frameCount = prev.frameCount + 1,
        )
    }

    // ── Assistant turn ──────────────────────────────────────────────────
    fun askAssistant(text: String) {
        val q = text.trim()
        if (q.isEmpty() || _assistantBusy.value) return
        val withUser = _chat.value + ChatMessage(ChatRole.USER, q)
        _chat.value = withUser
        _assistantBusy.value = true
        viewModelScope.launch {
            val reply = repo.askAssistant(withUser)
            val msg = when (reply) {
                is AssistantReply.Ok -> ChatMessage(ChatRole.ASSISTANT, reply.text, via = reply.via)
                is AssistantReply.Refused ->
                    ChatMessage(ChatRole.ASSISTANT, reply.explanation, via = "refused")
                is AssistantReply.Failed ->
                    ChatMessage(ChatRole.ASSISTANT, reply.message, via = "error")
            }
            _chat.value = _chat.value + msg
            _assistantBusy.value = false
        }
    }

    fun clearChat() {
        _chat.value = emptyList()
    }

    fun updateSettings(next: AppSettings) {
        viewModelScope.launch { settingsStore.update(next) }
    }

    data class ToolStatus(
        val label: String,
        val path: String,
        val body: String,
        val reachable: Boolean,
    )

    companion object {
        /** Read-only status surfaces for the Carsenal / Kali-arsenal tab. */
        val ARSENAL_ENDPOINTS: List<Pair<String, String>> = listOf(
            "Arsenal aggregate" to "/api/arsenal",
            "CAN discovery (carsenal)" to "/api/can/discovery",
            "CAN captures (carsenal)" to "/api/can/captures",
            "Flipper Zero" to "/api/flipper/status",
            "Marauder (ESP32)" to "/api/marauder/status",
            "Kismet devices" to "/api/kismet/devices",
            "HID / BadUSB" to "/api/hid/status",
            "Ghost protocol" to "/api/ghost/status",
        )

        private fun displayValue(el: JsonElement): String = when (el) {
            is JsonPrimitive -> el.content
            else -> el.toString()
        }

        private fun prettyJson(el: JsonElement): String {
            // Compact-but-readable: one key per line for objects, else toString.
            return when (el) {
                is JsonObject -> el.entries.joinToString("\n") { (k, v) ->
                    "$k: ${if (v is JsonPrimitive) v.content else v.toString()}"
                }.ifBlank { "{}" }
                else -> el.toString()
            }
        }
    }
}

/** Manual factory — pulls the container off the Application. */
class DrifterViewModelFactory(private val app: Application) : ViewModelProvider.Factory {
    override fun <T : androidx.lifecycle.ViewModel> create(modelClass: Class<T>): T {
        val container: AppContainer = (app as DrifterApp).container
        @Suppress("UNCHECKED_CAST")
        return DrifterViewModel(app, container.repository, container.settingsStore) as T
    }
}
