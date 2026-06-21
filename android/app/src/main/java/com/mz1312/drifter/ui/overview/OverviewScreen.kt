package com.mz1312.drifter.ui.overview

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavController
import com.mz1312.drifter.data.model.FailureKind
import com.mz1312.drifter.data.model.Healthz
import com.mz1312.drifter.ui.DrifterViewModel
import com.mz1312.drifter.ui.common.BulletList
import com.mz1312.drifter.ui.common.InfoRow
import com.mz1312.drifter.ui.common.Loadable
import com.mz1312.drifter.ui.common.SectionCard
import com.mz1312.drifter.ui.common.Severity
import com.mz1312.drifter.ui.common.StatusPill
import com.mz1312.drifter.ui.common.color
import com.mz1312.drifter.ui.nav.Destination

@Composable
fun OverviewScreen(vm: DrifterViewModel, nav: NavController) {
    val health by vm.health.collectAsStateWithLifecycle()
    val mode by vm.mode.collectAsStateWithLifecycle()
    val refreshing by vm.refreshing.collectAsStateWithLifecycle()
    val lastRefresh by vm.lastRefresh.collectAsStateWithLifecycle()

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        when (val h = health) {
            is Loadable.Success -> HealthyView(h.value, mode?.choices ?: emptyList(), vm)
            is Loadable.Error -> UnreachableView(h, nav)
            else -> SectionCard("Connecting…") {
                Text(
                    if (refreshing) "Probing the node…" else "No data yet.",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }

        Text(
            text = if (lastRefresh == 0L) "" else "Updated ${relTime(lastRefresh)}",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HealthyView(h: Healthz, choices: List<String>, vm: DrifterViewModel) {
    val (sev, label) = when (h.health) {
        Healthz.Health.OK -> Severity.GOOD to "NODE OK"
        Healthz.Health.HW_PENDING -> Severity.WARN to "HW PENDING"
        Healthz.Health.DEGRADED -> Severity.BAD to "DEGRADED"
        else -> Severity.NEUTRAL to h.status.uppercase()
    }

    SectionCard(
        title = if (h.nodeId.isBlank()) "Node" else h.nodeId,
        trailing = { StatusPill(label, sev) },
    ) {
        InfoRow("Mode", h.mode)
        InfoRow("Services active", "${h.activeCount} / ${h.totalCount}")
        InfoRow(
            "MQTT bus",
            if (h.mqttConnected) "connected" else "down",
            valueColor = (if (h.mqttConnected) Severity.GOOD else Severity.BAD).let { it.color() },
        )
        InfoRow(
            "Telemetry",
            if (h.telemetryFresh) "fresh (<30s)" else "stale",
            valueColor = (if (h.telemetryFresh) Severity.GOOD else Severity.WARN).color(),
        )
        InfoRow("Dashboard WS clients", h.wsClients.toString())
    }

    if (h.servicesFailed.isNotEmpty()) {
        SectionCard("Failed services", trailing = { StatusPill("${h.servicesFailed.size}", Severity.BAD) }) {
            BulletList(h.servicesFailed)
            Text(
                "These count against the mode and return /healthz 503. Open the Services tab to restart.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }

    if (h.servicesHwPending.isNotEmpty()) {
        SectionCard("Hardware-pending", trailing = { StatusPill("${h.servicesHwPending.size}", Severity.WARN) }) {
            Text(
                "Idle until their dongle is plugged in — not a fault on the bench:",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(6.dp))
            BulletList(h.servicesHwPending)
        }
    }

    SectionCard("Mode") {
        Text(
            "Switch the node's persona. diag is the lean floor; drive adds the LLM/voice stack; foot is the recon/arsenal persona.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            choices.ifEmpty { listOf("diag", "drive", "foot", "both") }.forEach { c ->
                FilterChip(
                    selected = c == h.mode,
                    onClick = { if (c != h.mode) vm.setMode(c) },
                    label = { Text(c) },
                )
            }
        }
    }
}

@Composable
private fun UnreachableView(err: Loadable.Error, nav: NavController) {
    val hint = when (err.kind) {
        FailureKind.FORBIDDEN -> "The node answered with 403 — your phone isn't on the 10.42.0.0/24 hotspot. Join MZ1312_DRIFTER."
        FailureKind.TIMEOUT -> "The node accepted the connection but didn't answer in time. The dashboard may be wedged — try the Doctor, then restart drifter-dashboard."
        FailureKind.DEGRADED -> "The dashboard is up but reporting degraded (HTTP 503). Open Services to see what failed."
        else -> "Can't reach the node. Check you're on the MZ1312_DRIFTER Wi-Fi and the host in Settings is right."
    }
    SectionCard(
        title = "Node unreachable",
        trailing = { StatusPill("OFFLINE", Severity.BAD) },
    ) {
        Text(err.message, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
        Spacer(Modifier.height(8.dp))
        Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { nav.navigate(Destination.Doctor.route) }) { Text("Run Connection Doctor") }
            OutlinedButton(onClick = { nav.navigate(Destination.SETTINGS_ROUTE) }) { Text("Settings") }
        }
    }
}

private fun relTime(epochMs: Long): String {
    val secs = (System.currentTimeMillis() - epochMs) / 1000
    return when {
        secs < 5 -> "just now"
        secs < 60 -> "${secs}s ago"
        secs < 3600 -> "${secs / 60}m ago"
        else -> "${secs / 3600}h ago"
    }
}
