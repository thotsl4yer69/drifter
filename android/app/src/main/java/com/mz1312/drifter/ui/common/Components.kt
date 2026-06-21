package com.mz1312.drifter.ui.common

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
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.mz1312.drifter.ui.theme.StatusGreen
import com.mz1312.drifter.ui.theme.StatusGrey
import com.mz1312.drifter.ui.theme.StatusRed
import com.mz1312.drifter.ui.theme.glow

enum class Severity { GOOD, WARN, BAD, NEUTRAL }

fun Severity.color(): Color = when (this) {
    Severity.GOOD -> StatusGreen
    Severity.WARN -> com.mz1312.drifter.ui.theme.StatusAmber
    Severity.BAD -> StatusRed
    Severity.NEUTRAL -> StatusGrey
}

@Composable
fun StatusDot(severity: Severity, size: Int = 12) {
    val c = severity.color()
    Box(
        Modifier.size((size + 9).dp),
        contentAlignment = Alignment.Center,
    ) {
        // Soft halo so live/critical states read as glowing instrument lamps.
        Box(Modifier.size((size + 9).dp).clip(CircleShape).background(c.glow(0.20f)))
        Box(Modifier.size((size + 4).dp).clip(CircleShape).background(c.glow(0.30f)))
        Box(Modifier.size(size.dp).clip(CircleShape).background(c))
    }
}

@Composable
fun StatusPill(text: String, severity: Severity, modifier: Modifier = Modifier) {
    Row(
        modifier
            .clip(RoundedCornerShape(50))
            .background(severity.color().copy(alpha = 0.14f))
            .border(1.dp, severity.color().copy(alpha = 0.40f), RoundedCornerShape(50))
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(severity.color()))
        Spacer(Modifier.width(8.dp))
        Text(
            text,
            color = severity.color(),
            style = MaterialTheme.typography.labelMedium,
        )
    }
}

@Composable
fun SectionCard(
    title: String,
    modifier: Modifier = Modifier,
    trailing: @Composable (() -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    Card(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(18.dp))
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(18.dp)),
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    ) {
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
                            .size(width = 3.dp, height = 16.dp)
                            .clip(RoundedCornerShape(2.dp))
                            .background(MaterialTheme.colorScheme.primary),
                    )
                    Spacer(Modifier.width(10.dp))
                    Text(
                        title,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                }
                trailing?.invoke()
            }
            Spacer(Modifier.size(12.dp))
            content()
        }
    }
}

@Composable
fun InfoRow(label: String, value: String, valueColor: Color? = null) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
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
        fontFamily = FontFamily.Monospace,
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
