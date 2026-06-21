package com.mz1312.drifter.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.googlefonts.Font
import androidx.compose.ui.text.googlefonts.GoogleFont
import androidx.compose.ui.unit.sp
import com.mz1312.drifter.R

/**
 * Instrument-grade type scale on a real typeface. Downloadable Google Fonts:
 * **Sora** for display/headings (geometric, confident), **Inter** for body and
 * labels (highly legible at a glance in a car mount), **JetBrains Mono** for
 * machine data so columns of readings align like a gauge cluster.
 *
 * All three resolve through the system font provider and fall back to the
 * platform default if Play Services / the provider is unavailable — the app
 * always renders, just without the custom face.
 */
private val provider = GoogleFont.Provider(
    providerAuthority = "com.google.android.gms.fonts",
    providerPackage = "com.google.android.gms",
    certificates = R.array.com_google_android_gms_fonts_certs,
)

// Compose loads provider fonts asynchronously and falls back to the platform
// default automatically if the provider/Play Services is unavailable.
private fun family(name: String, vararg weights: FontWeight): FontFamily {
    val gf = GoogleFont(name)
    return FontFamily(weights.map { Font(googleFont = gf, fontProvider = provider, weight = it) })
}

val DisplayFont: FontFamily = family(
    "Sora",
    FontWeight.Medium, FontWeight.SemiBold, FontWeight.Bold,
)

val BodyFont: FontFamily = family(
    "Inter",
    FontWeight.Normal, FontWeight.Medium, FontWeight.SemiBold,
)

/** Monospace family for telemetry / machine readouts. */
val DataFont: FontFamily = family(
    "JetBrains Mono",
    FontWeight.Normal, FontWeight.Medium, FontWeight.Bold,
)

val DrifterTypography = Typography(
    displayLarge = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.Bold,
        fontSize = 52.sp,
        lineHeight = 56.sp,
        letterSpacing = (-1).sp,
    ),
    displayMedium = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.Bold,
        fontSize = 40.sp,
        lineHeight = 44.sp,
        letterSpacing = (-0.5).sp,
    ),
    headlineMedium = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.SemiBold,
        fontSize = 26.sp,
        lineHeight = 31.sp,
        letterSpacing = (-0.3).sp,
    ),
    titleLarge = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.SemiBold,
        fontSize = 21.sp,
        lineHeight = 26.sp,
        letterSpacing = (-0.2).sp,
    ),
    titleMedium = TextStyle(
        fontFamily = DisplayFont,
        fontWeight = FontWeight.SemiBold,
        fontSize = 17.sp,
        lineHeight = 22.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = BodyFont,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 23.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = BodyFont,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = BodyFont,
        fontWeight = FontWeight.Normal,
        fontSize = 12.5.sp,
        lineHeight = 17.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = BodyFont,
        fontWeight = FontWeight.SemiBold,
        fontSize = 14.sp,
        lineHeight = 18.sp,
        letterSpacing = 0.2.sp,
    ),
    labelMedium = TextStyle(
        // "tactical tag" caps — section eyebrows / status chips.
        fontFamily = BodyFont,
        fontWeight = FontWeight.Bold,
        fontSize = 11.sp,
        lineHeight = 14.sp,
        letterSpacing = 1.4.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = BodyFont,
        fontWeight = FontWeight.Medium,
        fontSize = 11.sp,
        lineHeight = 15.sp,
        letterSpacing = 0.4.sp,
    ),
)
