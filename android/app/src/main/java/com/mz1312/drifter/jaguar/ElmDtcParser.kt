package com.mz1312.drifter.jaguar

/** ATH0/CAF1 responses. See ELM327DSK pp.35, 43–44 for protocol differences. */
internal object ElmDtcParser {
    fun parse(raw: String, can: Boolean): List<String> {
        val lines = raw.uppercase().lineSequence().map { it.filterNot(Char::isWhitespace) }
            .filter { it.isNotEmpty() && it != "SEARCHING..." }.toList()
        check(lines.isNotEmpty() && "NODATA" !in lines) { "No ECU response; DTC status is unknown" }
        val messages = mutableListOf<String>()
        var expectedBytes: Int? = null
        var nextFrame = 0
        val frames = StringBuilder()
        for (line in lines) {
            when {
                can && line.matches(Regex("[0-9A-F]{3}")) -> {
                    check(expectedBytes == null) { "Interleaved CAN responses cannot be attributed with headers off" }
                    expectedBytes = line.toInt(16)
                    check(expectedBytes >= 2) { "Invalid CAN response length" }
                    frames.clear(); nextFrame = 0
                }
                can && line.contains(':') -> {
                    val length = checkNotNull(expectedBytes) { "Missing CAN response length" }
                    val parts = line.split(':')
                    check(parts.size == 2 && parts[0] == (nextFrame % 16).toString(16).uppercase()) { "Missing or reordered CAN frame" }
                    val data = parts[1]
                    check(data.matches(Regex("[0-9A-F]+")) && data.length % 2 == 0) { "Invalid CAN frame" }
                    frames.append(data); nextFrame++
                    if (frames.length >= length * 2) {
                        check(frames.drop(length * 2).all { it == '0' }) { "Unexpected CAN trailing data" }
                        messages += frames.substring(0, length * 2)
                        expectedBytes = null
                    }
                }
                else -> {
                    check(expectedBytes == null) { "Incomplete CAN response" }
                    messages += line
                }
            }
        }
        check(expectedBytes == null && messages.isNotEmpty()) { "Incomplete Mode-03 response" }
        return messages.flatMap { message ->
            check(message.matches(Regex("43[0-9A-F]+")) && message.length % 2 == 0) { "Invalid Mode-03 response: $message" }
            val payload = if (can) {
                val count = message.substring(2, 4).toInt(16)
                val end = 4 + count * 4
                check(message.length >= end && message.drop(end).all { it == '0' }) { "DTC count does not match response" }
                message.substring(4, end)
            } else message.substring(2)
            check(payload.length % 4 == 0) { "Truncated Mode-03 DTC" }
            payload.chunked(4).filter { it != "0000" }.map(::decode)
        }.distinct()
    }

    private fun decode(word: String): String {
        val first = word[0].digitToInt(16)
        return "${"PCBU"[first shr 2]}${first and 3}${word.substring(1)}"
    }
}
