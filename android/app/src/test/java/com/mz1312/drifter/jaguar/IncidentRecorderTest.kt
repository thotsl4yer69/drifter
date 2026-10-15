package com.mz1312.drifter.jaguar

import org.junit.Assert.*
import org.junit.Test

class IncidentRecorderTest {
    private var elapsed = 0L
    private val recorder = IncidentRecorder { elapsed }

    @Test fun includesOnlyNinetySecondsBeforeAndFortyFiveSecondsAfter() {
        for (second in 0..100) {
            elapsed = second * 1000L
            recorder.record(LiveSnapshot(ts = elapsed, rpm = second.toDouble()))
        }
        assertTrue(recorder.start(100_000, "window test"))
        for (second in 101..146) {
            elapsed = second * 1000L
            recorder.record(LiveSnapshot(ts = elapsed, rpm = second.toDouble()))
        }
        val saved = recorder.finish()!!
        assertEquals((10L..145L).map { it * 1000 }, saved.samples.map { it.ts })
        assertEquals("window test", saved.reason)
        assertNull(recorder.finish())
    }

    @Test fun staleHistoryIsPrunedEvenWhenNoNewSampleArrives() {
        recorder.record(LiveSnapshot())
        elapsed = 90_001
        assertFalse(recorder.start(100_000, "no stale data"))
        assertNull(recorder.finish())
    }

    @Test fun wallClockJumpDoesNotChangeWindowOrSampleOrder() {
        recorder.record(LiveSnapshot(ts = 100_000))
        elapsed = 10_000
        recorder.record(LiveSnapshot(ts = 1))
        assertTrue(recorder.start(2, "clock adjusted"))
        elapsed = 55_000
        recorder.record(LiveSnapshot(ts = 900_000))
        elapsed = 55_001
        recorder.record(LiveSnapshot(ts = 3))
        assertEquals(listOf(100_000L, 1L, 900_000L), recorder.finish()!!.samples.map { it.ts })
    }

    @Test fun duplicateTriggerDoesNotReplaceCaptureAndNewSessionHasNoOldSamples() {
        recorder.record(LiveSnapshot(rpm = 720.0))
        assertTrue(recorder.start(1, "first"))
        assertFalse(recorder.start(2, "second"))
        assertEquals("first", recorder.finish()!!.reason)
        recorder.clearHistory()
        assertFalse(recorder.start(3, "disconnected"))
    }
}
