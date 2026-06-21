package com.mz1312.drifter.ui.telemetry

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mz1312.drifter.data.net.SocketState
import com.mz1312.drifter.ui.DrifterViewModel
import com.mz1312.drifter.ui.SignalValue
import com.mz1312.drifter.ui.common.Mono
import com.mz1312.drifter.ui.common.SectionCard
import com.mz1312.drifter.ui.common.Severity
import com.mz1312.drifter.ui.common.StatusPill

@Composable
fun TelemetryScreen(vm: DrifterViewModel) {
    val state by vm.telemetry.collectAsStateWithLifecycle()

    // Own the socket lifecycle to this screen — opening it is itself a probe
    // of the 8081 fan-out; leaving releases the connection.
    DisposableEffect(Unit) {
        vm.connectTelemetry()
        onDispose { vm.disconnectTelemetry() }
    }

    val (sev, label) = when (val s = state.socket) {
        is SocketState.Open -> Severity.GOOD to "STREAMING"
        is SocketState.Connecting -> Severity.WARN to "CONNECTING"
        is SocketState.Failed -> Severity.BAD to "FAILED"
        is SocketState.Closed -> Severity.NEUTRAL to "CLOSED"
    }

    // Signals with a gauge spec that we've actually received → render as gauges.
    val gauges = SIGNAL_SPECS.entries
        .filter { state.signals.containsKey(it.key) }
        .map { (key, spec) -> Triple(key, spec, state.signals[key]?.display?.toDoubleOrNull()) }
    val rest = state.signals.entries
        .filter { it.key !in SIGNAL_SPECS }
        .sortedBy { it.key }
        .map { it.key to it.value }

    LazyColumn(
        Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            SectionCard(title = "Live telemetry", trailing = { StatusPill(label, sev) }) {
                Mono(vm.settings.value.telemetryWsUrl)
                Spacer(Modifier.height(6.dp))
                Text(
                    "Frames received: ${state.frameCount}",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                )
                val sub = when (val s = state.socket) {
                    is SocketState.Failed -> "Socket failed: ${s.reason}. The 8081 fan-out may be down — check the Doctor and restart drifter-dashboard."
                    is SocketState.Closed -> "Disconnected. Re-open this tab to reconnect."
                    is SocketState.Connecting -> "Opening WebSocket…"
                    is SocketState.Open ->
                        if (state.frameCount == 0L)
                            "Connected but no frames yet — the bus may be quiet (engine off) or telemetry stale."
                        else "Live. Values update as MQTT publishes."
                }
                Spacer(Modifier.height(4.dp))
                Text(sub, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        if (gauges.isNotEmpty()) {
            item { SectionLabel("Vehicle vitals") }
            items(gauges.chunked(3), key = { row -> "g-" + row.joinToString(",") { it.first } }) { rowItems ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    rowItems.forEach { (key, spec, v) ->
                        GaugeTile(spec, v, state.history[key].orEmpty(), Modifier.weight(1f))
                    }
                    // Keep gauges square in a partial final row.
                    repeat(3 - rowItems.size) { Spacer(Modifier.weight(1f)) }
                }
            }
        }

        if (rest.isNotEmpty()) {
            item { SectionLabel("All topics (${rest.size})") }
            items(rest, key = { "r-${it.first}" }) { (topic, v) -> SignalRow(topic, v) }
        }
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(
        text.uppercase(),
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.primary,
        fontWeight = FontWeight.Bold,
        modifier = Modifier.padding(top = 4.dp),
    )
}

@Composable
private fun SignalRow(topic: String, value: SignalValue) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(topic, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodyMedium)
        Text(
            value.display,
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary,
        )
    }
}
