package com.mz1312.drifter.data.alerts

import android.content.Context

/**
 * Remembers the last node status the background watch *notified* about, so we
 * only alert on a transition (down → up, ok → degraded) instead of every poll.
 */
class AlertState(context: Context) {
    private val prefs = context.getSharedPreferences("drifter_alert_state", Context.MODE_PRIVATE)

    var last: String?
        get() = prefs.getString(KEY, null)
        set(value) {
            prefs.edit().apply {
                if (value == null) remove(KEY) else putString(KEY, value)
            }.apply()
        }

    private companion object {
        const val KEY = "last_status"
    }
}
