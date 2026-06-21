package com.mz1312.drifter.data.alerts

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.mz1312.drifter.R

/** Builds + posts the node-health notifications fired by the background watch. */
object AlertNotifier {

    const val CHANNEL_ID = "drifter_health"
    private const val NOTIF_ID = 4201

    fun ensureChannel(context: Context) {
        val mgr = context.getSystemService(NotificationManager::class.java) ?: return
        if (mgr.getNotificationChannel(CHANNEL_ID) == null) {
            mgr.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "Node health",
                    NotificationManager.IMPORTANCE_HIGH,
                ).apply {
                    description = "Alerts when the Drifter node degrades or goes offline."
                },
            )
        }
    }

    fun post(context: Context, title: String, text: String) {
        ensureChannel(context)
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_drifter)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(NOTIF_ID, notification)
        } catch (_: SecurityException) {
            // POST_NOTIFICATIONS not granted — nothing to do; the in-app UI still shows state.
        }
    }
}
