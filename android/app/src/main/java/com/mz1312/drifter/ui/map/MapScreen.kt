package com.mz1312.drifter.ui.map

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.android.gms.maps.model.CameraPosition
import com.google.android.gms.maps.model.LatLng
import com.google.android.gms.maps.model.MapStyleOptions
import com.google.maps.android.compose.Circle
import com.google.maps.android.compose.GoogleMap
import com.google.maps.android.compose.MapProperties
import com.google.maps.android.compose.MapType
import com.google.maps.android.compose.MapUiSettings
import com.google.maps.android.compose.Polyline
import com.google.maps.android.compose.rememberCameraPositionState
import com.mz1312.drifter.R
import com.mz1312.drifter.ui.DrifterViewModel

// Node default position (Bendigo VIC) — where the camera sits before a fix.
private val DEFAULT_POSITION = LatLng(-36.7570, 144.2794)

/**
 * Live GPS / drive-path map. Renders the current fix off `drifter/gps/fix` as a
 * pulse and the route so far as a polyline, dark-styled to match the cockpit.
 * Camera follows the vehicle until you pan; the recenter button re-locks it.
 */
@Composable
fun MapScreen(vm: DrifterViewModel) {
    val gps by vm.gps.collectAsStateWithLifecycle()
    val context = LocalContext.current

    // Opening the map needs the telemetry socket (GPS rides the same stream).
    DisposableEffect(Unit) {
        vm.connectTelemetry()
        onDispose { vm.disconnectTelemetry() }
    }

    var satellite by rememberSaveable { mutableStateOf(false) }
    var follow by rememberSaveable { mutableStateOf(true) }
    val camera = rememberCameraPositionState {
        position = CameraPosition.fromLatLngZoom(DEFAULT_POSITION, 6f)
    }
    val darkStyle = remember { MapStyleOptions.loadRawResourceStyle(context, R.raw.map_style_dark) }

    // Follow the live fix while in follow mode.
    LaunchedEffect(gps.current, follow) {
        val c = gps.current
        if (follow && c != null) {
            camera.position = CameraPosition.fromLatLngZoom(
                LatLng(c.lat, c.lng),
                maxOf(camera.position.zoom, 15f),
            )
        }
    }
    // A manual pan drops follow so the camera stops fighting the user.
    LaunchedEffect(camera.isMoving) {
        if (camera.isMoving &&
            camera.cameraMoveStartedReason ==
            com.google.maps.android.compose.CameraMoveStartedReason.GESTURE
        ) {
            follow = false
        }
    }

    val pathColor = MaterialTheme.colorScheme.primary
    val fixColor = MaterialTheme.colorScheme.secondary

    Box(Modifier.fillMaxSize()) {
        GoogleMap(
            modifier = Modifier.fillMaxSize(),
            cameraPositionState = camera,
            properties = MapProperties(
                mapType = if (satellite) MapType.SATELLITE else MapType.NORMAL,
                mapStyleOptions = if (satellite) null else darkStyle,
            ),
            uiSettings = MapUiSettings(zoomControlsEnabled = false, compassEnabled = true),
        ) {
            gps.current?.let { c ->
                Circle(
                    center = LatLng(c.lat, c.lng),
                    radius = 16.0,
                    fillColor = fixColor.copy(alpha = 0.45f),
                    strokeColor = fixColor,
                    strokeWidth = 4f,
                )
            }
            if (gps.path.size >= 2) {
                Polyline(points = gps.path.map { LatLng(it.lat, it.lng) }, color = pathColor, width = 9f)
            }
        }

        Column(
            Modifier.align(Alignment.TopEnd).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            FilledTonalIconButton(onClick = { satellite = !satellite }) {
                Icon(Icons.Filled.Layers, contentDescription = "Toggle satellite")
            }
            FilledTonalIconButton(onClick = {
                follow = true
                gps.current?.let {
                    camera.position = CameraPosition.fromLatLngZoom(LatLng(it.lat, it.lng), 16f)
                }
            }) {
                Icon(Icons.Filled.MyLocation, contentDescription = "Recenter on vehicle")
            }
        }

        if (gps.current == null) {
            Surface(
                Modifier.align(Alignment.BottomCenter).padding(16.dp),
                color = MaterialTheme.colorScheme.surfaceContainerHigh,
                shape = MaterialTheme.shapes.medium,
            ) {
                Text(
                    "No GPS fix yet — waiting on drifter/gps/fix. Needs the USB GPS dongle " +
                        "(drifter-gps) and a clear sky view.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(12.dp),
                )
            }
        }
    }
}
