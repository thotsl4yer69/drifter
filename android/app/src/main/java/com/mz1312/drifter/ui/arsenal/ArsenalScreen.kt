package com.mz1312.drifter.ui.arsenal

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mz1312.drifter.data.Knowledge
import com.mz1312.drifter.data.ServiceCategory
import com.mz1312.drifter.ui.DrifterViewModel
import com.mz1312.drifter.ui.common.Loadable
import com.mz1312.drifter.ui.common.Mono
import com.mz1312.drifter.ui.common.SectionCard
import com.mz1312.drifter.ui.common.Severity
import com.mz1312.drifter.ui.common.StatusDot
import com.mz1312.drifter.ui.common.StatusPill
import com.mz1312.drifter.ui.common.color

private val ARSENAL_CATEGORIES = setOf(
    ServiceCategory.CARSENAL, ServiceCategory.RECON, ServiceCategory.COUNTER,
)

@Composable
fun ArsenalScreen(vm: DrifterViewModel) {
    val health by vm.health.collectAsStateWithLifecycle()
    val mode by vm.mode.collectAsStateWithLifecycle()
    val arsenal by vm.arsenal.collectAsStateWithLifecycle()

    LaunchedEffect(Unit) { if (arsenal is Loadable.Idle) vm.refreshArsenal() }

    val h = (health as? Loadable.Success)?.value
    val currentMode = h?.mode ?: mode?.mode ?: "unknown"
    val footActive = currentMode == "foot" || currentMode == "both"

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        SectionCard(
            title = "Carsenal / Kali arsenal",
            trailing = { StatusPill(if (footActive) "FOOT" else "MODE: $currentMode", if (footActive) Severity.GOOD else Severity.NEUTRAL) },
        ) {
            Text(
                "The node runs Kali in the background — this is the red-team + CAN-offense surface. " +
                    "Arsenal service control is gated to foot mode on the Pi (it refuses in drive mode), so switch personas here first.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(10.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("diag", "drive", "foot", "both").forEach { c ->
                    FilterChip(
                        selected = c == currentMode,
                        onClick = { if (c != currentMode) vm.setMode(c) },
                        label = { Text(c) },
                    )
                }
            }
        }

        // Arsenal-relevant services with control.
        if (h != null) {
            val arsenalRows = h.services.entries
                .filter { Knowledge.categoryOf(it.key) in ARSENAL_CATEGORIES }
                .sortedWith(compareBy({ Knowledge.categoryOf(it.key).ordinal }, { it.key }))
            if (arsenalRows.isNotEmpty()) {
                SectionCard("Arsenal services") {
                    if (!footActive) {
                        Text(
                            "Not in foot mode — start/stop will be refused (409) until you switch.",
                            style = MaterialTheme.typography.labelSmall,
                            color = Severity.WARN.color(),
                        )
                        Spacer(Modifier.height(8.dp))
                    }
                    arsenalRows.forEach { (unit, active) ->
                        ArsenalServiceRow(unit, active, vm)
                    }
                }
            }
        }

        // Live tool status (read-only fan-out).
        SectionCard(
            title = "Tool status",
            trailing = { TextButton(onClick = { vm.refreshArsenal() }) { Text("Refresh") } },
        ) {
            when (val a = arsenal) {
                is Loadable.Success -> Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    a.value.forEach { tool ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            StatusDot(if (tool.reachable) Severity.GOOD else Severity.NEUTRAL)
                            Spacer(Modifier.height(0.dp))
                            Text(
                                "  ${tool.label}",
                                style = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                        Mono(tool.path)
                        Spacer(Modifier.height(2.dp))
                        Mono(
                            if (tool.reachable) tool.body.take(600) else "unreachable — ${tool.body}",
                            color = if (tool.reachable) MaterialTheme.colorScheme.onSurface
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                is Loadable.Loading -> Text("Polling tools…", style = MaterialTheme.typography.bodySmall)
                else -> Text(
                    "Tap Refresh to poll the arsenal endpoints.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun ArsenalServiceRow(unit: String, active: Boolean, vm: DrifterViewModel) {
    val doc = Knowledge.docFor(unit)
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(if (active) Severity.GOOD else Severity.NEUTRAL)
                Spacer(Modifier.height(0.dp))
                Column(Modifier.padding(start = 10.dp)) {
                    Text(doc.title, fontWeight = FontWeight.Medium, style = MaterialTheme.typography.bodyMedium)
                    Mono(unit)
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                OutlinedButton(onClick = { vm.serviceAction(unit, if (active) "stop" else "start") }) {
                    Text(if (active) "Stop" else "Start")
                }
            }
        }
    }
}
