package com.mz1312.drifter.data.store

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Keystore-backed storage for the one secret the app holds — the Claude API key.
 *
 * The AES-256 key lives in the hardware-backed Android Keystore (never leaves
 * it); we keep only the GCM ciphertext + IV in plain SharedPreferences, which is
 * safe because they're useless without the Keystore key. This replaces the
 * deprecated androidx.security:security-crypto (Jetpack Security) with the
 * platform primitives directly, so there's no maintenance-mode dependency.
 *
 * If anything crypto-side fails (a wiped/rotated Keystore entry), reads return
 * "" rather than crashing — the operator just re-enters the key once.
 */
class SecureStore(context: Context) {

    private val prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var apiKey: String
        get() = decrypt(prefs.getString(KEY_CIPHER, null), prefs.getString(KEY_IV, null))
        set(value) {
            val editor = prefs.edit()
            if (value.isBlank()) {
                editor.remove(KEY_CIPHER).remove(KEY_IV)
            } else {
                runCatching { encrypt(value) }
                    .onSuccess { (ct, iv) -> editor.putString(KEY_CIPHER, ct).putString(KEY_IV, iv) }
                    .onFailure { Log.w(TAG, "encrypt failed: ${it.message}") }
            }
            editor.apply()
        }

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    private fun encrypt(plain: String): Pair<String, String> {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val ciphertext = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return ciphertext.b64() to cipher.iv.b64()
    }

    private fun decrypt(cipherB64: String?, ivB64: String?): String {
        if (cipherB64 == null || ivB64 == null) return ""
        return runCatching {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(GCM_TAG_BITS, ivB64.unb64()))
            String(cipher.doFinal(cipherB64.unb64()), Charsets.UTF_8)
        }.getOrElse {
            Log.w(TAG, "decrypt failed (Keystore reset?): ${it.message}")
            ""
        }
    }

    private fun ByteArray.b64(): String = Base64.encodeToString(this, Base64.NO_WRAP)
    private fun String.unb64(): ByteArray = Base64.decode(this, Base64.NO_WRAP)

    private companion object {
        const val TAG = "SecureStore"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "drifter_api_key_v1"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val GCM_TAG_BITS = 128
        const val FILE = "drifter_secure_v2"
        const val KEY_CIPHER = "api_key_ct"
        const val KEY_IV = "api_key_iv"
    }
}
