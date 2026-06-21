package com.mz1312.drifter.ui.telemetry

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.mz1312.drifter.ui.theme.StatusAmber
import com.mz1312.drifter.ui.theme.StatusGreen
import com.mz1312.drifter.ui.theme.StatusGrey
import com.mz1312.drifter.ui.theme.StatusRed
import kotlin.math.roundToInt

/** Range + warn/danger bands for one telemetry signal, keyed by short topic. */
data class GaugeSpec(
    val label: String,
    val unit: String,
    val min: Double,
    val max: Double,
    val warn: Double? = null,     // high-side amber threshold
    val danger: Double? = null,   // high-side red threshold
    val lowWarn: Double? = null,  // low-side amber threshold (fuel, voltage)
    val decimals: Int = 0,
)

/** The AJ-V6's meaningful vitals. Bands are sane defaults for a 2.5 X-Type. */
val SIGNAL_SPECS: Map<String, GaugeSpec> = mapOf(
    "engine/rpm" to GaugeSpec("RPM", "rpm", 0.0, 7000.0, warn = 6000.0, danger = 6500.0),
    "vehicle/speed" to GaugeSpec("Speed", "km/h", 0.0, 220.0),
    "engine/coolant" to GaugeSpec("Coolant", "°C", 0.0, 130.0, warn = 105.0, danger = 115.0),
    "power/voltage" to GaugeSpec("Battery", "V", 8.0, 16.0, lowWarn = 11.8, decimals = 1),
    "vehicle/fuel_lvl" to GaugeSpec("Fuel", "%", 0.0, 100.0, lowWarn = 15.0),
    "engine/load" to GaugeSpec("Load", "%", 0.0, 100.0, warn = 90.0),
    "engine/throttle" to GaugeSpec("Throttle", "%", 0.0, 100.0),
    "engine/iat" to GaugeSpec("Intake", "°C", 0.0, 80.0, warn = 60.0),
)

private fun bandColor(spec: GaugeSpec, value: Double?): Color = when {
    value == null -> StatusGrey
    spec.danger != null && value >= spec.danger -> StatusRed
    spec.warn != null && value >= spec.warn -> StatusAmber
    spec.lowWarn != null && value <= spec.lowWarn -> StatusAmber
    else -> StatusGreen
}

private fun format(value: Double, decimals: Int): String =
    if (decimals <= 0) value.roundToInt().toString() else "%.${decimals}f".format(value)

/** A 270° arc gauge — track + a band-coloured value sweep, with the reading
 *  centred. Drawn on a Canvas so it stays crisp at any size. */
@Composable
fun Gauge(spec: GaugeSpec, value: Double?, modifier: Modifier = Modifier) {
    val color = bandColor(spec, value)
    val frac = if (value == null) {
        0f
    } else {
        (((value - spec.min) / (spec.max - spec.min)).coerceIn(0.0, 1.0)).toFloat()
    }
    val track = MaterialTheme.colorScheme.surfaceContainerHighest

    Box(modifier.aspectRatio(1f), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxWidth().aspectRatio(1f).padding(8.dp)) {
            val sw = 12.dp.toPx()
            val inset = sw / 2f
            val arcSize = Size(size.width - sw, size.height - sw)
            val topLeft = Offset(inset, inset)
            val stroke = Stroke(width = sw, cap = StrokeCap.Round)
            val start = 135f
            val total = 270f
            drawArc(track, start, total, false, topLeft, arcSize, style = stroke)
            if (value != null) {
                drawArc(color, start, total * frac, false, topLeft, arcSize, style = stroke)
            }
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                if (value == null) "--" else format(value, spec.decimals),
                style = MaterialTheme.typography.titleLarge,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold,
                color = color,
            )
            Text(
                spec.unit,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                spec.label.uppercase(),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** A gauge with a rolling trend sparkline beneath it. */
@Composable
fun GaugeTile(spec: GaugeSpec, value: Double?, history: List<Float>, modifier: Modifier = Modifier) {
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Gauge(spec, value, Modifier.fillMaxWidth())
        Sparkline(history, bandColor(spec, value))
    }
}

/** A thin rolling line chart of recent values, min/max auto-scaled. */
@Composable
fun Sparkline(values: List<Float>, color: Color, modifier: Modifier = Modifier) {
    if (values.size < 2) {
        Spacer(modifier.fillMaxWidth().height(SPARK_HEIGHT))
        return
    }
    Canvas(modifier.fillMaxWidth().height(SPARK_HEIGHT).padding(horizontal = 8.dp)) {
        val min = values.min()
        val max = values.max()
        val range = (max - min).takeIf { it > 0f } ?: 1f
        val dx = if (values.size > 1) size.width / (values.size - 1) else size.width
        val path = Path()
        values.forEachIndexed { i, v ->
            val x = i * dx
            val y = size.height - ((v - min) / range) * size.height
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        drawPath(path, color, style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round))
    }
}

private val SPARK_HEIGHT = 22.dp
