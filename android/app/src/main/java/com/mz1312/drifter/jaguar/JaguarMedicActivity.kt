package com.mz1312.drifter.jaguar

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import com.mz1312.drifter.ui.theme.DrifterBackground
import com.mz1312.drifter.ui.theme.DrifterTheme

/** A diagnostic workspace in the existing task, leaving DRIFTER's back stack intact. */
class JaguarMedicActivity : ComponentActivity() {
    private val viewModel: JaguarMedicViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            DrifterTheme {
                DrifterBackground {
                    JaguarMedicApp(viewModel, onBackToDrifter = { finish() })
                }
            }
        }
    }
}
