package com.mz1312.drifter.ui.common

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.mz1312.drifter.ui.theme.DataFont
import com.mz1312.drifter.ui.theme.StatusGreen
import com.mz1312.drifter.ui.theme.StatusGrey
import com.mz1312.drifter.ui.theme.StatusRed
import com.mz1312.drifter.ui.theme.glassEdge
import com.mz1312.drifter.ui.theme.glassFill
import com.mz1312.drifter.ui.theme.glow

enum class Severity { GOOD, WARN, BAD, NEUTRAL }

fun Severity.color(): Color = when (this) {
    Severity.GOOD -> StatusGreen
    Severity.WARN -> com.mz1312.drifter.ui.theme.StatusAmber
    Severity.BAD -> StatusRed
    Severity.NEUTRAL -> StatusGrey
}

private val CardShape = RoundedCornerShape(20.dp)

/** Pulsing glass status lamp. Live/critical states (GOOD, BAD) breathe; WARN and
 *  NEUTRAL sit steady so the eye is only drawn to things that move. */
@Composable
fun StatusDot(severity: Severity, size: Int = 12) {
    val c = severity.color()
    val animate = severity == Severity.GOOD || severity == Severity.BAD
    val pulse = if (animate) {
        rememberInfiniteTransition(label = "dot").animateFloat(
            initialValue = 0.45f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                animation = tween(1100),
                repeatMode = RepeatMode.Reverse,
            ),
            label = "dotPulse",
        ).value
    } else {
        0.85f
    }
    Box(Modifier.size((size + 10).dp), contentAlignment = Alignment.Center) {
        Box(
            Modifier
                .size((size + 10).dp)
                .scale(if (animate) 0.7f + pulse * 0.4f else 1f)
                .clip(CircleShape)
                .background(c.glow(0.10f + pulse * 0.18f)),
        )
        Box(Modifier.size((size + 4).dp).clip(CircleShape).background(c.glow(0.28f)))
        Box(Modifier.size(size.dp).clip(CircleShape).background(c))
    }
}

@Composable
fun StatusPill(text: String, severity: Severity, modifier: Modifier = Modifier) {
    val c = severity.color()
    Row(
        modifier
            .clip(RoundedCornerShape(50))
            .background(c.copy(alpha = 0.13f))
            .border(1.dp, c.copy(alpha = 0.42f), RoundedCornerShape(50))
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(c))
        Spacer(Modifier.width(8.dp))
        Text(text, color = c, style = MaterialTheme.typography.labelMedium)
    }
}

/** Small caps "eyebrow" tag for section headers / groupings. */
@Composable
fun Eyebrow(text: String, color: Color? = null, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(),
        modifier = modifier,
        style = MaterialTheme.typography.labelMedium,
        color = color ?: MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** Reusable frosted-glass surface: gradient fill, hairline top-lit edge, soft
 *  drop shadow. The base of every card in the app. */
@Composable
fun GlassCard(
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Box(
        modifier
            .fillMaxWidth()
            .shadow(14.dp, CardShape, ambientColor = Color.Black, spotColor = Color.Black)
            .clip(CardShape)
            .background(glassFill())
            .border(1.dp, glassEdge(), CardShape),
    ) {
        content()
    }
}

@Composable
fun SectionCard(
    title: String,
    modifier: Modifier = Modifier,
    trailing: @Composable (() -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    GlassCard(modifier) {
        Column(Modifier.padding(16.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    // Amber accent tick — the "panel header" instrument cue.
                    Box(
                        Modifier
                            .size(width = 3.dp, height = 17.dp)
                            .clip(RoundedCornerShape(2.dp))
                            .background(MaterialTheme.colorScheme.primary),
                    )
                    Spacer(Modifier.width(10.dp))
                    Text(title, style = MaterialTheme.typography.titleMedium)
                }
                trailing?.invoke()
            }
            Spacer(Modifier.size(14.dp))
            content()
        }
    }
}

@Composable
fun InfoRow(label: String, value: String, valueColor: Color? = null) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
            color = valueColor ?: MaterialTheme.colorScheme.onSurface,
        )
    }
}

@Composable
fun Mono(text: String, modifier: Modifier = Modifier, color: Color? = null) {
    Text(
        text,
        modifier = modifier,
        fontFamily = DataFont,
        style = MaterialTheme.typography.bodySmall,
        color = color ?: MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
fun BulletList(items: List<String>) {
    Column {
        items.forEach { line ->
            Row(Modifier.padding(vertical = 2.dp)) {
                Text("•  ", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
                Text(line, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
