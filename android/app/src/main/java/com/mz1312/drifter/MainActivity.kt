package com.mz1312.drifter

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import com.mz1312.drifter.jaguar.JaguarMedicApp
import com.mz1312.drifter.jaguar.JaguarMedicViewModel
import com.mz1312.drifter.ui.theme.DrifterBackground
import com.mz1312.drifter.ui.theme.DrifterTheme

class MainActivity : ComponentActivity() {
    private val viewModel: JaguarMedicViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        maybeRequestPermissions()
        setContent {
            DrifterTheme {
                DrifterBackground { JaguarMedicApp(viewModel) }
            }
        }
    }

    private fun maybeRequestPermissions() {
        val wanted = buildList {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) add(Manifest.permission.BLUETOOTH_CONNECT)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
        }.filter { checkSelfPermission(it) != android.content.pm.PackageManager.PERMISSION_GRANTED }
        if (wanted.isNotEmpty()) requestPermissions(wanted.toTypedArray(), 1312)
    }
}
