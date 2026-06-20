package com.mz1312.drifter.ui.doctor

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mz1312.drifter.data.net.ProbeResult
import com.mz1312.drifter.data.net.ProbeStatus
import com.mz1312.drifter.ui.DrifterViewModel
import com.mz1312.drifter.ui.common.BulletList
import com.mz1312.drifter.ui.common.Mono
import com.mz1312.drifter.ui.common.SectionCard
import com.mz1312.drifter.ui.common.Severity
import com.mz1312.drifter.ui.common.StatusDot
import com.mz1312.drifter.ui.common.StatusPill

@Composable
fun DoctorScreen(vm: DrifterViewModel) {
    val report by vm.doctor.collectAsStateWithLifecycle()

    // Auto-run once when the user first opens the tab.
    LaunchedEffect(Unit) {
        if (report.results.isEmpty() && !report.running) vm.runDoctor()
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        SectionCard(
            title = "Connection Doctor",
            trailing = {
                val sev = if (report.results.isEmpty()) Severity.NEUTRAL
                else if (report.ok) Severity.GOOD else Severity.BAD
                StatusPill(report.headline.uppercase(), sev)
            },
        ) {
            Text(
                "Probes every port the Pi should expose, from your phone — so you can tell whether the node is down, the dashboard is wedged, or you're just off the hotspot. This runs locally even when the node is unreachable.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Button(onClick = { vm.runDoctor() }, enabled = !report.running) {
                    Text(if (report.running) "Probing…" else "Re-run probes")
                }
                if (report.running) {
                    Spacer(Modifier.height(0.dp))
                    CircularProgressIndicator(
                        Modifier.padding(start = 12.dp).height(20.dp),
                        strokeWidth = 2.dp,
                    )
                }
            }
        }

        report.results.forEach { ProbeCard(it) }
    }
}

@Composable
private fun ProbeCard(p: ProbeResult) {
    val sev = when (p.status) {
        ProbeStatus.PASS -> Severity.GOOD
        ProbeStatus.WARN -> Severity.WARN
        ProbeStatus.FAIL -> Severity.BAD
        ProbeStatus.SKIP -> Severity.NEUTRAL
    }
    SectionCard(
        title = p.name,
        trailing = {
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                p.latencyMs?.let {
                    Mono("${it}ms  ")
                }
                StatusDot(sev)
            }
        },
    ) {
        Mono(p.target)
        Spacer(Modifier.height(6.dp))
        Text(
            p.detail,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = if (p.status == ProbeStatus.FAIL) FontWeight.Medium else FontWeight.Normal,
        )
        if (p.remediation.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Text(
                "Try this:",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(2.dp))
            BulletList(p.remediation)
        }
    }
}
