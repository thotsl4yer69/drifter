package com.mz1312.drifter

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.mz1312.drifter.ui.DrifterViewModel
import com.mz1312.drifter.ui.DrifterViewModelFactory
import com.mz1312.drifter.ui.arsenal.ArsenalScreen
import com.mz1312.drifter.ui.assistant.AssistantScreen
import com.mz1312.drifter.ui.doctor.DoctorScreen
import com.mz1312.drifter.ui.nav.Destination
import com.mz1312.drifter.ui.overview.OverviewScreen
import com.mz1312.drifter.ui.services.ServicesScreen
import com.mz1312.drifter.ui.settings.SettingsScreen
import com.mz1312.drifter.ui.telemetry.TelemetryScreen
import com.mz1312.drifter.ui.theme.DrifterTheme
import kotlinx.coroutines.flow.collectLatest

class MainActivity : ComponentActivity() {

    private val viewModel: DrifterViewModel by viewModels {
        DrifterViewModelFactory(application)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        maybeRequestNotificationPermission()
        setContent {
            DrifterTheme {
                DrifterApp(viewModel)
            }
        }
    }

    /** Android 13+ gates notifications behind a runtime grant; ask once on launch. */
    private fun maybeRequestNotificationPermission() {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1001)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DrifterApp(vm: DrifterViewModel) {
    val navController = rememberNavController()
    val backStack by navController.currentBackStackEntryAsState()
    val currentRoute = backStack?.destination?.route
    val snackbar = remember { SnackbarHostState() }

    val settings by vm.settings.collectAsStateWithLifecycle()
    val health by vm.health.collectAsStateWithLifecycle()

    // Live link pip shown in the app bar on every tab — at-a-glance "can I reach
    // the node?" without opening Overview.
    val connSeverity = when (val h = health) {
        is com.mz1312.drifter.ui.common.Loadable.Success -> when (h.value.health) {
            com.mz1312.drifter.data.model.Healthz.Health.OK ->
                com.mz1312.drifter.ui.common.Severity.GOOD
            com.mz1312.drifter.data.model.Healthz.Health.HW_PENDING ->
                com.mz1312.drifter.ui.common.Severity.WARN
            com.mz1312.drifter.data.model.Healthz.Health.DEGRADED ->
                com.mz1312.drifter.ui.common.Severity.BAD
            com.mz1312.drifter.data.model.Healthz.Health.UNKNOWN ->
                com.mz1312.drifter.ui.common.Severity.NEUTRAL
        }
        is com.mz1312.drifter.ui.common.Loadable.Error ->
            com.mz1312.drifter.ui.common.Severity.BAD
        else -> com.mz1312.drifter.ui.common.Severity.NEUTRAL
    }

    androidx.compose.runtime.LaunchedEffect(Unit) {
        vm.messages.collectLatest { snackbar.showSnackbar(it) }
    }

    val title = Destination.bottomBar.firstOrNull { it.route == currentRoute }?.label
        ?: if (currentRoute == Destination.SETTINGS_ROUTE) "Settings" else "Drifter"

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    androidx.compose.foundation.layout.Column {
                        Text(
                            "MZ1312 · DRIFTER",
                            style = androidx.compose.material3.MaterialTheme.typography.labelMedium,
                            color = androidx.compose.material3.MaterialTheme.colorScheme.primary,
                        )
                        Text(
                            "$title  ·  ${settings.host}",
                            style = androidx.compose.material3.MaterialTheme.typography.titleMedium,
                        )
                    }
                },
                colors = androidx.compose.material3.TopAppBarDefaults.topAppBarColors(
                    containerColor = androidx.compose.material3.MaterialTheme.colorScheme.surface,
                    scrolledContainerColor = androidx.compose.material3.MaterialTheme.colorScheme.surface,
                ),
                actions = {
                    com.mz1312.drifter.ui.common.StatusDot(connSeverity, 9)
                    androidx.compose.foundation.layout.Spacer(
                        Modifier.width(8.dp),
                    )
                    IconButton(onClick = { vm.refreshNow() }) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Refresh")
                    }
                    IconButton(onClick = {
                        if (currentRoute != Destination.SETTINGS_ROUTE) {
                            navController.navigate(Destination.SETTINGS_ROUTE)
                        }
                    }) {
                        Icon(Icons.Filled.Settings, contentDescription = "Settings")
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar(
                containerColor = androidx.compose.material3.MaterialTheme.colorScheme.surfaceContainerLow,
            ) {
                Destination.bottomBar.forEach { dest ->
                    NavigationBarItem(
                        selected = currentRoute == dest.route,
                        onClick = {
                            navController.navigate(dest.route) {
                                popUpTo(Destination.Overview.route) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = { Icon(dest.icon, contentDescription = dest.label) },
                        label = { Text(dest.label) },
                        colors = androidx.compose.material3.NavigationBarItemDefaults.colors(
                            selectedIconColor = androidx.compose.material3.MaterialTheme.colorScheme.primary,
                            selectedTextColor = androidx.compose.material3.MaterialTheme.colorScheme.primary,
                            indicatorColor = androidx.compose.material3.MaterialTheme.colorScheme.primary.copy(alpha = 0.16f),
                            unselectedIconColor = androidx.compose.material3.MaterialTheme.colorScheme.onSurfaceVariant,
                            unselectedTextColor = androidx.compose.material3.MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
                    )
                }
            }
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = Destination.Overview.route,
            modifier = Modifier.padding(padding),
        ) {
            composable(Destination.Overview.route) { OverviewScreen(vm, navController) }
            composable(Destination.Doctor.route) { DoctorScreen(vm) }
            composable(Destination.Assistant.route) { AssistantScreen(vm) }
            composable(Destination.Services.route) { ServicesScreen(vm) }
            composable(Destination.Arsenal.route) { ArsenalScreen(vm) }
            composable(Destination.Telemetry.route) { TelemetryScreen(vm) }
            composable(Destination.SETTINGS_ROUTE) {
                SettingsScreen(vm, onDone = { navController.popBackStack() })
            }
        }
    }
}
