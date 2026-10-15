package com.mz1312.drifter.jaguar

import android.app.Application
import android.os.Looper
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.test.core.app.ApplicationProvider
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import kotlinx.serialization.json.Json
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.TimeUnit
import java.time.Duration

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class MedicStateTest {
    private val app = ApplicationProvider.getApplicationContext<Application>()
    private fun model(store: ViewModelStore): JaguarMedicViewModel = ViewModelProvider(
        object : ViewModelStoreOwner { override val viewModelStore = store },
        ViewModelProvider.AndroidViewModelFactory(app),
    )[JaguarMedicViewModel::class.java]

    @Test fun repairLogAndWorkshopSettingsPersistWithoutChangingDrifterPreferences() {
        app.getSharedPreferences("drifter_secure_v2", 0).edit().putString("sentinel", "unchanged").commit()
        val firstStore = ViewModelStore()
        val first = model(firstStore)
        first.workshopBase = "http://127.0.0.1:8765"
        first.addEvent("repair", "engine", "Harness inspected", "Test record")
        val event = first.events.value.first()
        first.resolveEvent(event.id)
        firstStore.clear()
        val secondStore = ViewModelStore()
        val second = model(secondStore)
        assertEquals("resolved", second.events.value.first { it.id == event.id }.status)
        assertEquals("http://127.0.0.1:8765", second.workshopBase)
        assertEquals("unchanged", app.getSharedPreferences("drifter_secure_v2", 0).getString("sentinel", null))
        secondStore.clear()
    }

    @Test fun workshopApiParsesEvidenceAndRetainsOfflineFallbackOnFailure() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"results":[{"title":"Workshop test source","page":68,"text":"Check the harness","applicability":"X-Type","score":1.0}]}"""))
            server.enqueue(MockResponse().setResponseCode(503))
            val store = ViewModelStore()
            val vm = model(store)
            vm.workshopBase = server.url("/").toString()
            vm.searchWorkshop("B1S1 oxygen")
            await { vm.evidence.value.firstOrNull()?.title == "Workshop test source" }
            assertEquals(68, vm.evidence.value.first().page)
            assertTrue(server.takeRequest(2, TimeUnit.SECONDS)!!.path!!.startsWith("/api/search?q=B1S1+oxygen"))
            vm.searchWorkshop("fuel gauge")
            assertTrue(vm.evidence.value.isNotEmpty())
            assertTrue(vm.evidence.value.any { it.title.contains("XT310-05") })
            server.takeRequest(2, TimeUnit.SECONDS)
            store.clear()
        }
    }

    @Test fun invalidNodeAddressDoesNotCrashOrRemainConnecting() {
        val store = ViewModelStore()
        val vm = model(store)
        vm.connectDrifter("[")
        assertNotNull(vm.link.value.error)
        assertFalse(vm.link.value.busy)
        vm.disconnect()
        assertEquals(LinkMode.NONE, vm.link.value.mode)
        store.clear()
    }

    @Test fun invalidWifiAddressDoesNotReplaceSavedSettingsOrOpenConnection() {
        val store = ViewModelStore()
        val vm = model(store)
        val host = vm.wifiHost
        val port = vm.wifiPort
        vm.connectWifi("", 35000)
        vm.connectWifi("127.0.0.1", 70000)
        assertEquals(host, vm.wifiHost)
        assertEquals(port, vm.wifiPort)
        assertEquals(LinkMode.NONE, vm.link.value.mode)
        store.clear()
    }

    @Test fun lateWorkshopSearchCannotOverwriteNewerResults() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"results":[{"title":"Old search"}]}""").setBodyDelay(1, TimeUnit.SECONDS))
            server.enqueue(MockResponse().setBody("""{"results":[{"title":"Current search"}]}"""))
            val store = ViewModelStore()
            val vm = model(store)
            vm.workshopBase = server.url("/").toString()
            vm.searchWorkshop("oxygen")
            assertNotNull(server.takeRequest(2, TimeUnit.SECONDS))
            vm.searchWorkshop("fuel gauge")
            await { vm.evidence.value.firstOrNull()?.title == "Current search" }
            allowDelayedResponse()
            assertEquals("Current search", vm.evidence.value.first().title)
            store.clear()
        }
    }

    @Test fun lateWorkshopDiagnosisCannotMergeIntoNewerSymptoms() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"priority_lanes":[{"title":"Old diagnosis"}]}""").setBodyDelay(1, TimeUnit.SECONDS))
            server.enqueue(MockResponse().setBody("""{"priority_lanes":[{"title":"Current diagnosis"}]}"""))
            val store = ViewModelStore()
            val vm = model(store)
            vm.workshopBase = server.url("/").toString()
            vm.diagnose("oxygen")
            assertNotNull(server.takeRequest(2, TimeUnit.SECONDS))
            vm.diagnose("fuel gauge")
            await { vm.diagnosis.value.firstOrNull()?.title == "Current diagnosis" }
            allowDelayedResponse()
            assertEquals("Current diagnosis", vm.diagnosis.value.first().title)
            assertFalse(vm.diagnosis.value.any { it.title == "Old diagnosis" })
            store.clear()
        }
    }

    @Test fun captureTimerSavesAfterFortyFiveSecondsEvenIfTelemetryStops() {
        MockWebServer().use { node ->
            node.start(8081)
            node.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    webSocket.send("""{"topic":"drifter/engine/rpm","data":"NaN"}""")
                    webSocket.send("""{"topic":"drifter/engine/rpm","data":720}""")
                }
            }))
            val store = ViewModelStore()
            val vm = model(store)
            vm.connectDrifter("127.0.0.1")
            await { vm.snapshot.value.rpm == 720.0 }
            vm.captureIncident("timer test")
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(44))
            assertTrue(vm.incidents.value.isEmpty())
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(1))
            assertEquals("timer test", vm.incidents.value.single().reason)
            assertEquals(1, vm.incidents.value.single().sampleCount)
            store.clear()
            assertEquals(1, vm.incidents.value.size)
        }
    }

    @Test fun captureWithoutTelemetryDoesNotCreateAnEmptyIncident() {
        val store = ViewModelStore()
        val vm = model(store)
        vm.captureIncident()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(46))
        assertTrue(vm.incidents.value.isEmpty())
        store.clear()
    }

    @Test fun remoteDisconnectSavesPartialCaptureAndClearsStaleReadings() {
        MockWebServer().use { node ->
            lateinit var socket: WebSocket
            node.start(8081)
            node.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    socket = webSocket
                    webSocket.send("""{"topic":"drifter/engine/rpm","data":720}""")
                }
            }))
            val store = ViewModelStore()
            val vm = model(store)
            vm.connectDrifter("127.0.0.1")
            await { vm.snapshot.value.rpm == 720.0 }
            vm.captureIncident("connection lost")
            socket.close(1000, "Test server disconnected")
            await { !vm.link.value.connected }
            assertNull(vm.snapshot.value.rpm)
            assertEquals("connection lost", vm.incidents.value.single().reason)
            assertEquals("Test server disconnected", vm.link.value.error)
            store.clear()
        }
    }

    private fun allowDelayedResponse() {
        val deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(1200)
        while (System.nanoTime() < deadline) {
            shadowOf(Looper.getMainLooper()).idle()
            Thread.sleep(10)
        }
    }

    @Test fun leavingMedicSavesPartialCaptureAndReleasesConnectionState() {
        MockWebServer().use { node ->
            node.start(8081)
            node.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    webSocket.send("""{"topic":"drifter/engine/rpm","data":720}""")
                    webSocket.send("""{"topic":"drifter/engine/maf","data":4.1}""")
                }
            }))
            val store = ViewModelStore()
            val vm = model(store)
            vm.connectDrifter("127.0.0.1")
            await { vm.snapshot.value.mafGps == 4.1 }
            assertEquals(720.0, vm.snapshot.value.rpm!!, 0.001)
            vm.captureIncident("navigation regression marker")
            store.clear()
            val saved = vm.incidents.value.first()
            assertEquals("navigation regression marker", saved.reason)
            val file = java.io.File(app.filesDir, "incidents/${saved.fileName}")
            val samples = Json.decodeFromString<List<LiveSnapshot>>(file.readText())
            assertTrue(samples.isNotEmpty())
            assertEquals(720.0, samples.last().rpm!!, 0.001)
            assertEquals(4.1, samples.last().mafGps!!, 0.001)
            assertEquals(LinkMode.NONE, vm.link.value.mode)
            assertNull(vm.snapshot.value.rpm)
        }
    }

    private fun await(predicate: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
        while (!predicate() && System.nanoTime() < deadline) {
            shadowOf(Looper.getMainLooper()).idle()
            Thread.sleep(10)
        }
        assertTrue("Timed out waiting for workshop response", predicate())
    }
}
