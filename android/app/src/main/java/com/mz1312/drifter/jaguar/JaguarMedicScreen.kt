package com.mz1312.drifter.jaguar

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlin.math.abs

enum class MedicTab(val label: String, val icon: ImageVector) {
    CAR("CAR", Icons.Filled.DirectionsCar), LIVE("LIVE", Icons.Filled.Speed),
    DIAG("DIAG", Icons.Filled.Build), MANUAL("MANUAL", Icons.Filled.MenuBook), LOG("LOG", Icons.Filled.List)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun JaguarMedicApp(vm: JaguarMedicViewModel) {
    var tab by remember { mutableStateOf(MedicTab.CAR) }
    val link by vm.link.collectAsStateWithLifecycle()
    val snack = remember { SnackbarHostState() }
    LaunchedEffect(Unit) { vm.messages.collect { snack.showSnackbar(it) } }
    Scaffold(
        containerColor = Color.Transparent,
        snackbarHost = { SnackbarHost(snack) },
        topBar = { TopAppBar(title = { Column { Text("MZ1312 // X-TYPE MEDIC", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelMedium); Text("2004 X-Type 2.1 V6 · MY05") } }, actions = { Text(if (link.connected) link.mode.label else "OFFLINE", color = if (link.connected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(end = 12.dp)) }, colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent)) },
        bottomBar = { NavigationBar { MedicTab.entries.forEach { t -> NavigationBarItem(selected = tab == t, onClick = { tab = t }, icon = { Icon(t.icon, t.label) }, label = { Text(t.label) }) } } }
    ) { p -> Box(Modifier.fillMaxSize().padding(p)) { when (tab) { MedicTab.CAR -> CarScreen(vm, { tab = MedicTab.LIVE }, { tab = MedicTab.DIAG }); MedicTab.LIVE -> LiveScreen(vm); MedicTab.DIAG -> DiagScreen(vm); MedicTab.MANUAL -> ManualScreen(vm); MedicTab.LOG -> LogScreen(vm) } } }
}

@Composable private fun CarScreen(vm: JaguarMedicViewModel, goLive: () -> Unit, goDiag: () -> Unit) {
    val s by vm.snapshot.collectAsStateWithLifecycle(); val events by vm.events.collectAsStateWithLifecycle(); val incidents by vm.incidents.collectAsStateWithLifecycle()
    LazyColumn(Modifier.fillMaxSize().padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { Panel("CURRENT CAR", "Live evidence + persistent fault history") {
            Metrics(s)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) { Button(goLive, Modifier.weight(1f)) { Text("CONNECT OBD") }; Button({ vm.captureIncident() }, Modifier.weight(1f), colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)) { Text("CAPTURE FAULT") } }
            OutlinedButton({ vm.diagnose("rough idle stutter from stop occasional stall B1S1 O2 MAF"); goDiag() }, Modifier.fillMaxWidth()) { Text("RUN CURRENT-FAULT TRIAGE") }
        } }
        item { Panel("FACTORY TARGETS", "Grounded in Jaguar workshop material") {
            Check("MAF warm idle", "3.0–6.0 g/s", s.mafGps?.let { it in 3.0..6.0 })
            Check("STFT warm", "around 0% ±7%", if (!s.warm) null else listOfNotNull(s.stftB1,s.stftB2).takeIf { it.isNotEmpty() }?.all { abs(it)<=7 })
            Check("Coolant", "below 105°C during normal test", s.coolantC?.let { it < 105 })
            Check("Running voltage", ">11.7V alert floor", if (!s.engineRunning) null else s.voltage?.let { it >= 11.7 })
        } }
        item { Title("OPEN CAR FILE", "${events.count { it.status != "resolved" }} active") }
        items(events.filter { it.status != "resolved" }.take(6), key={it.id}) { e -> Event(e) { vm.resolveEvent(e.id) } }
        if (incidents.isNotEmpty()) { item { Title("BLACK BOX", "90s before + 45s after") }; items(incidents.take(3), key={it.id}) { i -> Panel(i.reason.uppercase(), "${i.sampleCount} samples") { Text(i.firstChange ?: "Captured for later comparison") } } }
    }
}

@Composable private fun LiveScreen(vm: JaguarMedicViewModel) {
    val context= LocalContext.current; val link by vm.link.collectAsStateWithLifecycle(); val s by vm.snapshot.collectAsStateWithLifecycle(); val devices by vm.devices.collectAsStateWithLifecycle(); val dtcs by vm.dtcs.collectAsStateWithLifecycle()
    var host by remember { mutableStateOf(vm.wifiHost) }; var port by remember { mutableStateOf(vm.wifiPort.toString()) }; var drifter by remember { mutableStateOf(vm.drifterHost) }
    val perm=if(Build.VERSION.SDK_INT>=31) Manifest.permission.BLUETOOTH_CONNECT else Manifest.permission.BLUETOOTH
    val launcher= rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { if(it) vm.refreshBondedDevices() }
    LaunchedEffect(Unit) { if(context.checkSelfPermission(perm)==PackageManager.PERMISSION_GRANTED) vm.refreshBondedDevices() }
    LazyColumn(Modifier.fillMaxSize().padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { Panel("OBD LINK", link.error ?: if(link.connected) "Connected to ${link.label}" else "Use your existing paired ELM327 or DRIFTER") {
            if(context.checkSelfPermission(perm)!=PackageManager.PERMISSION_GRANTED) Button({launcher.launch(perm)},Modifier.fillMaxWidth()){Text("GRANT BLUETOOTH ACCESS")}
            else { Text("PAIRED BLUETOOTH", style=MaterialTheme.typography.labelSmall, color=MaterialTheme.colorScheme.primary); devices.forEach { d -> OutlinedButton({vm.connectBluetooth(d.address)},Modifier.fillMaxWidth()){Text("${d.name}  ${d.address}")} } }
            Row(Modifier.fillMaxWidth(), horizontalArrangement=Arrangement.spacedBy(8.dp)){ OutlinedTextField(host,{host=it},Modifier.weight(1f),label={Text("Wi-Fi ELM host")},singleLine=true); OutlinedTextField(port,{port=it},Modifier.width(105.dp),label={Text("Port")},singleLine=true) }
            Button({vm.connectWifi(host,port.toIntOrNull()?:35000)},Modifier.fillMaxWidth()){Icon(Icons.Filled.Wifi,null);Spacer(Modifier.width(6.dp));Text("CONNECT WI-FI ELM")}
            OutlinedTextField(drifter,{drifter=it},Modifier.fillMaxWidth(),label={Text("DRIFTER host")},singleLine=true); OutlinedButton({vm.connectDrifter(drifter)},Modifier.fillMaxWidth()){Text("USE DRIFTER TELEMETRY")}
            if(link.connected) TextButton({vm.disconnect()},Modifier.fillMaxWidth()){Text("DISCONNECT")}
        } }
        item { Panel("LIVE ENGINE", "RPM · trims · MAF · coolant · voltage") { Metrics(s); Row(horizontalArrangement=Arrangement.spacedBy(8.dp)){Button({vm.scanDtcs()},Modifier.weight(1f)){Text("SCAN DTCs")};Button({vm.captureIncident("manual road-test marker")},Modifier.weight(1f)){Text("MARK FAULT")}}; if(dtcs.isNotEmpty()) Text("DTC: ${dtcs.joinToString()}",color=MaterialTheme.colorScheme.error,fontFamily=FontFamily.Monospace) } }
    }
}

@Composable private fun DiagScreen(vm: JaguarMedicViewModel) {
    val lanes by vm.diagnosis.collectAsStateWithLifecycle(); val dtcs by vm.dtcs.collectAsStateWithLifecycle(); var q by remember { mutableStateOf("Rough idle, stutters from stop, occasional stall. B1S1 O2 was replaced; MAF connection uncertain.") }
    LazyColumn(Modifier.fillMaxSize().padding(14.dp), verticalArrangement=Arrangement.spacedBy(10.dp)) {
        item { Panel("DIAGNOSE", "Ranks tests using symptoms + live data + DTCs") { OutlinedTextField(q,{q=it},Modifier.fillMaxWidth(),minLines=3,label={Text("What is the car doing?")}); Button({vm.diagnose(q)},Modifier.fillMaxWidth()){Text("BUILD TEST ORDER")}; if(dtcs.isNotEmpty()) Text("Codes: ${dtcs.joinToString()}",fontFamily=FontFamily.Monospace) } }
        items(lanes,key={"${it.priority}-${it.title}"}) { lane -> Panel("${lane.priority.toString().padStart(2,'0')}  ${lane.title}", lane.reason) { lane.checks.forEachIndexed { i,c -> Text("${i+1}. $c",modifier=Modifier.padding(vertical=3.dp)) }; lane.evidence.forEach { Text(it,color=MaterialTheme.colorScheme.primary,style=MaterialTheme.typography.labelSmall) } } }
    }
}

@Composable private fun ManualScreen(vm: JaguarMedicViewModel) {
    val hits by vm.evidence.collectAsStateWithLifecycle(); var q by remember { mutableStateOf("B1S1 upstream oxygen sensor location") }; var server by remember { mutableStateOf(vm.workshopBase) }
    LazyColumn(Modifier.fillMaxSize().padding(14.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
        item { Panel("WORKSHOP INTELLIGENCE","Offline key evidence + full Jaguar RAG when reachable") { OutlinedTextField(q,{q=it},Modifier.fillMaxWidth(),label={Text("Manual / DTC / component search")}); Button({vm.workshopBase=server;vm.searchWorkshop(q)},Modifier.fillMaxWidth()){Icon(Icons.Filled.Search,null);Spacer(Modifier.width(6.dp));Text("SEARCH")}; OutlinedTextField(server,{server=it},Modifier.fillMaxWidth(),label={Text("Workshop server")},singleLine=true) } }
        items(hits,key={"${it.title}-${it.page}-${it.snippet.hashCode()}"}) { h -> Panel(h.title, listOfNotNull(h.page?.let{"PDF p.$it"},h.applicability.takeIf{it.isNotBlank()}).joinToString(" · ")) { Text(h.snippet) } }
    }
}

@Composable private fun LogScreen(vm: JaguarMedicViewModel) {
    val events by vm.events.collectAsStateWithLifecycle(); val incidents by vm.incidents.collectAsStateWithLifecycle(); var summary by remember{mutableStateOf("")}; var details by remember{mutableStateOf("")}; var subsystem by remember{mutableStateOf("engine")}
    LazyColumn(Modifier.fillMaxSize().padding(14.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
        item { Panel("ADD TO CAR FILE","Repairs, tests and symptoms stay with this car") { OutlinedTextField(summary,{summary=it},Modifier.fillMaxWidth(),label={Text("Summary")}); OutlinedTextField(subsystem,{subsystem=it},Modifier.fillMaxWidth(),label={Text("Subsystem")}); OutlinedTextField(details,{details=it},Modifier.fillMaxWidth(),label={Text("Details")},minLines=2); Button({vm.addEvent("observation",subsystem,summary,details);summary="";details=""},Modifier.fillMaxWidth()){Icon(Icons.Filled.Add,null);Spacer(Modifier.width(6.dp));Text("ADD ENTRY")} } }
        if(incidents.isNotEmpty()){ item{Title("INCIDENTS","${incidents.size} captures")}; items(incidents,key={it.id}){i->Panel(i.reason,"${i.sampleCount} samples · ${i.fileName}"){Text(i.firstChange?:"No first change isolated")}} }
        item{Title("HISTORY","${events.size} entries")}; items(events,key={it.id}){e->Event(e){vm.resolveEvent(e.id)}}
    }
}

@Composable private fun Metrics(s: LiveSnapshot) { val rows=listOf("RPM" to (s.rpm?.let{"%.0f".format(it)}?:"—"),"MAF" to (s.mafGps?.let{"%.1f g/s".format(it)}?:"—"),"STFT B1" to (s.stftB1?.let{"%+.1f%%".format(it)}?:"—"),"STFT B2" to (s.stftB2?.let{"%+.1f%%".format(it)}?:"—"),"Coolant" to (s.coolantC?.let{"%.0f°C".format(it)}?:"—"),"Voltage" to (s.voltage?.let{"%.1f V".format(it)}?:"—")); rows.chunked(3).forEach{r->Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(7.dp)){r.forEach{(a,b)->Surface(Modifier.weight(1f),shape=RoundedCornerShape(10.dp),color=MaterialTheme.colorScheme.surfaceContainerHighest){Column(Modifier.padding(9.dp)){Text(a,style=MaterialTheme.typography.labelSmall);Text(b,fontFamily=FontFamily.Monospace,fontWeight=FontWeight.Bold,color=MaterialTheme.colorScheme.primary)}}}};Spacer(Modifier.height(7.dp))} }
@Composable private fun Check(label:String,target:String,ok:Boolean?){Row(Modifier.fillMaxWidth().padding(vertical=4.dp),verticalAlignment=Alignment.CenterVertically){Icon(if(ok==false)Icons.Filled.Warning else Icons.Filled.CheckCircle,null,tint=when(ok){true->MaterialTheme.colorScheme.primary;false->MaterialTheme.colorScheme.error;null->MaterialTheme.colorScheme.onSurfaceVariant});Spacer(Modifier.width(8.dp));Column{Text(label,fontWeight=FontWeight.Medium);Text(target,style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)}}}
@Composable private fun Event(e:CarEvent,resolve:()->Unit){Panel(e.summary,"${e.date} · ${e.subsystem.uppercase()} · ${e.kind.uppercase()}"){if(e.details.isNotBlank())Text(e.details);if(e.status!="resolved")OutlinedButton(resolve){Text("MARK RESOLVED")}}}
@Composable private fun Title(a:String,b:String){Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){Text(a,color=MaterialTheme.colorScheme.primary,fontWeight=FontWeight.Bold);Text(b,style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)}}
@Composable private fun Panel(title:String,sub:String,content:@Composable ColumnScope.()->Unit){Card(colors=CardDefaults.cardColors(containerColor=MaterialTheme.colorScheme.surfaceContainerLow)){Column(Modifier.padding(13.dp),verticalArrangement=Arrangement.spacedBy(8.dp)){Text(title,fontWeight=FontWeight.Bold,style=MaterialTheme.typography.titleMedium);if(sub.isNotBlank())Text(sub,style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant);content()}}}
