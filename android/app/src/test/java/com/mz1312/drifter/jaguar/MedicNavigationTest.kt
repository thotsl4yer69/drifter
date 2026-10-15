package com.mz1312.drifter.jaguar

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import com.mz1312.drifter.captureScreen
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class MedicNavigationTest {
    @get:Rule val compose = createAndroidComposeRule<JaguarMedicActivity>()

    private fun tab(label: String) = compose.onNode(hasText(label) and isSelectable()).performClick()

    @Test fun allMedicTabsAndDirectAdapterControlsAreReachable() {
        compose.onNodeWithText("CURRENT CAR").assertIsDisplayed()
        compose.captureScreen("medic-car")
        compose.onNodeWithText("CONNECT OBD").performClick()
        compose.onNodeWithText("OBD LINK").assertIsDisplayed()
        compose.captureScreen("medic-live")
        compose.onNodeWithText("CONNECT WI-FI ELM").assertExists()
        compose.onNodeWithText("USE DRIFTER TELEMETRY").assertExists()
        tab("DIAG")
        compose.onNodeWithText("BUILD TEST ORDER").assertIsDisplayed()
        tab("MANUAL")
        compose.onNodeWithText("WORKSHOP INTELLIGENCE").assertIsDisplayed()
        tab("LOG")
        compose.onNodeWithText("ADD TO CAR FILE").assertIsDisplayed()
        tab("CAR")
        compose.onNodeWithText("CURRENT CAR").assertIsDisplayed()
    }

    @Test fun diagnosticTabAndDraftSurviveRecreation() {
        tab("DIAG")
        compose.onNodeWithText("What is the car doing?").performTextReplacement("Test symptom draft")
        compose.activityRule.scenario.recreate()
        compose.onNode(hasText("DIAG") and isSelectable()).assertIsSelected()
        compose.onNodeWithText("Test symptom draft").assertExists()
    }

    @Test fun invalidWifiPortCannotSilentlyUseTheDefault() {
        tab("LIVE")
        compose.onNodeWithText("Port").performTextReplacement("not a port")
        compose.onNodeWithText("CONNECT WI-FI ELM").assertIsNotEnabled()
        compose.onNodeWithText("Enter a port from 1 to 65535").assertExists()
        compose.onNodeWithText("Port").performTextReplacement("70000")
        compose.onNodeWithText("CONNECT WI-FI ELM").assertIsNotEnabled()
        compose.onNodeWithText("Port").performTextReplacement("35000")
        compose.onNodeWithText("CONNECT WI-FI ELM").assertIsEnabled()
    }

    @Test fun explicitReturnFinishesMedicInsteadOfReplacingDrifter() {
        val activity = compose.activity
        tab("MANUAL")
        compose.onNodeWithContentDescription("Back to DRIFTER").performClick()
        assertTrue(activity.isFinishing)
    }

    @Test fun systemBackExitsMedic() {
        val activity = compose.activity
        compose.runOnIdle { activity.onBackPressedDispatcher.onBackPressed() }
        assertTrue(activity.isFinishing)
    }
}
