package com.mz1312.drifter.ui.theme

import android.app.Activity
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

/**
 * The full MZ1312 instrument colour scheme. Every Material role is set
 * deliberately (not left to defaults) so cards, chips, buttons and the nav bar
 * all read as one cohesive glass cockpit. The app is a night-driving tool, so
 * it is dark-first and does not follow the system light theme.
 */
private val DrifterColors = darkColorScheme(
    primary = BrandAmber,
    onPrimary = Color(0xFF1A1206),
    primaryContainer = Color(0xFF3A2A02),
    onPrimaryContainer = Color(0xFFFFD98A),

    secondary = BrandCyan,
    onSecondary = Color(0xFF00201F),
    secondaryContainer = Color(0xFF033A40),
    onSecondaryContainer = Color(0xFFA6F0F7),

    tertiary = StatusGreen,
    onTertiary = Color(0xFF00210F),
    tertiaryContainer = Color(0xFF023A20),
    onTertiaryContainer = Color(0xFFA7F3C8),

    background = BackgroundDark,
    onBackground = OnDark,
    surface = SurfaceDark,
    onSurface = OnDark,
    surfaceVariant = SurfaceVariantDark,
    onSurfaceVariant = OnDarkVariant,
    surfaceContainerLowest = BackgroundDark,
    surfaceContainerLow = SurfaceContainerLow,
    surfaceContainer = SurfaceContainer,
    surfaceContainerHigh = SurfaceContainerHigh,
    surfaceContainerHighest = Color(0xFF222C36),

    outline = OutlineDark,
    outlineVariant = OutlineVariantDark,

    error = StatusRed,
    onError = Color(0xFF2A0608),
    errorContainer = Color(0xFF3A0E10),
    onErrorContainer = Color(0xFFFFB3B5),
)

@Composable
fun DrifterTheme(
    content: @Composable () -> Unit,
) {
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = Color.Transparent.toArgb()
            window.navigationBarColor = Color.Transparent.toArgb()
            val controller = WindowCompat.getInsetsController(window, view)
            controller.isAppearanceLightStatusBars = false
            controller.isAppearanceLightNavigationBars = false
        }
    }
    MaterialTheme(
        colorScheme = DrifterColors,
        typography = DrifterTypography,
        content = content,
    )
}
