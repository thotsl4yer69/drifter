package com.mz1312.drifter.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mz1312.drifter.data.store.AppSettings
import com.mz1312.drifter.ui.DrifterViewModel
import com.mz1312.drifter.ui.common.SectionCard

@Composable
fun SettingsScreen(vm: DrifterViewModel, onDone: () -> Unit) {
    val current by vm.settings.collectAsStateWithLifecycle()

    var host by rememberSaveable { mutableStateOf(current.host) }
    var httpPort by rememberSaveable { mutableStateOf(current.httpPort.toString()) }
    var wsPort by rememberSaveable { mutableStateOf(current.wsPort.toString()) }
    var poll by rememberSaveable { mutableStateOf(current.pollSeconds.toString()) }
    var auto by rememberSaveable { mutableStateOf(current.autoRefresh) }

    fun save() {
        vm.updateSettings(
            AppSettings(
                host = host.trim().ifBlank { AppSettings.DEFAULT_HOST },
                httpPort = httpPort.toIntOrNull() ?: AppSettings.DEFAULT_HTTP_PORT,
                wsPort = wsPort.toIntOrNull() ?: AppSettings.DEFAULT_WS_PORT,
                pollSeconds = poll.toIntOrNull() ?: AppSettings.DEFAULT_POLL_SECONDS,
                autoRefresh = auto,
            ),
        )
        vm.refreshNow()
        onDone()
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        SectionCard("Node connection") {
            OutlinedTextField(
                value = host,
                onValueChange = { host = it },
                label = { Text("Host / IP") },
                supportingText = { Text("Default 10.42.0.1 on the MZ1312_DRIFTER hotspot. Use the Pi's LAN IP if tethered elsewhere.") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = httpPort,
                    onValueChange = { httpPort = it.filter(Char::isDigit) },
                    label = { Text("HTTP port") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = wsPort,
                    onValueChange = { wsPort = it.filter(Char::isDigit) },
                    label = { Text("Telemetry WS port") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
            }
        }

        SectionCard("Refresh") {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Auto-refresh", style = MaterialTheme.typography.bodyLarge)
                Switch(checked = auto, onCheckedChange = { auto = it })
            }
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = poll,
                onValueChange = { poll = it.filter(Char::isDigit) },
                label = { Text("Poll interval (seconds)") },
                supportingText = { Text("2–60s. /healthz is cached 2s server-side, so frequent polling is cheap.") },
                enabled = auto,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = ::save) { Text("Save") }
            OutlinedButton(onClick = {
                host = AppSettings.DEFAULT_HOST
                httpPort = AppSettings.DEFAULT_HTTP_PORT.toString()
                wsPort = AppSettings.DEFAULT_WS_PORT.toString()
                poll = AppSettings.DEFAULT_POLL_SECONDS.toString()
                auto = true
            }) { Text("Reset to hotspot defaults") }
        }

        Text(
            "Drifter Diagnostics — MZ1312 UNCAGED TECHNOLOGY. The app talks to the node over plain HTTP on the local hotspot; all /api control is gated server-side to 10.42.0.0/24.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
