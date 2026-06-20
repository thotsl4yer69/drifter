package com.mz1312.drifter.ui.nav

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ShowChart
import androidx.compose.material.icons.filled.HealthAndSafety
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.NetworkCheck
import androidx.compose.material.icons.filled.Security
import androidx.compose.ui.graphics.vector.ImageVector

enum class Destination(
    val route: String,
    val label: String,
    val icon: ImageVector,
) {
    Overview("overview", "Overview", Icons.Filled.HealthAndSafety),
    Doctor("doctor", "Doctor", Icons.Filled.NetworkCheck),
    Services("services", "Services", Icons.Filled.Memory),
    Arsenal("arsenal", "Arsenal", Icons.Filled.Security),
    Telemetry("telemetry", "Telemetry", Icons.AutoMirrored.Filled.ShowChart);

    companion object {
        val bottomBar = entries.toList()
        const val SETTINGS_ROUTE = "settings"
    }
}
