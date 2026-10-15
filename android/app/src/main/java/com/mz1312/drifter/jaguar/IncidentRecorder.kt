package com.mz1312.drifter.jaguar

import java.util.ArrayDeque

/** Uses elapsed time for windows; wall-clock changes must not change capture duration. */
internal class IncidentRecorder(private val elapsedMs: () -> Long) {
    companion object {
        const val BEFORE_MS = 90_000L
        const val AFTER_MS = 45_000L
    }

    data class Capture(val triggerTs: Long, val reason: String, val samples: List<LiveSnapshot>)
    private data class Sample(val elapsed: Long, val value: LiveSnapshot)
    private data class Active(val triggerTs: Long, val reason: String, val deadline: Long, val samples: MutableList<LiveSnapshot>)
    private val recent = ArrayDeque<Sample>()
    private var active: Active? = null
    val isCapturing: Boolean get() = active != null

    fun record(snapshot: LiveSnapshot) {
        val now = elapsedMs()
        recent.addLast(Sample(now, snapshot))
        prune(now)
        active?.let { if (now <= it.deadline) it.samples += snapshot }
    }

    fun start(triggerTs: Long, reason: String): Boolean {
        if (active != null) return false
        val now = elapsedMs()
        prune(now)
        if (recent.isEmpty()) return false
        active = Active(triggerTs, reason, now + AFTER_MS, recent.map { it.value }.toMutableList())
        return true
    }

    fun finish(): Capture? {
        val capture = active ?: return null
        active = null
        return Capture(capture.triggerTs, capture.reason, capture.samples.toList())
    }

    fun clearHistory() = recent.clear()

    private fun prune(now: Long) {
        while (recent.isNotEmpty() && recent.first.elapsed < now - BEFORE_MS) recent.removeFirst()
    }
}
