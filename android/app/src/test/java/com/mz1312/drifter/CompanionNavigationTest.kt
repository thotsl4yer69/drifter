package com.mz1312.drifter

import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import com.mz1312.drifter.data.store.AppSettings
import kotlinx.coroutines.runBlocking
import org.junit.rules.ExternalResource
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import com.mz1312.drifter.jaguar.JaguarMedicActivity
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class CompanionNavigationTest {
    @get:Rule(order = 0) val localSettings = object : ExternalResource() {
        override fun before() {
            val app = ApplicationProvider.getApplicationContext<DrifterApp>()
            runBlocking {
                app.container.settingsStore.update(AppSettings(host = "127.0.0.1", httpPort = 1, wsPort = 1, autoRefresh = false))
            }
        }
    }
    @get:Rule(order = 1) val compose = createAndroidComposeRule<MainActivity>()

    @Test fun launcherKeepsDrifterIdentityAndOpensMedicExplicitly() {
        val activity = compose.activity
        val launch = activity.packageManager.getLaunchIntentForPackage(activity.packageName)!!
        assertEquals(MainActivity::class.java.name, launch.component!!.className)
        assertEquals("Drifter Diagnostics", activity.getString(R.string.app_name))
        compose.onNodeWithText("MZ1312 · DRIFTER").assertIsDisplayed()
        compose.captureScreen("drifter-overview")
        compose.onNodeWithText("Open X-Type Medic").performClick()
        val intent = shadowOf(activity).nextStartedActivity
        assertEquals(JaguarMedicActivity::class.java.name, intent.component!!.className)
        assertEquals(0, intent.flags and Intent.FLAG_ACTIVITY_NEW_TASK)
        assertFalse(activity.isFinishing)
    }

    @Test fun everyOriginalDestinationAndSettingsRemainReachable() {
        val screens = listOf(
            "Doctor" to "Connection Doctor",
            "Assistant" to "Describe the problem…",
            "Services" to "Waiting for /healthz…",
            "Arsenal" to "Carsenal / Kali arsenal",
            "Telemetry" to "Live telemetry",
            "Map" to "No GPS fix yet",
        )
        screens.forEach { (tab, content) ->
            compose.onNode(hasText(tab) and isSelectable()).performClick()
            compose.onNode(hasText(tab) and isSelectable()).assertIsSelected()
            compose.onNodeWithText(content, substring = true).assertExists()
        }
        compose.onNodeWithContentDescription("Settings").performClick()
        compose.onNodeWithText("Node connection").assertIsDisplayed()
        compose.runOnIdle { compose.activity.onBackPressedDispatcher.onBackPressed() }
        compose.onNode(hasText("Map") and isSelectable()).assertIsSelected()
        compose.onNode(hasText("Overview") and isSelectable()).performClick()
        compose.onNodeWithText("Open X-Type Medic").assertIsDisplayed()
    }

    @Test fun telemetryObservesConnectionSettingsChanges() {
        compose.onNode(hasText("Telemetry") and isSelectable()).performClick()
        val app = ApplicationProvider.getApplicationContext<DrifterApp>()
        runBlocking {
            app.container.settingsStore.update(AppSettings(host = "127.0.0.1", httpPort = 1, wsPort = 9876, autoRefresh = false))
        }
        compose.waitUntil(3000) {
            compose.onAllNodesWithText("ws://127.0.0.1:9876").fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithText("ws://127.0.0.1:9876").assertIsDisplayed()
    }

    @Test fun drifterRetainsSelectedRouteAcrossRecreation() {
        compose.onNode(hasText("Assistant") and isSelectable()).performClick()
        compose.activityRule.scenario.recreate()
        compose.onNode(hasText("Assistant") and isSelectable()).assertIsSelected()
        compose.onNodeWithText("Describe the problem…").assertExists()
    }
}
