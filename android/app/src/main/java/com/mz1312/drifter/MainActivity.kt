package com.mz1312.drifter

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.core.tween
import androidx.compose.animation.slideInHorizontally
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.mz1312.drifter.data.model.Healthz
import com.mz1312.drifter.ui.DrifterViewModel
import com.mz1312.drifter.ui.DrifterViewModelFactory
import com.mz1312.drifter.ui.arsenal.ArsenalScreen
import com.mz1312.drifter.ui.assistant.AssistantScreen
import com.mz1312.drifter.ui.common.Loadable
import com.mz1312.drifter.ui.common.Severity
import com.mz1312.drifter.ui.common.StatusDot
import com.mz1312.drifter.ui.doctor.DoctorScreen
import com.mz1312.drifter.ui.nav.Destination
import com.mz1312.drifter.ui.overview.OverviewScreen
import com.mz1312.drifter.ui.services.ServicesScreen
import com.mz1312.drifter.ui.settings.SettingsScreen
import com.mz1312.drifter.ui.telemetry.TelemetryScreen
import com.mz1312.drifter.ui.theme.DataFont
import com.mz1312.drifter.ui.theme.DrifterBackground
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
                DrifterBackground {
                    DrifterApp(viewModel)
                }
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
        is Loadable.Success -> when (h.value.health) {
            Healthz.Health.OK -> Severity.GOOD
            Healthz.Health.HW_PENDING -> Severity.WARN
            Healthz.Health.DEGRADED -> Severity.BAD
            Healthz.Health.UNKNOWN -> Severity.NEUTRAL
        }
        is Loadable.Error -> Severity.BAD
        else -> Severity.NEUTRAL
    }

    LaunchedEffect(Unit) {
        vm.messages.collectLatest { snackbar.showSnackbar(it) }
    }

    val title = Destination.bottomBar.firstOrNull { it.route == currentRoute }?.label
        ?: if (currentRoute == Destination.SETTINGS_ROUTE) "Settings" else "Drifter"

    Scaffold(
        containerColor = Color.Transparent,
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            "MZ1312 · DRIFTER",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Text(
                            title,
                            style = MaterialTheme.typography.titleLarge,
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Color.Transparent,
                    scrolledContainerColor = Color.Transparent,
                ),
                actions = {
                    Text(
                        settings.host,
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = DataFont,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.width(10.dp))
                    StatusDot(connSeverity, 8)
                    Spacer(Modifier.width(6.dp))
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
                containerColor = MaterialTheme.colorScheme.surfaceContainerLow.copy(alpha = 0.88f),
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
                        label = { Text(dest.label, style = MaterialTheme.typography.labelSmall) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = MaterialTheme.colorScheme.primary,
                            selectedTextColor = MaterialTheme.colorScheme.primary,
                            indicatorColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.16f),
                            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
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
            enterTransition = { fadeIn(tween(220)) + slideInHorizontally(tween(260)) { it / 18 } },
            exitTransition = { fadeOut(tween(160)) },
            popEnterTransition = { fadeIn(tween(220)) },
            popExitTransition = { fadeOut(tween(160)) },
        ) {
            composable(Destination.Overview.route) { OverviewScreen(vm, navController) }
            composable(Destination.Doctor.route) { DoctorScreen(vm) }
            composable(Destination.Assistant.route) { AssistantScreen(vm) }
            composable(Destination.Services.route) { ServicesScreen(vm) }
            composable(Destination.Arsenal.route) { ArsenalScreen(vm) }
            composable(Destination.Telemetry.route) { TelemetryScreen(vm) }
            composable(Destination.Map.route) { com.mz1312.drifter.ui.map.MapScreen(vm) }
            composable(Destination.SETTINGS_ROUTE) {
                SettingsScreen(vm, onDone = { navController.popBackStack() })
            }
        }
    }
}
