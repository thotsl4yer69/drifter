package com.mz1312.drifter.jaguar

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.Closeable
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.UUID

class Elm327Client(private val context: Context) : Closeable {
    private val commandLock = Mutex()
    private var input: InputStream? = null
    private var output: OutputStream? = null
    private var closeable: Closeable? = null

    companion object {
        private val SPP_UUID: UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
    }

    @SuppressLint("MissingPermission")
    fun bondedDevices(): List<BondedElmDevice> {
        val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter ?: return emptyList()
        return adapter.bondedDevices.orEmpty()
            .map { BondedElmDevice(it.name ?: "Bluetooth device", it.address) }
            .sortedWith(compareBy<BondedElmDevice>({ !it.name.contains("OBD", true) && !it.name.contains("ELM", true) }, { it.name }))
    }

    @SuppressLint("MissingPermission")
    suspend fun connectBluetooth(address: String) = withContext(Dispatchers.IO) {
        close()
        val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
            ?: error("Bluetooth is not available on this phone")
        val device = adapter.getRemoteDevice(address)
        val socket = device.createRfcommSocketToServiceRecord(SPP_UUID)
        closeable = socket
        socket.connect()
        currentCoroutineContext().ensureActive()
        input = BufferedInputStream(socket.inputStream)
        output = BufferedOutputStream(socket.outputStream)
        closeable = socket
        initialise()
    }

    suspend fun connectWifi(host: String, port: Int) = withContext(Dispatchers.IO) {
        close()
        val socket = Socket()
        closeable = socket
        socket.connect(InetSocketAddress(host, port), 4000)
        currentCoroutineContext().ensureActive()
        socket.soTimeout = 3500
        input = BufferedInputStream(socket.getInputStream())
        output = BufferedOutputStream(socket.getOutputStream())
        closeable = socket
        initialise()
    }

    private suspend fun initialise() {
        command("ATZ", 1800)
        delay(300)
        listOf("ATE0", "ATL0", "ATS0", "ATH0", "ATAT1", "ATSP0").forEach { command(it) }
        val probe = command("0100", 5000)
        if (!probe.contains("41") && !probe.contains("NO DATA", true)) {
            error("ELM327 answered, but ECU handshake did not produce a valid OBD response")
        }
    }

    suspend fun poll(): LiveSnapshot = withContext(Dispatchers.IO) {
        suspend fun v(pid: String): List<Int>? = readPid(pid)
        val rpm = v("0C")?.let { bytes -> if (bytes.size >= 2) ((bytes[0] * 256 + bytes[1]) / 4.0) else null }
        val speed = v("0D")?.firstOrNull()?.toDouble()
        val coolant = v("05")?.firstOrNull()?.minus(40)?.toDouble()
        val maf = v("10")?.let { if (it.size >= 2) (it[0] * 256 + it[1]) / 100.0 else null }
        val stft1 = v("06")?.firstOrNull()?.let(::trimPct)
        val ltft1 = v("07")?.firstOrNull()?.let(::trimPct)
        val stft2 = v("08")?.firstOrNull()?.let(::trimPct)
        val ltft2 = v("09")?.firstOrNull()?.let(::trimPct)
        val load = v("04")?.firstOrNull()?.let { it * 100.0 / 255.0 }
        val throttle = v("11")?.firstOrNull()?.let { it * 100.0 / 255.0 }
        val iat = v("0F")?.firstOrNull()?.minus(40)?.toDouble()
        val voltage = v("42")?.let { if (it.size >= 2) (it[0] * 256 + it[1]) / 1000.0 else null }
            ?: runCatching { parseAdapterVoltage(command("ATRV")) }.getOrNull()
        val o2 = v("14")?.firstOrNull()?.let { it / 200.0 }
        LiveSnapshot(
            rpm = rpm, speedKph = speed, coolantC = coolant, mafGps = maf,
            stftB1 = stft1, ltftB1 = ltft1, stftB2 = stft2, ltftB2 = ltft2,
            throttlePct = throttle, loadPct = load, intakeC = iat, voltage = voltage,
            o2B1s1V = o2,
        )
    }

    suspend fun readDtcs(): List<String> = withContext(Dispatchers.IO) {
        val raw = command("03", 5000)
        val hex = raw.uppercase().replace(Regex("[^0-9A-F]"), "")
        if (raw.contains("NO DATA", true)) return@withContext emptyList()
        val idx = hex.indexOf("43")
        check(idx >= 0) { "Invalid Mode-03 response: $raw" }
        val payload = hex.substring(idx + 2)
        payload.chunked(4).mapNotNull { word ->
            if (word.length < 4 || word == "0000") null else decodeDtc(word)
        }.distinct()
    }

    private suspend fun readPid(pid: String): List<Int>? {
        val raw = command("01$pid", 3500)
        if (raw.contains("NO DATA", true) || raw.contains("UNABLE", true)) return null
        val hex = raw.uppercase().replace(Regex("[^0-9A-F]"), "")
        val marker = "41${pid.uppercase()}"
        val idx = hex.indexOf(marker)
        if (idx < 0) return null
        val payload = hex.substring(idx + marker.length)
        return payload.chunked(2).takeWhile { it.length == 2 }.mapNotNull { it.toIntOrNull(16) }
    }

    // A scan and the live poller share one ELM command/response channel.
    private suspend fun command(cmd: String, timeoutMs: Long = 3000): String =
        commandLock.withLock {
            withContext(Dispatchers.IO) {
                val out = output ?: error("ELM327 is not connected")
                val inp = input ?: error("ELM327 is not connected")
                while (inp.available() > 0) inp.read()
                out.write((cmd.trim() + "\r").toByteArray(Charsets.US_ASCII))
                out.flush()
                val deadline = System.nanoTime() + timeoutMs * 1_000_000
                val response = StringBuilder()
                var complete = false
                while (System.nanoTime() < deadline) {
                    currentCoroutineContext().ensureActive()
                    if (inp.available() > 0) {
                        val c = inp.read()
                        check(c >= 0) { "ELM327 disconnected" }
                        if (c.toChar() == '>') {
                            complete = true
                            break
                        }
                        response.append(c.toChar())
                    } else {
                        delay(12)
                    }
                }
                check(complete) { "Timed out waiting for ELM327 prompt for $cmd" }
                // Remove only an echoed command line, never matching bytes in a PID/DTC.
                response.toString().split('\r', '\n')
                    .map { it.trim() }
                    .filter { it.isNotEmpty() && !it.equals(cmd, ignoreCase = true) }
                    .joinToString("\n")
                    .also { check(it.isNotBlank()) { "Empty ELM327 response for $cmd" } }
            }
        }

    private fun trimPct(b: Int): Double = (b - 128) * 100.0 / 128.0

    private fun parseAdapterVoltage(raw: String): Double? = Regex("(\\d{1,2}(?:\\.\\d+)?)\\s*V", RegexOption.IGNORE_CASE)
        .find(raw)?.groupValues?.getOrNull(1)?.toDoubleOrNull()

    private fun decodeDtc(word: String): String? {
        val a = word.substring(0, 2).toIntOrNull(16) ?: return null
        val b = word.substring(2, 4).toIntOrNull(16) ?: return null
        val letter = when ((a shr 6) and 0x3) { 0 -> 'P'; 1 -> 'C'; 2 -> 'B'; else -> 'U' }
        val d1 = (a shr 4) and 0x3
        val d2 = a and 0xF
        val d3 = (b shr 4) and 0xF
        val d4 = b and 0xF
        return "$letter$d1${d2.toString(16)}${d3.toString(16)}${d4.toString(16)}".uppercase()
    }

    override fun close() {
        runCatching { input?.close() }
        runCatching { output?.close() }
        runCatching { closeable?.close() }
        input = null; output = null; closeable = null
    }
}
