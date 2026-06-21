package com.mz1312.drifter.data.store

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Keystore-backed storage for the one real secret the app holds — the user's
 * Claude API key. Backed by EncryptedSharedPreferences (AES-256, with the
 * master key held in the hardware-backed Android Keystore), so the key is
 * encrypted at rest rather than sitting in plaintext prefs/DataStore.
 *
 * If crypto initialisation ever fails (a corrupted keystore is the only
 * realistic cause), it degrades to a private SharedPreferences so the app keeps
 * working instead of crashing — logged so the degradation is visible.
 */
class SecureStore(context: Context) {

    private val prefs: SharedPreferences = runCatching {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            FILE,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }.getOrElse { e ->
        Log.w(TAG, "encrypted prefs unavailable, using private fallback: ${e.message}")
        context.getSharedPreferences("${FILE}_fallback", Context.MODE_PRIVATE)
    }

    var apiKey: String
        get() = prefs.getString(KEY_API, "").orEmpty()
        set(value) {
            prefs.edit().apply {
                if (value.isBlank()) remove(KEY_API) else putString(KEY_API, value)
            }.apply()
        }

    private companion object {
        const val TAG = "SecureStore"
        const val FILE = "drifter_secure"
        const val KEY_API = "claude_api_key"
    }
}
