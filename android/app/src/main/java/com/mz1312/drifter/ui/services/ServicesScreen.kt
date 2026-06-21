package com.mz1312.drifter.ui.services

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mz1312.drifter.data.Knowledge
import com.mz1312.drifter.data.ServiceCategory
import com.mz1312.drifter.ui.DrifterViewModel
import com.mz1312.drifter.ui.common.BulletList
import com.mz1312.drifter.ui.common.Loadable
import com.mz1312.drifter.ui.common.Mono
import com.mz1312.drifter.ui.common.Severity
import com.mz1312.drifter.ui.common.StatusDot
import com.mz1312.drifter.ui.common.StatusPill

private data class ServiceRow(
    val unit: String,
    val active: Boolean,
    val hwPending: Boolean,
)

@Composable
fun ServicesScreen(vm: DrifterViewModel) {
    val health by vm.health.collectAsStateWithLifecycle()
    val logs by vm.logs.collectAsStateWithLifecycle()
    val expanded = remember { mutableStateMapOf<String, Boolean>() }

    val h = (health as? Loadable.Success)?.value
    if (h == null) {
        Column(Modifier.fillMaxSize().padding(24.dp)) {
            Text("Waiting for /healthz…", style = MaterialTheme.typography.bodyMedium)
            Text(
                "Service inventory comes from the node's health payload. If this stays empty, run the Connection Doctor.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        return
    }

    val rows = h.services.entries
        .map { (unit, active) ->
            ServiceRow(unit, active, hwPending = unit in h.servicesHwPending || Knowledge.docFor(unit).hardwarePending)
        }
        .sortedWith(compareBy({ Knowledge.categoryOf(it.unit).ordinal }, { it.unit }))

    val grouped = rows.groupBy { Knowledge.categoryOf(it.unit) }

    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 12.dp),
    ) {
        grouped.forEach { (category, catRows) ->
            item(key = "hdr-${category.name}") {
                CategoryHeader(category, catRows)
            }
            items(catRows, key = { it.unit }) { row ->
                ServiceCard(
                    row = row,
                    expanded = expanded[row.unit] == true,
                    logsCell = logs[row.unit],
                    onToggle = { expanded[row.unit] = !(expanded[row.unit] ?: false) },
                    onAction = { action -> vm.serviceAction(row.unit, action) },
                    onFetchLogs = { vm.fetchLogs(row.unit) },
                )
            }
        }
    }
}

@Composable
private fun CategoryHeader(category: ServiceCategory, rows: List<ServiceRow>) {
    val up = rows.count { it.active }
    Row(
        Modifier.fillMaxWidth().padding(top = 10.dp, bottom = 2.dp, start = 4.dp, end = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            category.label.uppercase(),
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.primary,
            fontWeight = FontWeight.Bold,
        )
        Text(
            "$up/${rows.size} up",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun ServiceCard(
    row: ServiceRow,
    expanded: Boolean,
    logsCell: Loadable<List<String>>?,
    onToggle: () -> Unit,
    onAction: (String) -> Unit,
    onFetchLogs: () -> Unit,
) {
    val doc = Knowledge.docFor(row.unit)
    val sev = when {
        row.active -> Severity.GOOD
        row.hwPending -> Severity.WARN
        else -> Severity.BAD
    }
    androidx.compose.material3.Card(
        Modifier.fillMaxWidth().clickable(onClick = onToggle),
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    StatusDot(sev)
                    Spacer(Modifier.width(10.dp))
                    Column {
                        Text(doc.title, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyLarge)
                        Mono(row.unit)
                    }
                }
                val tag = when {
                    row.active -> "active"
                    row.hwPending -> "hw-pending"
                    else -> "down"
                }
                StatusPill(tag, sev)
            }

            AnimatedVisibility(visible = expanded) {
                Column(Modifier.padding(top = 12.dp)) {
                    Text(doc.role, style = MaterialTheme.typography.bodyMedium)
                    if (doc.kaliBacked) {
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "Kali-backed tool",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.secondary,
                        )
                    }
                    if (doc.remediation.isNotEmpty()) {
                        Spacer(Modifier.height(10.dp))
                        BulletList(doc.remediation)
                    }
                    Spacer(Modifier.height(12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { onAction("restart") }) { Text("Restart") }
                        TextButton(onClick = { onAction("start") }) { Text("Start") }
                        TextButton(onClick = { onAction("stop") }) { Text("Stop") }
                    }
                    Text(
                        "Control works for arsenal units in foot mode; core units are Pi-side only (the node will say so).",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )

                    Spacer(Modifier.height(12.dp))
                    LogsSection(logsCell = logsCell, onFetchLogs = onFetchLogs)
                }
            }
        }
    }
}

/**
 * Read-only journal tail for the unit, fetched on demand from GET
 * /api/logs/<unit>. Lets the operator see *why* a service is down without
 * the AI assistant — and gives the assistant's evidence a human-readable home.
 */
@Composable
private fun LogsSection(logsCell: Loadable<List<String>>?, onFetchLogs: () -> Unit) {
    when (logsCell) {
        null, Loadable.Idle -> TextButton(onClick = onFetchLogs) { Text("View recent logs") }
        Loadable.Loading -> Text(
            "Loading logs…",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        is Loadable.Success -> {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "Recent journal",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                TextButton(onClick = onFetchLogs) { Text("Refresh") }
            }
            val lines = logsCell.value
            if (lines.isEmpty()) {
                Text(
                    "No recent journal entries.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                Column(
                    Modifier.fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(MaterialTheme.colorScheme.surfaceVariant)
                        .padding(8.dp),
                ) {
                    lines.takeLast(60).forEach { Mono(it) }
                }
            }
        }
        is Loadable.Error -> {
            Text(
                "Couldn't load logs: ${logsCell.message}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
            TextButton(onClick = onFetchLogs) { Text("Retry") }
        }
    }
}
