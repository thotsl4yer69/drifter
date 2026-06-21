package com.mz1312.drifter.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * MZ1312 "graphite glass" palette — a night-driving instrument, not a generic
 * app. Deep blue-graphite blacks, an amber signal primary (the dash's own
 * colour), an electric-cyan telemetry accent, and saturated signal colours for
 * status. Surfaces step up in tonal tiers so cards read as layered glass.
 *
 * Names exported from this file are part of the design system's public surface
 * (Components.kt, Theme.kt, screens import them) — keep them stable.
 */

// ── Brand ───────────────────────────────────────────────────────────────────
val BrandAmber = Color(0xFFFFB020)       // primary signal
val BrandAmberBright = Color(0xFFFFC247)
val BrandAmberDim = Color(0xFFB07D00)
val BrandCyan = Color(0xFF54D9E6)        // telemetry / live-data accent

// ── Graphite surface tiers (low → high elevation) ───────────────────────────
val BackgroundDark = Color(0xFF06080B)
val SurfaceDark = Color(0xFF0E1217)
val SurfaceContainerLow = Color(0xFF11161C)
val SurfaceContainer = Color(0xFF151C23)
val SurfaceContainerHigh = Color(0xFF1C242D)
val SurfaceVariantDark = Color(0xFF212B34)
val OutlineDark = Color(0xFF36424C)
val OutlineVariantDark = Color(0xFF232C34)

val OnDark = Color(0xFFE8EDF2)
val OnDarkVariant = Color(0xFF9BA7B2)

// ── Status / signal ──────────────────────────────────────────────────────────
val StatusGreen = Color(0xFF2FD27A)
val StatusAmber = Color(0xFFFFB020)
val StatusRed = Color(0xFFFF5A5F)
val StatusGrey = Color(0xFF6B7785)
val StatusCyan = Color(0xFF54D9E6)

/** Translucent halo colours for glowing status indicators. */
fun Color.glow(alpha: Float = 0.28f): Color = copy(alpha = alpha)
