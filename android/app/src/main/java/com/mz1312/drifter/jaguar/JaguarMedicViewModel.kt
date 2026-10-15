package com.mz1312.drifter.jaguar

import android.app.Application
import android.os.SystemClock
import android.util.AtomicFile
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.io.File
import java.time.LocalDate
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.math.abs

class JaguarMedicViewModel(app: Application) : AndroidViewModel(app) {
    private val json = Json { ignoreUnknownKeys = true; isLenient = true; prettyPrint = false }
    private val prefs = app.getSharedPreferences("jaguar_medic", 0)
    private var elm = Elm327Client(app)
    private val http = OkHttpClient.Builder().connectTimeout(4, TimeUnit.SECONDS).readTimeout(8, TimeUnit.SECONDS).build()

    private val _link = MutableStateFlow(LinkUiState())
    val link: StateFlow<LinkUiState> = _link.asStateFlow()
    private val _snapshot = MutableStateFlow(LiveSnapshot())
    val snapshot: StateFlow<LiveSnapshot> = _snapshot.asStateFlow()
    private val _dtcs = MutableStateFlow<List<String>>(emptyList())
    val dtcs: StateFlow<List<String>> = _dtcs.asStateFlow()
    private val _devices = MutableStateFlow<List<BondedElmDevice>>(emptyList())
    val devices: StateFlow<List<BondedElmDevice>> = _devices.asStateFlow()
    private val _diagnosis = MutableStateFlow<List<DiagnosisLane>>(emptyList())
    val diagnosis: StateFlow<List<DiagnosisLane>> = _diagnosis.asStateFlow()
    private val _evidence = MutableStateFlow<List<EvidenceHit>>(emptyList())
    val evidence: StateFlow<List<EvidenceHit>> = _evidence.asStateFlow()
    private val _events = MutableStateFlow(loadEvents())
    val events: StateFlow<List<CarEvent>> = _events.asStateFlow()
    private val _incidents = MutableStateFlow(loadIncidents())
    val incidents: StateFlow<List<IncidentSummary>> = _incidents.asStateFlow()
    private val _message = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages = _message.asSharedFlow()

    private var connectionJob: Job? = null
    private var scanJob: Job? = null
    private var session = 0
    private var pollJob: Job? = null
    private var nodeSocket: WebSocket? = null
    private val recorder = IncidentRecorder(SystemClock::elapsedRealtime)
    private var captureJob: Job? = null
    private var searchJob: Job? = null
    private var diagnosisJob: Job? = null
    private var searchRequest = 0
    private var diagnosisRequest = 0

    var wifiHost: String
        get() = prefs.getString("wifi_host", "192.168.0.10") ?: "192.168.0.10"
        set(v) { prefs.edit().putString("wifi_host", v.trim()).apply() }
    var wifiPort: Int
        get() = prefs.getInt("wifi_port", 35000)
        set(v) { prefs.edit().putInt("wifi_port", v).apply() }
    var drifterHost: String
        get() = prefs.getString("drifter_host", "10.42.0.1") ?: "10.42.0.1"
        set(v) { prefs.edit().putString("drifter_host", v.trim()).apply() }
    var workshopBase: String
        get() = prefs.getString("workshop_base", "http://10.42.0.1:8765") ?: "http://10.42.0.1:8765"
        set(v) { prefs.edit().putString("workshop_base", v.trim().trimEnd('/')).apply() }

    init {
        if (_events.value.isEmpty()) seedKnownCarHistory()
    }

    fun refreshBondedDevices() {
        viewModelScope.launch {
            runCatching { elm.bondedDevices() }
                .onSuccess { _devices.value = it }
                .onFailure { _message.tryEmit("Bluetooth access is required to list paired ELM327 adapters") }
        }
    }

    fun connectBluetooth(address: String) = connect(LinkMode.BLUETOOTH_ELM, "Bluetooth ELM327") { client ->
        client.connectBluetooth(address)
    }

    fun connectWifi(host: String, port: Int) {
        if (host.isBlank() || port !in 1..65535) {
            _message.tryEmit("Enter an ELM host and a port from 1 to 65535")
            return
        }
        wifiHost = host; wifiPort = port
        connect(LinkMode.WIFI_ELM, "${host.trim()}:$port") { client ->
            client.connectWifi(host.trim(), port)
        }
    }

    fun connectDrifter(host: String) {
        drifterHost = host
        disconnect()
        _link.value = LinkUiState(LinkMode.DRIFTER, host, busy = true)
        val activeSession = session
        val req = runCatching { Request.Builder().url("ws://$host:8081").build() }
            .getOrElse {
                _link.value = LinkUiState(LinkMode.DRIFTER, host, error = "Invalid DRIFTER host")
                return
            }
        nodeSocket = http.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                viewModelScope.launch { if (session == activeSession) _link.value = LinkUiState(LinkMode.DRIFTER, host, connected = true) }
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching {
                    val obj = json.parseToJsonElement(text).jsonObject
                    val topic = obj["topic"]?.jsonPrimitive?.content ?: return
                    val data = obj["data"] ?: return
                    viewModelScope.launch { if (session == activeSession) ingestNodeTopic(topic, data) }
                }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                viewModelScope.launch { if (session == activeSession) connectionLost(t.message ?: "DRIFTER socket failed") }
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
                viewModelScope.launch { if (session == activeSession) connectionLost(reason.ifBlank { "Disconnected" }) }
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                viewModelScope.launch { if (session == activeSession) connectionLost(reason.ifBlank { "Disconnected" }) }
            }
        })
    }

    private fun connect(mode: LinkMode, label: String, block: suspend (Elm327Client) -> Unit) {
        disconnect()
        val activeSession = session
        // A cancelled connection must never install its socket into a newer session.
        val client = Elm327Client(getApplication())
        elm = client
        _link.value = LinkUiState(mode, label, busy = true)
        connectionJob = viewModelScope.launch {
            try {
                block(client)
                if (session == activeSession) {
                    _link.value = LinkUiState(mode, label, connected = true)
                    startElmPolling(client, activeSession)
                } else client.close()
            } catch (cancelled: CancellationException) {
                client.close()
                throw cancelled
            } catch (error: Exception) {
                client.close()
                if (session == activeSession) {
                    _link.value = LinkUiState(mode, label, error = error.message ?: "Connection failed")
                }
            }
        }
    }

    private fun startElmPolling(client: Elm327Client, activeSession: Int) {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            while (isActive && session == activeSession) {
                try {
                    val snapshot = client.poll()
                    if (session == activeSession) updateSnapshot(snapshot)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: Exception) {
                    client.close()
                    if (session == activeSession) {
                        connectionLost(error.message ?: "OBD read failed")
                    }
                    return@launch
                }
                delay(350)
            }
        }
    }

    fun disconnect() {
        session++
        connectionJob?.cancel(); connectionJob = null
        scanJob?.cancel(); scanJob = null
        pollJob?.cancel(); pollJob = null
        nodeSocket?.cancel(); nodeSocket = null
        elm.close()
        // Returning to DRIFTER preserves the samples already collected.
        finishIncident()
        recorder.clearHistory()
        _snapshot.value = LiveSnapshot()
        _dtcs.value = emptyList()
        _link.value = LinkUiState()
    }

    private fun connectionLost(message: String) {
        val previous = _link.value
        disconnect()
        _link.value = previous.copy(connected = false, busy = false, error = message)
    }

    fun scanDtcs() {
        if (!_link.value.connected) {
            _message.tryEmit("Connect an ELM327 adapter before scanning")
            return
        }
        if (_link.value.mode == LinkMode.DRIFTER) {
            _message.tryEmit("Direct DTC scan currently uses the ELM327 link; DRIFTER telemetry can still feed diagnosis")
            return
        }
        if (scanJob?.isActive == true) return
        val client = elm
        val activeSession = session
        scanJob = viewModelScope.launch {
            try {
                val codes = client.readDtcs()
                if (session == activeSession) {
                    _dtcs.value = codes
                    _message.tryEmit(if (codes.isEmpty()) "No Mode-03 DTCs returned" else "${codes.size} DTC(s) captured")
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                if (session == activeSession) _message.tryEmit("DTC scan failed: ${error.message}")
            }
        }
    }

    fun diagnose(query: String) {
        diagnosisJob?.cancel()
        val request = ++diagnosisRequest
        val base = workshopBase
        val local = DiagnosticEngine.diagnose(query, _snapshot.value, _dtcs.value)
        _diagnosis.value = local
        diagnosisJob = viewModelScope.launch {
            try {
                val remote = remoteDiagnosis(query, base)
                if (request == diagnosisRequest && base == workshopBase && remote.isNotEmpty()) {
                    _diagnosis.value = mergeDiagnosis(local, remote)
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                // The current local diagnosis remains available offline.
            }
        }
    }

    fun searchWorkshop(query: String) {
        searchJob?.cancel()
        val request = ++searchRequest
        val base = workshopBase
        _evidence.value = OfflineKnowledge.search(query)
        searchJob = viewModelScope.launch {
            try {
                val remote = remoteSearch(query, base)
                if (request == searchRequest && base == workshopBase && remote.isNotEmpty()) _evidence.value = remote
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                // Keep this query's offline results if the workshop service is unavailable.
            }
        }
    }

    fun captureIncident(note: String = "manual capture") {
        if (recorder.isCapturing) {
            _message.tryEmit("Incident capture is already active")
        } else if (_link.value.connected && recorder.start(System.currentTimeMillis(), note)) {
            captureJob = viewModelScope.launch {
                delay(IncidentRecorder.AFTER_MS)
                finishIncident()
            }
            _message.tryEmit("Incident capture armed: up to 90s before + 45s after")
        } else _message.tryEmit("Connect telemetry and wait for a reading before capturing")
    }

    fun addEvent(kind: String, subsystem: String, summary: String, details: String) {
        if (summary.isBlank()) return
        val item = CarEvent(UUID.randomUUID().toString(), LocalDate.now().toString(), kind, subsystem, summary.trim(), details.trim())
        _events.value = listOf(item) + _events.value
        saveEvents()
    }

    fun resolveEvent(id: String) {
        _events.value = _events.value.map { if (it.id == id) it.copy(status = "resolved") else it }
        saveEvents()
    }

    private fun updateSnapshot(s: LiveSnapshot) {
        _snapshot.value = s
        recorder.record(s)
        if (!recorder.isCapturing) autoTrigger(s)
    }

    private fun autoTrigger(s: LiveSnapshot) {
        val reason = when {
            (s.coolantC ?: 0.0) >= FactorySpecs.COOLANT_CRITICAL -> "coolant critical"
            s.engineRunning && (s.voltage ?: 99.0) < FactorySpecs.VOLTAGE_LOW_RUNNING -> "running voltage collapse"
            s.warm && listOfNotNull(s.stftB1, s.stftB2).any { abs(it) >= 25 } -> "fuel trim excursion"
            else -> null
        }
        if (reason != null) captureIncident(reason)
    }

    private fun finishIncident() {
        captureJob?.cancel(); captureJob = null
        val a = recorder.finish() ?: return
        val dir = File(getApplication<Application>().filesDir, "incidents").apply { mkdirs() }
        val name = "incident_${a.triggerTs}.json"
        val file = AtomicFile(File(dir, name))
        val payload = json.encodeToString(a.samples)
        try {
            val stream = file.startWrite()
            try {
                stream.write(payload.toByteArray(Charsets.UTF_8))
                file.finishWrite(stream)
            } catch (error: Exception) {
                file.failWrite(stream)
                throw error
            }
        } catch (error: Exception) {
            _message.tryEmit("Incident could not be saved: ${error.message}")
            return
        }
        val before = a.samples.filter { it.ts < a.triggerTs }
        val after = a.samples.filter { it.ts >= a.triggerTs }
        val first = changeAtTrigger(before, after)
        val summary = IncidentSummary(a.triggerTs.toString(), a.triggerTs, a.reason, name, a.samples.size, first)
        _incidents.value = listOf(summary) + _incidents.value
        saveIncidents()
        _message.tryEmit("Incident saved: ${a.samples.size} samples")
    }

    private fun changeAtTrigger(before: List<LiveSnapshot>, after: List<LiveSnapshot>): String? {
        if (before.isEmpty() || after.isEmpty()) return null
        val base = before.takeLast(12)
        fun avg(f: (LiveSnapshot) -> Double?) = base.mapNotNull(f).takeIf { it.isNotEmpty() }?.average()
        val latest = after.firstOrNull() ?: return null
        // Report each observed difference, without ranking unlike units or claiming causality.
        val changes = listOf(
            "RPM" to (avg { it.rpm } to latest.rpm),
            "MAF" to (avg { it.mafGps } to latest.mafGps),
            "STFT B1" to (avg { it.stftB1 } to latest.stftB1),
            "STFT B2" to (avg { it.stftB2 } to latest.stftB2),
            "Voltage" to (avg { it.voltage } to latest.voltage),
        ).mapNotNull { (name, pair) ->
            val a = pair.first ?: return@mapNotNull null
            val b = pair.second ?: return@mapNotNull null
            if (abs(b - a) < 0.01) null else "$name ${"%.2f".format(a)} → ${"%.2f".format(b)}"
        }
        return changes.takeIf { it.isNotEmpty() }?.joinToString(prefix = "At trigger: ", separator = "; ")
    }

    private fun ingestNodeTopic(topic: String, data: JsonElement) {
        fun num(): Double? = runCatching {
            when (data) {
                is JsonObject -> data["value"]?.jsonPrimitive?.doubleOrNull
                else -> data.jsonPrimitive.doubleOrNull
            }
        }.getOrNull()
        val v = num()?.takeIf { it.isFinite() } ?: return
        val old = _snapshot.value
        val s = when (topic.removePrefix("drifter/")) {
            "engine/rpm" -> old.copy(ts = System.currentTimeMillis(), rpm = v)
            "vehicle/speed" -> old.copy(ts = System.currentTimeMillis(), speedKph = v)
            "engine/coolant" -> old.copy(ts = System.currentTimeMillis(), coolantC = v)
            "engine/maf" -> old.copy(ts = System.currentTimeMillis(), mafGps = v)
            "engine/stft1" -> old.copy(ts = System.currentTimeMillis(), stftB1 = v)
            "engine/ltft1" -> old.copy(ts = System.currentTimeMillis(), ltftB1 = v)
            "engine/stft2" -> old.copy(ts = System.currentTimeMillis(), stftB2 = v)
            "engine/ltft2" -> old.copy(ts = System.currentTimeMillis(), ltftB2 = v)
            "engine/throttle" -> old.copy(ts = System.currentTimeMillis(), throttlePct = v)
            "engine/load" -> old.copy(ts = System.currentTimeMillis(), loadPct = v)
            "engine/iat" -> old.copy(ts = System.currentTimeMillis(), intakeC = v)
            "power/voltage" -> old.copy(ts = System.currentTimeMillis(), voltage = v)
            else -> return
        }
        updateSnapshot(s)
    }

    private suspend fun remoteSearch(q: String, base: String): List<EvidenceHit> = withContext(Dispatchers.IO) {
        val url = "$base/api/search?q=${java.net.URLEncoder.encode(q, "UTF-8")}&n=10"
        http.newCall(Request.Builder().url(url).build()).execute().use { response ->
            if (!response.isSuccessful) return@withContext emptyList()
            val root = json.parseToJsonElement(response.body?.string().orEmpty()).jsonObject
            root["results"]?.jsonArray.orEmpty().mapNotNull { el ->
                val o = el.jsonObject
                EvidenceHit(
                    title = o["title"]?.jsonPrimitive?.content ?: return@mapNotNull null,
                    page = o["page"]?.jsonPrimitive?.content?.toIntOrNull(),
                    snippet = o["text"]?.jsonPrimitive?.content ?: "",
                    applicability = o["applicability"]?.jsonPrimitive?.content ?: "",
                    score = o["score"]?.jsonPrimitive?.doubleOrNull ?: 0.0,
                )
            }
        }
    }

    private suspend fun remoteDiagnosis(q: String, base: String): List<DiagnosisLane> = withContext(Dispatchers.IO) {
        val url = "$base/api/diagnose?q=${java.net.URLEncoder.encode(q, "UTF-8")}" 
        http.newCall(Request.Builder().url(url).build()).execute().use { response ->
            if (!response.isSuccessful) return@withContext emptyList()
            val root = json.parseToJsonElement(response.body?.string().orEmpty()).jsonObject
            root["priority_lanes"]?.jsonArray.orEmpty().mapIndexedNotNull { idx, el ->
                val o = el.jsonObject
                DiagnosisLane(
                    priority = idx + 1,
                    title = o["title"]?.jsonPrimitive?.content ?: return@mapIndexedNotNull null,
                    reason = o["reason"]?.jsonPrimitive?.content ?: "",
                    checks = o["checks"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty(),
                    evidence = o["sources"]?.jsonArray?.map { src ->
                        val so = src.jsonObject
                        "${so["title"]?.jsonPrimitive?.content ?: "Factory source"} · p.${so["page"]?.jsonPrimitive?.content ?: "?"}"
                    }.orEmpty(),
                )
            }
        }
    }

    private fun mergeDiagnosis(local: List<DiagnosisLane>, remote: List<DiagnosisLane>): List<DiagnosisLane> {
        val seen = mutableSetOf<String>()
        return (remote + local).filter { seen.add(it.title.lowercase()) }.mapIndexed { i, it -> it.copy(priority = i + 1) }
    }

    private fun seedKnownCarHistory() {
        val seed = listOf(
            CarEvent("seed-o2", "2026-10-12", "repair", "engine", "Bank 1 upstream O₂ sensor previously replaced", "Installation/sealing may need re-checking; rough-running symptoms existed around this work."),
            CarEvent("seed-maf", "2026-10-12", "observation", "intake", "MAF electrical connection is uncertain", "Sensor/electrical port has looked unusual; verify actual harness connector and live MAF before replacing parts."),
            CarEvent("seed-run", "2026-10-12", "symptom", "engine", "Rough idle, stutter from stop and occasional stall", "Priority drivability fault. Capture warm-idle trims/MAF and an incident when it occurs."),
            CarEvent("seed-fuel", "2026-10-12", "symptom", "fuel", "Fuel gauge never reads above about half", "Factory bulletin XT310-05 is directly relevant."),
            CarEvent("seed-speed", "2026-10-12", "symptom", "cluster", "Speedometer reads roughly 10 km/h high", "Keep separate from drivability diagnosis unless shared cluster evidence emerges."),
        )
        _events.value = seed
        saveEvents()
    }

    private fun loadEvents(): List<CarEvent> = runCatching {
        json.decodeFromString(ListSerializer(CarEvent.serializer()), prefs.getString("events", "[]") ?: "[]")
    }.getOrDefault(emptyList())
    private fun saveEvents() = prefs.edit().putString("events", json.encodeToString(ListSerializer(CarEvent.serializer()), _events.value)).apply()
    private fun loadIncidents(): List<IncidentSummary> = runCatching {
        json.decodeFromString(ListSerializer(IncidentSummary.serializer()), prefs.getString("incidents", "[]") ?: "[]")
    }.getOrDefault(emptyList())
    private fun saveIncidents() = prefs.edit().putString("incidents", json.encodeToString(ListSerializer(IncidentSummary.serializer()), _incidents.value)).apply()

    override fun onCleared() {
        disconnect(); http.dispatcher.cancelAll(); http.dispatcher.executorService.shutdown(); super.onCleared()
    }
}
