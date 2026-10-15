package com.mz1312.drifter.jaguar

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.Closeable
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread

/** Real TCP I/O to a scripted adapter; never connects to a car or a network node. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class Elm327ClientTest {
    private fun client() = Elm327Client(ApplicationProvider.getApplicationContext<Context>())

    @Test fun wifiPollingDecodesMeasurementsAndConcurrentScanKeepsP0301Intact() = runBlocking {
        Adapter().use { adapter ->
            client().use { client ->
                client.connectWifi("127.0.0.1", adapter.port)
                coroutineScope {
                    val live = async { client.poll() }
                    val scan = async { client.readDtcs() }
                    val snapshot = live.await()
                    assertEquals(720.0, snapshot.rpm!!, 0.001)
                    assertEquals(4.0, snapshot.mafGps!!, 0.001)
                    assertEquals(90.0, snapshot.coolantC!!, 0.001)
                    assertEquals(14.1, snapshot.voltage!!, 0.001)
                    assertEquals(0.5, snapshot.o2B1s1V!!, 0.001)
                    assertEquals(listOf("P0301", "P1313", "P1646"), scan.await())
                }
                assertTrue(adapter.commands.containsAll(listOf("ATZ", "ATSP0", "0100", "03", "010C")))
            }
        }
    }

    @Test fun invalidDtcResponseIsAnErrorInsteadOfFalseAllClear() = runBlocking {
        Adapter(dtcResponse = "BUS ERROR").use { adapter ->
            client().use { client ->
                client.connectWifi("127.0.0.1", adapter.port)
                assertTrue(runCatching { client.readDtcs() }.isFailure)
            }
        }
    }

    @Test fun adapterNoDataIsSupportedWithoutFabricatingMeasurements() = runBlocking {
        Adapter(noData = true, dtcResponse = "NO DATA").use { adapter ->
            client().use { client ->
                client.connectWifi("127.0.0.1", adapter.port)
                val snapshot = client.poll()
                assertNull(snapshot.rpm)
                assertNull(snapshot.mafGps)
                assertEquals(12.4, snapshot.voltage!!, 0.001)
                assertTrue(client.readDtcs().isEmpty())
                client.close()
                assertTrue(runCatching { client.poll() }.isFailure)
            }
        }
    }

    private class Adapter(val noData: Boolean = false, val dtcResponse: String = "430301131316460000") : Closeable {
        private val server = ServerSocket(0)
        val port = server.localPort
        val commands = CopyOnWriteArrayList<String>()
        @Volatile private var socket: Socket? = null
        private val worker = thread(isDaemon = true, name = "test-elm327") {
            runCatching {
                server.accept().use { connection ->
                    socket = connection
                    val input = connection.getInputStream()
                    val output = connection.getOutputStream()
                    val command = StringBuilder()
                    while (true) {
                        val next = input.read()
                        if (next < 0) break
                        if (next.toChar() != '\r') { command.append(next.toChar()); continue }
                        val cmd = command.toString(); command.clear(); commands += cmd
                        val result = when {
                            cmd == "ATZ" -> "ELM327 v1.5"
                            cmd == "ATRV" -> "12.4V"
                            cmd.startsWith("AT") -> "OK"
                            cmd == "0100" -> "4100FFFFFFFF"
                            cmd == "03" -> dtcResponse
                            noData -> "NO DATA"
                            else -> mapOf(
                                "010C" to "410C0B40", "010D" to "410D00", "0105" to "410582",
                                "0110" to "41100190", "0106" to "410680", "0107" to "410780",
                                "0108" to "410880", "0109" to "410980", "0104" to "410420",
                                "0111" to "411110", "010F" to "410F50", "0142" to "41423714",
                                "0114" to "41146480",
                            )[cmd] ?: "NO DATA"
                        }
                        // Echo and fragment replies to expose interleaved command races.
                        output.write("$cmd\r".toByteArray()); output.flush()
                        Thread.sleep(15)
                        output.write("$result\r>".toByteArray()); output.flush()
                    }
                }
            }
        }
        override fun close() {
            socket?.close(); server.close(); worker.join(1000)
        }
    }
}
