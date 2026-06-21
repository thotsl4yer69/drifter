package com.mz1312.drifter.data.alerts

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.mz1312.drifter.data.model.ApiResult
import com.mz1312.drifter.data.net.DrifterApi
import com.mz1312.drifter.data.store.SettingsStore
import kotlinx.coroutines.flow.first

/**
 * The background half of "it tells you": a periodic WorkManager job that probes
 * /healthz and fires a notification when the node's status *changes* — goes
 * unreachable, degrades, or recovers. Quiet by design about `ok-hw-pending`
 * (just dongles not plugged in), so it never cries wolf on the bench.
 */
class HealthWatchWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val settings = SettingsStore(applicationContext).settings.first()
        if (!settings.backgroundAlerts) return Result.success()

        val current = when (val r = DrifterApi(settings.httpBaseUrl).healthz()) {
            is ApiResult.Ok -> r.value.status        // ok | ok-hw-pending | degraded
            is ApiResult.Err -> STATUS_UNREACHABLE
        }

        val state = AlertState(applicationContext)
        val previous = state.last
        if (current != previous) {
            notifyTransition(previous, current, settings.host)
            state.last = current
        }
        return Result.success()
    }

    private fun notifyTransition(previous: String?, current: String, host: String) {
        when (current) {
            STATUS_UNREACHABLE -> AlertNotifier.post(
                applicationContext,
                "DRIFTER unreachable",
                "Can't reach the node at $host. It may be powered off, or the phone " +
                    "isn't on the MZ1312_DRIFTER hotspot.",
            )
            "degraded" -> AlertNotifier.post(
                applicationContext,
                "DRIFTER degraded",
                "A non-hardware service is down on the node. Open the app and ask the " +
                    "assistant what's wrong.",
            )
            "ok" -> if (previous != null && previous != "ok-hw-pending") {
                AlertNotifier.post(
                    applicationContext,
                    "DRIFTER recovered",
                    "The node is healthy again — every expected service is back up.",
                )
            }
            // "ok-hw-pending": healthy, only dongles missing — stay silent.
        }
    }

    private companion object {
        const val STATUS_UNREACHABLE = "unreachable"
    }
}
