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
    search as kb_search
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [LLM-MECH] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════

OLLAMA_HOST = "localhost"
OLLAMA_PORT = 11434
OLLAMA_MODEL = "llama3.2:3b"  # 3B parameter model, fits on Pi 5
# Alternatives: "phi3:3.8b", "qwen2.5:3b", "gemma2:2b"

SYSTEM_PROMPT = """You are an expert automotive mechanic specializing in the 2004 Jaguar X-Type 2.5L V6 (AJ-V6 engine). 

You have access to:
- Complete vehicle specifications (engine, transmission, fluids, capacities)
- Common failure modes and repair procedures for this specific vehicle
- Real-time telemetry data from the car's OBD-II system
- Service schedules and torque specifications

Your personality: Direct, practical, experienced. You've worked on dozens of X-Types. You know the common issues by heart. You give actionable advice, not vague suggestions.

When responding:
1. Be specific to the X-Type — mention known weak points (thermostat housing, coil packs, etc.)
2. Prioritize safety — flag anything dangerous immediately
3. Give difficulty ratings and rough cost estimates in GBP
4. If you need more info, ask targeted questions
5. Reference the live telemetry when relevant ("Your coolant is at 95°C which suggests...")

Current vehicle: 2004 Jaguar X-Type 2.5L V6 (AJ-V6)
Current date: {current_date}
"""

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
                 temperature: float = 0.7, max_tokens: int = 500) -> str:
        """Generate response from LLM."""
        import urllib.request
        
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
        
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }
        
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())
                return result.get('message', {}).get('content', '[No response]')
                
        except Exception as e:
            log.error(f"LLM generation failed: {e}")
            return f"[Error: {str(e)}]"


# ═══════════════════════════════════════════════════════════════════
#  Knowledge Base RAG (Retrieval Augmented Generation)
# ═══════════════════════════════════════════════════════════════════

class MechanicRAG:
    """Retrieval system for mechanic knowledge base."""
    
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
        
        # Search by tags
        for tag, problems in self.tag_index.items():
            if tag in query_lower:
                for p in problems:
                    results.append(self._format_problem(p))
        
        # Search by title
        for title, problem in self.problem_index.items():
            if any(word in title for word in query_lower.split()):
                if problem not in results:
                    results.append(self._format_problem(problem))
        
        # General search
        kb_results = kb_search(query)
        for r in kb_results[:3]:
            if r['type'] == 'problem':
                results.append(self._format_problem(r['data']))
        
        if results:
            return "\n\n---\n\n".join(results[:3])  # Limit context
        return "No specific knowledge base entries found."
    
    def _format_problem(self, p: dict) -> str:
        """Format a problem for LLM context."""
        return f"""KNOWN ISSUE: {p['title']}
Symptoms: {', '.join(p['symptoms'])}
Cause: {p['cause']}
Fix: {p['fix']}
Parts: {', '.join(p.get('parts', []))}
Difficulty: {p.get('difficulty', 'Unknown')}
Cost: {p.get('cost', 'Unknown')}"""


# ═══════════════════════════════════════════════════════════════════
#  Main LLM Mechanic Service
# ═══════════════════════════════════════════════════════════════════

class LLMMechanic:
    """Main service integrating LLM with vehicle telemetry."""
    
    def __init__(self):
        self.telemetry = TelemetryContext()
        self.memory = ConversationMemory()
        self.llm = OllamaClient()
        self.rag = MechanicRAG()
        self.running = True
        
        # MQTT client
        self.mqtt = mqtt.Client(client_id="drifter-llm-mechanic")
        self.mqtt.on_message = self._on_mqtt_message

    def _on_mqtt_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        try:
            data = json.loads(msg.payload)
            topic = msg.topic

            # Update telemetry
            self.telemetry.update(topic, data)

            # Handle LLM queries — spawn a daemon thread so the MQTT loop
            # is never blocked.  asyncio.create_task() cannot be used here
            # because this callback runs in the paho network thread, not in
            # any asyncio event loop.
            if topic == 'drifter/llm/query':
                query = data.get('query', '')
                session_id = data.get('session_id', 'default')
                threading.Thread(
                    target=self._process_query,
                    args=(query, session_id),
                    daemon=True,
                ).start()

        except Exception as e:
            log.warning(f"MQTT message error: {e}")

    def _process_query(self, query: str, session_id: str):
        """Process a user query with full context (runs in a worker thread)."""
        log.info(f"Query: {query[:50]}...")
        
        # Build context
        context_parts = []
        
        # Add telemetry summary
        telem = self.telemetry.get_summary()
        if telem:
            context_parts.append(f"CURRENT VEHICLE STATE:\n{telem}")
        
        # Add relevant knowledge base entries
        kb_context = self.rag.retrieve(query)
        if kb_context:
            context_parts.append(f"RELEVANT KNOWLEDGE:\n{kb_context}")
        
        # Build full prompt
        full_prompt = f"""{query}

---

{chr(10).join(context_parts)}"""
        
        # Get conversation history
        conv_history = self.memory.get_context()
        
        # Generate response
        response = self.llm.generate(
            prompt=full_prompt,
            context=conv_history,
            temperature=0.7
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
                self.mqtt.connect("localhost", 1883, 60)
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
        
        # Keep running
        while self.running:
            time.sleep(0.1)
        
        # Cleanup
        self.mqtt.loop_stop()
        self.mqtt.disconnect()
        log.info("LLM Mechanic stopped")


def main():
    """Entry point."""
    import signal
    
    service = LLMMechanic()
    
    def handle_signal(sig, frame):
        service.running = False
    
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    
    service.start()


if __name__ == '__main__':
    main()
