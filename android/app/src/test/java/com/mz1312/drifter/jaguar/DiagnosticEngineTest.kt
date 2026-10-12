package com.mz1312.drifter.jaguar

import org.junit.Assert.assertTrue
import org.junit.Test

class DiagnosticEngineTest {
    @Test
    fun currentRoughRunningFaultPrioritisesO2MafAndAirLeakWhenEvidenceSupportsIt() {
        val snapshot = LiveSnapshot(rpm = 720.0, coolantC = 91.0, mafGps = 2.1, stftB1 = 18.0, stftB2 = 16.0, voltage = 14.1)
        val lanes = DiagnosticEngine.diagnose("rough idle stutter stall B1S1 oxygen sensor MAF", snapshot, listOf("P0171"))
        val titles = lanes.map { it.title }
        assertTrue(titles.any { it.contains("O₂") })
        assertTrue(titles.any { it.contains("MAF") })
        assertTrue(titles.any { it.contains("Unmetered-air") })
    }

    @Test fun fuelGaugeSymptomUsesJaguarBulletin() {
        val lanes = DiagnosticEngine.diagnose("fuel gauge only reads half full", LiveSnapshot(), emptyList())
        assertTrue(lanes.any { lane -> lane.evidence.any { it.contains("XT310-05") } })
    }

    @Test fun factoryMafRangeIsEncoded() {
        assertTrue(FactorySpecs.MAF_IDLE_MIN == 3.0)
        assertTrue(FactorySpecs.MAF_IDLE_MAX == 6.0)
    }
}
