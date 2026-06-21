package com.mz1312.drifter

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.padding
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
        setContent {
            DrifterTheme {
                DrifterApp(viewModel)
            }
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

    androidx.compose.runtime.LaunchedEffect(Unit) {
        vm.messages.collectLatest { snackbar.showSnackbar(it) }
    }

    val title = Destination.bottomBar.firstOrNull { it.route == currentRoute }?.label
        ?: if (currentRoute == Destination.SETTINGS_ROUTE) "Settings" else "Drifter"

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("$title  ·  ${settings.host}") },
                actions = {
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
            NavigationBar {
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
