package com.mz1312.drifter.jaguar

import org.junit.Assert.*
import org.junit.Test

class ElmDtcParserTest {
    @Test fun separateEcuRepliesDoNotCreateCodesAcrossLineBoundaries() {
        assertEquals(listOf("P0301", "P1313", "P1646"), ElmDtcParser.parse(
            "43 03 01 00 00 00 00\n43 13 13 16 46 00 00\n43 03 01 00 00 00 00", can = false,
        ))
    }

    @Test fun canCountIsNotDecodedAsPartOfTheFirstCode() {
        assertEquals(listOf("P0301", "U1016", "C1234", "B1234"),
            ElmDtcParser.parse("43020301D016\n430252349234", can = true))
        assertTrue(ElmDtcParser.parse("4300000000000000", can = true).isEmpty())
        assertTrue(ElmDtcParser.parse("43000000000000", can = false).isEmpty())
    }

    @Test fun numberedCanFramesAreReassembledBeforeDecoding() {
        assertEquals(listOf("P0301", "P1313", "P1646", "U1016"),
            ElmDtcParser.parse("00A\n0:430403011313\n1:1646D016", can = true))
    }

    @Test fun badTruncatedOrMissingDataNeverMeansAllClear() {
        for (raw in listOf("NO DATA", "BUS ERROR", "43", "4303", "4303010", "430301\nBUS ERROR", "7E8430301")) {
            assertTrue(raw, runCatching { ElmDtcParser.parse(raw, can = false) }.isFailure)
        }
        for (raw in listOf("43020301", "43000301", "00A\n0:430403011313", "00A\n0:430403011313\n2:1646D016", "00A\n0:430403011313\n00A\n0:430403011313")) {
            assertTrue(raw, runCatching { ElmDtcParser.parse(raw, can = true) }.isFailure)
        }
    }
}
