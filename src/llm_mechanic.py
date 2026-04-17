#!/usr/bin/env python3
"""
MZ1312 DRIFTER — LLM Mechanic
Conversational AI mechanic using local LLM (Ollama).
Integrates with live telemetry for contextual diagnosis.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import time
import signal
import logging
import threading
import paho.mqtt.client as mqtt
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

# Import the deterministic knowledge base
from mechanic import (
    VEHICLE_SPECS, COMMON_PROBLEMS, SERVICE_SCHEDULE,
    EMERGENCY_PROCEDURES, TORQUE_SPECS, FUSE_REFERENCE,
    DTC_REFERENCE, CRUISE_CONTROL_LOGIC, OWNER_VEHICLE_HISTORY,
    TELEMETRY_INTERPRETATION, CAN_ARCHITECTURE, AUSTRALIAN_SPECS,
    search as kb_search, get_dtc_info, get_telemetry_context
)
# Field operations knowledge base (emergency, RF, security, survival)
try:
    from field_ops_kb import search as field_ops_search
except ImportError:
    field_ops_search = None
# Tool execution engine (V3SP3R pattern)
try:
    from tool_executor import execute_tool, update_telemetry_cache
except ImportError:
    execute_tool = None
    update_telemetry_cache = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LLM-MECH] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════

from config import OLLAMA_HOST, OLLAMA_PORT, OLLAMA_MODEL, MQTT_HOST, MQTT_PORT

SYSTEM_PROMPT = """You are an expert diagnostic technician and mechanic specialising in the 2004 Jaguar X-Type 2.5L V6 (AJ-V6 engine). This is an Australian-delivered, right-hand-drive, AWD vehicle with the Jatco JF506E 5-speed automatic.

You have access to:
- Complete vehicle specifications (engine, transmission, drivetrain, fluids, electrical)
- Comprehensive DTC reference with causes and ECM actions for each code
- This car's specific history (known repairs, current symptoms, suspected issues)
- Common X-Type failure modes with detailed repair procedures
- Real-time telemetry data from the car's OBD-II/CAN bus system via DRIFTER
- Service schedules adjusted for Australian conditions
- Torque specifications, fuse/relay maps, CAN bus architecture
- Cruise control disable logic (which DTCs trigger "Cruise Unavailable")
- Telemetry interpretation guides (what sensor values mean)

YOUR APPROACH — THINK, DON'T JUST RECITE:
You have a comprehensive knowledge base, but you must REASON through problems, not just quote facts. Use the reference data combined with live telemetry to:
1. Form hypotheses based on the evidence (sensor values + DTCs + symptoms)
2. Consider multiple possible causes ranked by probability
3. Suggest targeted diagnostic tests to confirm/eliminate each hypothesis
4. Factor in THIS car's specific history (prior spark plug failure, valve cover leak, vacuum leak suspicion)
5. Explain your reasoning so the owner learns and can make informed decisions

Your personality: Direct, practical, experienced. You've worked on dozens of X-Types in Australian conditions. You know the common issues by heart. You give actionable advice with clear reasoning.

When responding:
1. Be specific to the X-Type — cite known weak points and how they relate to the current data
2. ALWAYS prioritise safety — flag anything dangerous immediately
3. Give difficulty ratings and cost estimates in AUD (Australian Dollars)
4. Reference live telemetry values when relevant ("Your coolant is at 95°C which suggests...")
5. Cross-reference DTCs with the knowledge base to explain what the ECM is doing and why
6. Consider interconnected failures (e.g., valve cover gasket → oil in plug wells → coil failure → misfire → cruise disabled)
7. If you need more info, ask targeted diagnostic questions

Current vehicle: 2004 Jaguar X-Type 2.5L V6 (AJ-V6), AWD, Jatco JF506E, Australian-spec RHD
Current date: {current_date}

You are also a field operations advisor running on Kali Linux with an RTL-SDR dongle.

Your additional domains:
- RF operations: spectrum analysis, signal identification, emergency frequencies, TPMS, ADS-B
- Kali Linux security tools: network recon, wireless analysis, Bluetooth scanning, RF tools
- Emergency/survival: off-grid comms, signaling, navigation, first aid, Australian conditions

You have tools available to execute commands, scan RF spectrum, scan networks, query knowledge
bases, and read vehicle telemetry. Use them when the user asks you to DO something, not just
explain something. Always explain what you're doing and what the results mean.

For security operations: you are authorized for defensive testing and reconnaissance on the
owner's own networks and equipment. Passive scanning is always acceptable.
"""

# ═══════════════════════════════════════════════════════════════════
#  Tool Definitions (Ollama native tool calling)
# ═══════════════════════════════════════════════════════════════════

DRIFTER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a system command on the Kali Linux Pi",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"},
                    "reason": {"type": "string", "description": "Why this command is needed"},
                },
                "required": ["command", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rf_scan",
            "description": "Scan RF spectrum or decode signals using RTL-SDR",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["spectrum", "decode_433", "listen_freq", "adsb", "emergency"]},
                    "frequency_mhz": {"type": "number", "description": "Target frequency in MHz (for listen_freq mode)"},
                    "duration_sec": {"type": "integer", "description": "Scan duration in seconds", "default": 10},
                },
                "required": ["mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "network_scan",
            "description": "Scan local network or wireless environment",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["discover", "port_scan", "wifi_list", "bluetooth", "arp"]},
                    "target": {"type": "string", "description": "Target IP/range (for port_scan mode)"},
                },
                "required": ["mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge",
            "description": "Search DRIFTER knowledge bases (vehicle, RF, emergency, security)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "domain": {"type": "string", "enum": ["vehicle", "rf", "emergency", "security", "all"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vehicle_telemetry",
            "description": "Get current live vehicle sensor data from OBD-II/CAN bus",
            "parameters": {
                "type": "object",
                "properties": {
                    "sensors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Sensor names: rpm, coolant, voltage, stft1, ltft1, maf, iat, throttle, speed, dtcs"
                    },
                },
                "required": ["sensors"],
            },
        },
    },
]

# ═══════════════════════════════════════════════════════════════════
#  Conversation Memory
# ═══════════════════════════════════════════════════════════════════

class ConversationMemory:
    """Simple conversation history for context."""
    
    def __init__(self, max_messages: int = 20):
        self.messages: List[Dict[str, str]] = []
        self.max_messages = max_messages
    
    def add(self, role: str, content: str):
        """Add a message to history."""
        self.messages.append({"role": role, "content": content})
        # Keep only recent messages
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
    
    def get_context(self) -> List[Dict[str, str]]:
        """Get conversation history for LLM context."""
        return self.messages.copy()
    
    def clear(self):
        """Clear conversation history."""
        self.messages.clear()


# ═══════════════════════════════════════════════════════════════════
#  Telemetry Context
# ═══════════════════════════════════════════════════════════════════

class TelemetryContext:
    """Maintains current vehicle state from MQTT."""
    
    def __init__(self):
        self.data: Dict[str, Any] = {}
        self.last_update: float = 0
        self.drive_session_start: Optional[float] = None
    
    def update(self, topic: str, data: dict):
        """Update telemetry from MQTT message."""
        key = topic.replace('drifter/', '').replace('/', '_')
        self.data[key] = data
        self.last_update = time.time()
        
        # Track drive session
        if 'session' in key and data.get('event') == 'start':
            self.drive_session_start = time.time()
    
    def get_summary(self) -> str:
        """Generate a human-readable summary of current telemetry."""
        lines = []
        
        # Engine basics
        rpm = self._get_value('engine_rpm')
        coolant = self._get_value('engine_coolant')
        speed = self._get_value('vehicle_speed')
        
        if rpm is not None:
            lines.append(f"Engine: {rpm:.0f} RPM")
        if coolant is not None:
            lines.append(f"Coolant: {coolant:.1f}°C")
        if speed is not None:
            lines.append(f"Speed: {speed:.0f} km/h")
        
        # Fuel trims
        stft1 = self._get_value('engine_stft1')
        stft2 = self._get_value('engine_stft2')
        if stft1 is not None and stft2 is not None:
            lines.append(f"Fuel trims: B1 {stft1:+.1f}%, B2 {stft2:+.1f}%")
        
        # Voltage
        voltage = self._get_value('power_voltage')
        if voltage is not None:
            lines.append(f"Battery: {voltage:.1f}V")
        
        # Current alert
        alert_msg = self._get_nested('alert_message', 'message')
        if alert_msg:
            lines.append(f"Current alert: {alert_msg}")
        
        # DTCs
        dtc_data = self.data.get('diag_dtc', {})
        stored = dtc_data.get('stored', [])
        if stored:
            lines.append(f"Active codes: {', '.join(stored)}")
        
        return "\n".join(lines) if lines else "No live data available"
    
    def _get_value(self, key: str) -> Optional[float]:
        """Get numeric value from telemetry."""
        data = self.data.get(key, {})
        return data.get('value') if isinstance(data, dict) else None
    
    def _get_nested(self, key: str, subkey: str) -> Optional[str]:
        """Get nested value from telemetry."""
        data = self.data.get(key, {})
        return data.get(subkey) if isinstance(data, dict) else None


# ═══════════════════════════════════════════════════════════════════
#  Ollama Client
# ═══════════════════════════════════════════════════════════════════

class OllamaClient:
    """Client for local Ollama LLM."""
    
    def __init__(self, host: str = OLLAMA_HOST, port: int = OLLAMA_PORT):
        self.base_url = f"http://{host}:{port}"
        self.model = OLLAMA_MODEL
        self.available = False
        self._check_connection()
    
    def _check_connection(self):
        """Check if Ollama is running."""
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/tags",
                method='GET'
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    self.available = True
                    log.info(f"Connected to Ollama at {self.base_url}")
        except Exception as e:
            log.warning(f"Ollama not available: {e}")
            self.available = False
    
    def generate(self, prompt: str, context: List[Dict] = None,
                 temperature: float = 0.7, max_tokens: int = 500,
                 tools: List[Dict] = None,
                 mqtt_client=None) -> str:
        """Generate response from LLM with optional tool calling.

        When *tools* is provided, enters a tool-calling loop:
        1. Send messages + tools to Ollama
        2. If model returns tool_calls, execute each via tool_executor
        3. Feed results back as 'tool' role messages
        4. Repeat until model returns a text response (no tool_calls)
        Max 5 iterations to prevent infinite loops.
        """
        import urllib.request
        from config import OLLAMA_TIMEOUT

        if not self.available:
            return "[LLM offline - install Ollama and pull model]"

        messages = []

        # Add system prompt with current date
        sys_prompt = SYSTEM_PROMPT.format(current_date=datetime.now().strftime("%Y-%m-%d"))
        messages.append({"role": "system", "content": sys_prompt})

        # Add conversation context
        if context:
            messages.extend(context)

        # Add current prompt
        messages.append({"role": "user", "content": prompt})

        max_iterations = 5
        for iteration in range(max_iterations):
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens
                }
            }
            if tools and execute_tool:
                payload["tools"] = tools

            try:
                req = urllib.request.Request(
                    f"{self.base_url}/api/chat",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                    method='POST'
                )

                with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
                    result = json.loads(resp.read().decode())

                msg = result.get('message', {})
                tool_calls = msg.get('tool_calls', [])

                # If no tool calls, return the text response
                if not tool_calls:
                    return msg.get('content', '[No response]')

                # Process tool calls
                messages.append(msg)  # Add assistant message with tool_calls
                for tc in tool_calls:
                    fn = tc.get('function', {})
                    tool_name = fn.get('name', '')
                    tool_args = fn.get('arguments', {})
                    log.info(f"Tool call: {tool_name}({json.dumps(tool_args)[:100]})")

                    tool_result = execute_tool(
                        tool_name, tool_args, mqtt_client=mqtt_client
                    )
                    # Add tool result as 'tool' role message
                    messages.append({
                        "role": "tool",
                        "content": json.dumps(tool_result),
                    })
                    log.info(f"Tool result: {tool_result.get('risk_level', '?')} "
                             f"success={tool_result.get('success', '?')}")

            except Exception as e:
                log.error(f"LLM generation failed: {e}")
                return f"[Error: {str(e)}]"

        return "[Tool calling limit reached - please rephrase your request]"


# ═══════════════════════════════════════════════════════════════════
#  Knowledge Base RAG (Retrieval Augmented Generation)
# ═══════════════════════════════════════════════════════════════════

class MechanicRAG:
    """Retrieval system for mechanic knowledge base.

    Enhanced to search across all knowledge domains:
    problems, DTCs, owner history, telemetry guides,
    cruise logic, service schedule, and electrical reference.
    """

    def __init__(self):
        self.problem_index = {p['title'].lower(): p for p in COMMON_PROBLEMS}
        self.tag_index = {}
        for p in COMMON_PROBLEMS:
            for tag in p.get('tags', []):
                if tag not in self.tag_index:
                    self.tag_index[tag] = []
                self.tag_index[tag].append(p)

    def retrieve(self, query: str) -> str:
        """Retrieve relevant knowledge for a query."""
        query_lower = query.lower()
        results = []
        seen_titles = set()

        # Search by tags (highest priority for known problems)
        for tag, problems in self.tag_index.items():
            if tag in query_lower:
                for p in problems:
                    title = p['title']
                    if title not in seen_titles:
                        results.append(self._format_problem(p))
                        seen_titles.add(title)

        # Search by title keywords
        for title, problem in self.problem_index.items():
            if any(word in title for word in query_lower.split()):
                if problem['title'] not in seen_titles:
                    results.append(self._format_problem(problem))
                    seen_titles.add(problem['title'])

        # Search DTC reference for any P-codes mentioned
        import re
        dtc_matches = re.findall(r'[pPuUcCbB]\d{4}', query)
        for code in dtc_matches:
            info = get_dtc_info(code)
            if info:
                results.append(self._format_dtc(info))

        # Check owner vehicle history for relevance
        for symptom in OWNER_VEHICLE_HISTORY.get('current_symptoms', []):
            if any(word in symptom['details'].lower() for word in query_lower.split()):
                results.append(f"THIS CAR'S ACTIVE ISSUE: {symptom['symptom']}\n{symptom['details']}")

        # Check cruise control logic if relevant
        cruise_terms = ['cruise', 'unavailable', 'speed control']
        if any(t in query_lower for t in cruise_terms):
            results.append(self._format_cruise_logic())

        # General search across all knowledge
        kb_results = kb_search(query)
        for r in kb_results[:5]:
            title = r.get('title', '')
            if title not in seen_titles:
                if r['type'] == 'problem':
                    results.append(self._format_problem(r['data']))
                elif r['type'] == 'dtc':
                    results.append(self._format_dtc(r['data']))
                elif r['type'] == 'owner_history':
                    results.append(f"OWNER HISTORY: {r['data'].get('issue', '')}\n{r['data'].get('details', '')}")
                elif r['type'] == 'owner_symptom':
                    results.append(f"ACTIVE ISSUE: {r['data'].get('symptom', '')}\n{r['data'].get('details', '')}")
                elif r['type'] == 'telemetry_guide':
                    results.append(f"TELEMETRY GUIDE: {title}\n{self._format_dict(r['data'])}")
                elif r['type'] in ('torque', 'fuse', 'spec', 'service'):
                    results.append(f"{r['type'].upper()}: {title}")
                elif r['type'] == 'cruise_logic':
                    results.append(self._format_cruise_logic())
                seen_titles.add(title)

        if results:
            return "\n\n---\n\n".join(results[:5])  # Up to 5 context entries
        return "No specific knowledge base entries found."

    def _format_problem(self, p: dict) -> str:
        """Format a problem for LLM context."""
        lines = [f"KNOWN ISSUE: {p['title']}"]
        lines.append(f"Symptoms: {', '.join(p['symptoms'])}")
        lines.append(f"Cause: {p['cause']}")
        lines.append(f"Fix: {p['fix']}")
        if p.get('diagnostic_test'):
            lines.append(f"Diagnostic Test: {p['diagnostic_test']}")
        if p.get('viton_upgrade'):
            lines.append(f"Upgrade Note: {p['viton_upgrade']}")
        lines.append(f"Parts: {', '.join(p.get('parts', []))}")
        lines.append(f"Difficulty: {p.get('difficulty', 'Unknown')}")
        lines.append(f"Cost: {p.get('cost', 'Unknown')}")
        if p.get('related_dtcs'):
            lines.append(f"Related DTCs: {', '.join(p['related_dtcs'])}")
        return '\n'.join(lines)

    def _format_dtc(self, info: dict) -> str:
        """Format a DTC entry for LLM context."""
        code = info.get('code', '?')
        desc = info.get('desc', '')
        action = info.get('action', '')
        causes = info.get('causes', [])
        return (f"DTC {code}: {desc}\n"
                f"ECM Action: {action}\n"
                f"Possible Causes: {', '.join(causes)}")

    def _format_cruise_logic(self) -> str:
        """Format cruise control logic for LLM context."""
        cl = CRUISE_CONTROL_LOGIC
        faults = '\n'.join(f"  - {f}" for f in cl['triggering_faults'])
        steps = '\n'.join(cl['resolution_steps'])
        return (f"CRUISE CONTROL DISABLE LOGIC:\n{cl['description']}\n\n"
                f"Triggering faults:\n{faults}\n\n"
                f"Resolution:\n{steps}\n\n"
                f"Note: {cl['note']}")

    def _format_dict(self, d: dict) -> str:
        """Format a dict as key: value lines."""
        return '\n'.join(f"  {k}: {v}" for k, v in d.items()
                        if not k.startswith('_'))


# ═══════════════════════════════════════════════════════════════════
#  Main LLM Mechanic Service
# ═══════════════════════════════════════════════════════════════════

class LLMMechanic:
    """Main service integrating LLM with vehicle telemetry."""
    
    # Maximum number of LLM queries processed concurrently. Protects against
    # a flood of `drifter/llm/query` messages spawning unbounded daemon
    # threads and exhausting memory / the Ollama socket.
    MAX_CONCURRENT_QUERIES = 3

    def __init__(self):
        self.telemetry = TelemetryContext()
        self.memory = ConversationMemory()
        self.llm = OllamaClient()
        self.rag = MechanicRAG()
        self.running = True
        self._stop_event = threading.Event()
        self._query_semaphore = threading.BoundedSemaphore(
            self.MAX_CONCURRENT_QUERIES
        )

        # MQTT client
        self.mqtt = mqtt.Client(client_id="drifter-llm-mechanic")
        self.mqtt.on_message = self._on_mqtt_message

    def stop(self):
        """Signal the main loop to exit promptly without busy-waiting."""
        self.running = False
        self._stop_event.set()

    def _on_mqtt_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        try:
            data = json.loads(msg.payload)
            topic = msg.topic

            # Update telemetry
            self.telemetry.update(topic, data)
            if update_telemetry_cache:
                update_telemetry_cache(topic, msg.payload)

            # Handle LLM queries — spawn a daemon thread so the MQTT loop
            # is never blocked.  asyncio.create_task() cannot be used here
            # because this callback runs in the paho network thread, not in
            # any asyncio event loop.  A bounded semaphore caps concurrent
            # queries so a flood of messages cannot exhaust resources.
            if topic == 'drifter/llm/query':
                query = data.get('query', '')
                session_id = data.get('session_id', 'default')
                if not self._query_semaphore.acquire(blocking=False):
                    log.warning("Query dropped — %d already in flight",
                                self.MAX_CONCURRENT_QUERIES)
                    self.mqtt.publish('drifter/llm/response', json.dumps({
                        'query': query,
                        'response': 'Busy — try again in a moment.',
                        'session_id': session_id,
                        'timestamp': time.time(),
                        'dropped': True,
                    }))
                    return
                threading.Thread(
                    target=self._process_query_wrapped,
                    args=(query, session_id),
                    daemon=True,
                ).start()

        except Exception as e:
            log.warning(f"MQTT message error: {e}")

    def _process_query_wrapped(self, query: str, session_id: str):
        """Wrapper that guarantees the concurrency semaphore is released."""
        try:
            self._process_query(query, session_id)
        except Exception:
            log.exception("Query processing failed")
        finally:
            self._query_semaphore.release()

    def _process_query(self, query: str, session_id: str):
        """Process a user query with full context (runs in a worker thread)."""
        log.info(f"Query: {query[:50]}...")

        # Build context
        context_parts = []

        # Add telemetry summary
        telem = self.telemetry.get_summary()
        if telem:
            context_parts.append(f"CURRENT VEHICLE STATE:\n{telem}")

        # Add relevant knowledge base entries (vehicle)
        kb_context = self.rag.retrieve(query)
        if kb_context:
            context_parts.append(f"RELEVANT KNOWLEDGE:\n{kb_context}")

        # Add field ops knowledge (RF, emergency, security, survival)
        if field_ops_search:
            field_results = field_ops_search(query)
            for r in field_results[:3]:
                entry = r['data']
                parts = [f"FIELD OPS: {entry['title']}"]
                parts.append(entry.get('content', '')[:500])
                if entry.get('commands'):
                    parts.append(f"Commands: {'; '.join(entry['commands'][:3])}")
                context_parts.append('\n'.join(parts))

        # Build full prompt
        full_prompt = f"""{query}

---

{chr(10).join(context_parts)}"""

        # Get conversation history
        conv_history = self.memory.get_context()

        # Generate response with tool calling
        response = self.llm.generate(
            prompt=full_prompt,
            context=conv_history,
            temperature=0.7,
            tools=DRIFTER_TOOLS,
            mqtt_client=self.mqtt,
        )
        
        # Update memory
        self.memory.add("user", query)
        self.memory.add("assistant", response)
        
        # Publish response
        self.mqtt.publish('drifter/llm/response', json.dumps({
            'query': query,
            'response': response,
            'session_id': session_id,
            'timestamp': time.time(),
            'has_telemetry': bool(telem)
        }))
        
        log.info(f"Response sent ({len(response)} chars)")
    
    def start(self):
        """Start the service."""
        log.info("Starting LLM Mechanic service...")
        
        # Connect to MQTT
        connected = False
        while not connected and self.running:
            try:
                self.mqtt.connect(MQTT_HOST, MQTT_PORT, 60)
                connected = True
                log.info("Connected to MQTT broker")
            except Exception as e:
                log.warning(f"MQTT connection failed: {e}")
                time.sleep(3)
        
        # Subscribe to topics
        self.mqtt.subscribe("drifter/#")
        self.mqtt.loop_start()
        
        log.info("LLM Mechanic is LIVE")
        log.info(f"Model: {OLLAMA_MODEL}")
        log.info("Send queries to: drifter/llm/query")

        # Block on an Event rather than polling with sleep(0.1) — the old
        # loop wasted CPU and added up to 100ms of shutdown latency.
        self._stop_event.wait()

        # Cleanup
        self.mqtt.loop_stop()
        self.mqtt.disconnect()
        log.info("LLM Mechanic stopped")


def main():
    """Entry point."""
    import signal
    
    service = LLMMechanic()

    def handle_signal(sig, frame):
        service.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    service.start()


if __name__ == '__main__':
    main()
