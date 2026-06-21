package com.mz1312.drifter.data.store

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** User-configurable connection settings, persisted via DataStore. */
data class AppSettings(
    val host: String = DEFAULT_HOST,
    val httpPort: Int = DEFAULT_HTTP_PORT,
    val wsPort: Int = DEFAULT_WS_PORT,
    val pollSeconds: Int = DEFAULT_POLL_SECONDS,
    val autoRefresh: Boolean = true,
    /** Watch the node in the background and notify on degrade/offline/recover. */
    val backgroundAlerts: Boolean = false,
    /** Anthropic API key for the cloud troubleshooting brain (blank = Pi-only). */
    val claudeApiKey: String = "",
    /** Model id for the cloud brain. Defaults to the most capable Claude. */
    val claudeModel: String = DEFAULT_CLAUDE_MODEL,
) {
    val httpBaseUrl: String get() = "http://$host:$httpPort"
    val telemetryWsUrl: String get() = "ws://$host:$wsPort"

    /** True when the cloud brain is configured; otherwise the assistant falls
     *  back to the Pi's own on-board LLM (which is unreachable when the Pi is). */
    val hasCloudBrain: Boolean get() = claudeApiKey.isNotBlank()

    companion object {
        const val DEFAULT_HOST = "10.42.0.1"
        const val DEFAULT_HTTP_PORT = 8080
        const val DEFAULT_WS_PORT = 8081
        const val DEFAULT_POLL_SECONDS = 5
        // Per the Anthropic model catalogue, the most capable Claude. The
        // assistant reasons over live diagnostics, so default to the strongest
        // model; the user can switch to a faster/cheaper one in Settings.
        const val DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"
    }
}

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "drifter_settings")

class SettingsStore(private val context: Context) {

    // The Claude API key lives in the Keystore-backed encrypted store, not in
    // (plaintext) DataStore. Everything else stays in DataStore.
    private val secure = SecureStore(context)

    private object Keys {
        val HOST = stringPreferencesKey("host")
        val HTTP_PORT = intPreferencesKey("http_port")
        val WS_PORT = intPreferencesKey("ws_port")
        val POLL = intPreferencesKey("poll_seconds")
        val AUTO = booleanPreferencesKey("auto_refresh")
        val ALERTS = booleanPreferencesKey("background_alerts")
        val CLAUDE_KEY = stringPreferencesKey("claude_api_key")
        val CLAUDE_MODEL = stringPreferencesKey("claude_model")
    }

    val settings: Flow<AppSettings> = context.dataStore.data.map { p ->
        AppSettings(
            host = p[Keys.HOST] ?: AppSettings.DEFAULT_HOST,
            httpPort = p[Keys.HTTP_PORT] ?: AppSettings.DEFAULT_HTTP_PORT,
            wsPort = p[Keys.WS_PORT] ?: AppSettings.DEFAULT_WS_PORT,
            pollSeconds = (p[Keys.POLL] ?: AppSettings.DEFAULT_POLL_SECONDS).coerceIn(2, 60),
            autoRefresh = p[Keys.AUTO] ?: true,
            backgroundAlerts = p[Keys.ALERTS] ?: false,
            // Prefer the encrypted store; fall back to any legacy plaintext key
            // from an older build (migrated to the encrypted store on next save).
            claudeApiKey = secure.apiKey.ifBlank { p[Keys.CLAUDE_KEY].orEmpty() },
            claudeModel = p[Keys.CLAUDE_MODEL]?.ifBlank { null } ?: AppSettings.DEFAULT_CLAUDE_MODEL,
        )
    }

    suspend fun update(settings: AppSettings) {
        secure.apiKey = settings.claudeApiKey.trim()
        context.dataStore.edit { p ->
            p[Keys.HOST] = settings.host.trim()
            p[Keys.HTTP_PORT] = settings.httpPort
            p[Keys.WS_PORT] = settings.wsPort
            p[Keys.POLL] = settings.pollSeconds.coerceIn(2, 60)
            p[Keys.AUTO] = settings.autoRefresh
            p[Keys.ALERTS] = settings.backgroundAlerts
            p[Keys.CLAUDE_MODEL] = settings.claudeModel.trim().ifBlank { AppSettings.DEFAULT_CLAUDE_MODEL }
            // Drop any legacy plaintext copy now that it lives encrypted.
            p.remove(Keys.CLAUDE_KEY)
        }
    }
}
