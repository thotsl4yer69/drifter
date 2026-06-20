package com.mz1312.drifter.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// MZ1312 UNCAGED — amber on near-black, the dash's own palette.
val BrandAmber = Color(0xFFF2A900)
val BrandAmberDim = Color(0xFFB07D00)
val SurfaceDark = Color(0xFF14181D)
val BackgroundDark = Color(0xFF0B0E11)

val StatusGreen = Color(0xFF35C46B)
val StatusAmber = Color(0xFFF2A900)
val StatusRed = Color(0xFFE5484D)
val StatusGrey = Color(0xFF6B7280)

private val DarkColors = darkColorScheme(
    primary = BrandAmber,
    onPrimary = Color(0xFF1A1206),
    secondary = Color(0xFF7FB7FF),
    background = BackgroundDark,
    onBackground = Color(0xFFE6E8EB),
    surface = SurfaceDark,
    onSurface = Color(0xFFE6E8EB),
    surfaceVariant = Color(0xFF222831),
    onSurfaceVariant = Color(0xFFAEB4BC),
    error = StatusRed,
)

private val LightColors = lightColorScheme(
    primary = BrandAmberDim,
    secondary = Color(0xFF2563EB),
    error = StatusRed,
)

@Composable
fun DrifterTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        typography = Typography(),
        content = content,
    )
}
