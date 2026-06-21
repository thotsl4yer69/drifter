package com.mz1312.drifter.ui.assistant

import android.content.Intent
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mz1312.drifter.data.model.ChatMessage
import com.mz1312.drifter.data.model.ChatRole
import com.mz1312.drifter.ui.DrifterViewModel

/**
 * The capability the fixed Doctor/Services checklists can't offer: a free-form
 * troubleshooting chat that reasons over the node's live state and real logs,
 * so it can help with faults nobody put in a lookup table. Backed by the cloud
 * brain (Claude) with the Pi's on-board LLM as fallback.
 */
@Composable
fun AssistantScreen(vm: DrifterViewModel) {
    val chat by vm.chat.collectAsStateWithLifecycle()
    val busy by vm.assistantBusy.collectAsStateWithLifecycle()
    val settings by vm.settings.collectAsStateWithLifecycle()

    var draft by remember { mutableStateOf("") }
    val listState = rememberLazyListState()

    androidx.compose.runtime.LaunchedEffect(chat.size, busy) {
        val count = chat.size + if (busy) 1 else 0
        if (count > 0) listState.animateScrollToItem(count - 1)
    }

    Column(Modifier.fillMaxSize()) {
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (chat.isEmpty()) {
                EmptyState(
                    hasCloudBrain = settings.hasCloudBrain,
                    enabled = !busy,
                    onAsk = { vm.askAssistant(it) },
                )
            }
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 12.dp),
            ) {
                items(chat) { msg -> ChatBubble(msg) }
                if (busy) {
                    item { ThinkingBubble() }
                }
            }
        }

        if (chat.isNotEmpty()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                horizontalArrangement = Arrangement.End,
            ) {
                TextButton(onClick = { vm.clearChat() }, enabled = !busy) {
                    Text("Clear conversation")
                }
            }
        }

        Composer(
            value = draft,
            onValueChange = { draft = it },
            enabled = !busy,
            onSend = {
                vm.askAssistant(draft)
                draft = ""
            },
        )
    }
}

@Composable
private fun EmptyState(hasCloudBrain: Boolean, enabled: Boolean, onAsk: (String) -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            "Ask anything about the node",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )
        Spacer(Modifier.padding(6.dp))
        Text(
            "I read the live health, port probes and real service logs, then reason " +
                "about whatever's wrong — including problems no checklist covers.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.padding(10.dp))
        listOf(
            "Why can't I connect to the dashboard?",
            "What's wrong right now, and how do I fix it?",
            "drifter-canbridge is down — is that a real fault?",
            "Walk me through getting telemetry flowing again.",
        ).forEach { example ->
            Text(
                "“$example”",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier
                    .padding(vertical = 3.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(enabled = enabled) { onAsk(example) }
                    .padding(vertical = 3.dp, horizontal = 8.dp),
                textAlign = TextAlign.Center,
            )
        }
        if (!hasCloudBrain) {
            Spacer(Modifier.padding(10.dp))
            Text(
                "No Claude API key set — answers come from the Pi's on-board LLM, " +
                    "which is unavailable when the Pi is down. Add a key in Settings " +
                    "for a cloud brain that always works.",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
        }
    }
}

@Composable
private fun ChatBubble(msg: ChatMessage) {
    val mine = msg.role == ChatRole.USER
    val isProblem = msg.via == "error" || msg.via == "refused"
    val container = when {
        mine -> MaterialTheme.colorScheme.primary
        isProblem -> MaterialTheme.colorScheme.errorContainer
        else -> MaterialTheme.colorScheme.surfaceVariant
    }
    val onContainer = when {
        mine -> MaterialTheme.colorScheme.onPrimary
        isProblem -> MaterialTheme.colorScheme.onErrorContainer
        else -> MaterialTheme.colorScheme.onSurface
    }
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start,
    ) {
        Card(
            colors = CardDefaults.cardColors(containerColor = container),
            shape = RoundedCornerShape(
                topStart = 14.dp,
                topEnd = 14.dp,
                bottomStart = if (mine) 14.dp else 2.dp,
                bottomEnd = if (mine) 2.dp else 14.dp,
            ),
            modifier = Modifier.widthIn(max = 320.dp),
        ) {
            Column(Modifier.padding(12.dp)) {
                Text(msg.text, style = MaterialTheme.typography.bodyMedium, color = onContainer)
                msg.via?.takeIf { !mine }?.let { via ->
                    Spacer(Modifier.padding(2.dp))
                    Text(
                        viaLabel(via),
                        style = MaterialTheme.typography.labelSmall,
                        color = onContainer.copy(alpha = 0.7f),
                    )
                }
            }
        }
    }
}

private fun viaLabel(via: String): String = when (via) {
    "error" -> "couldn't answer"
    "refused" -> "declined"
    else -> via
}

@Composable
private fun ThinkingBubble() {
    Row(horizontalArrangement = Arrangement.Start) {
        Card(
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            shape = RoundedCornerShape(14.dp),
        ) {
            Row(
                Modifier.padding(14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                CircularProgressIndicator(
                    modifier = Modifier.padding(end = 10.dp).size(16.dp),
                    strokeWidth = 2.dp,
                )
                Text(
                    "Reading the node and thinking…",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun Composer(
    value: String,
    onValueChange: (String) -> Unit,
    enabled: Boolean,
    onSend: () -> Unit,
) {
    val context = LocalContext.current
    val speechAvailable = remember { SpeechRecognizer.isRecognitionAvailable(context) }
    // System speech recognizer — hands-free input while driving. The intent-based
    // recognizer shows its own UI and handles the mic permission itself, so we
    // need no RECORD_AUDIO grant. The transcript fills the box for a quick review.
    val voiceLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
            ?.firstOrNull()?.takeIf { it.isNotBlank() }
            ?.let { onValueChange(it) }
    }

    Row(
        Modifier.fillMaxWidth().padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            modifier = Modifier.weight(1f),
            placeholder = { Text("Describe the problem…") },
            enabled = enabled,
            maxLines = 4,
        )
        Spacer(Modifier.padding(4.dp))
        if (speechAvailable) {
            IconButton(
                enabled = enabled,
                onClick = {
                    val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                        putExtra(
                            RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                            RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
                        )
                        putExtra(RecognizerIntent.EXTRA_PROMPT, "Ask Drifter…")
                    }
                    runCatching { voiceLauncher.launch(intent) }
                },
            ) {
                Icon(Icons.Filled.Mic, contentDescription = "Voice input")
            }
        }
        IconButton(
            onClick = onSend,
            enabled = enabled && value.isNotBlank(),
        ) {
            Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send")
        }
    }
}
