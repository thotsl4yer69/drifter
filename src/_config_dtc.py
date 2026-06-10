#!/usr/bin/env python3
"""
MZ1312 DRIFTER — DTC lookup table (pure data).

Extracted from config.py to keep the central config module lean. Plain-English
diagnosis for 2004 Jaguar X-Type / AJ-V6 fault codes, with X-Type-specific
known causes. No logic, no imports — config.py re-imports XTYPE_DTC_LOOKUP so
the public API (`config.XTYPE_DTC_LOOKUP` / `from config import XTYPE_DTC_LOOKUP`)
is unchanged.

UNCAGED TECHNOLOGY — EST 1991
"""

# ═══════════════════════════════════════════════════════════════════
#  DTC Lookup — 2004 Jaguar X-Type / AJ-V6 Specific
#  Plain-English diagnosis with X-Type known causes
# ═══════════════════════════════════════════════════════════════════
XTYPE_DTC_LOOKUP = {
    # Fuel system
    'P0171': {
        'desc': 'System Too Lean — Bank 1',
        'cause': 'Intake manifold gasket leak, cracked PCV valve diaphragm, '
                 'dirty MAF sensor, or vacuum hose off the brake booster.',
        'action': 'Smoke test intake, clean MAF with electronics cleaner, '
                  'check PCV valve on top of valve cover Bank 1.',
        'severity': 'AMBER',
    },
    'P0174': {
        'desc': 'System Too Lean — Bank 2',
        'cause': 'Same as P0171 but Bank 2 side. On the X-Type the Bank 2 '
                 'intake runner seals are harder to access.',
        'action': 'Smoke test intake. If both P0171+P0174 appear together, '
                  'suspect the upper intake plenum gasket or large shared vacuum leak.',
        'severity': 'AMBER',
    },
    'P0172': {
        'desc': 'System Too Rich — Bank 1',
        'cause': 'Leaking fuel injector, stuck-open purge valve (common on X-Type), '
                 'or failing upstream O2 sensor Bank 1.',
        'action': 'Check purge valve on firewall side. Pull injector rail and '
                  'look for drippers. Test O2 sensor heater resistance.',
        'severity': 'AMBER',
    },
    'P0175': {
        'desc': 'System Too Rich — Bank 2',
        'cause': 'Same as P0172 but Bank 2. Also check for coolant leaking '
                 'into cylinder (head gasket weep) on the rear bank.',
        'action': 'Inspect spark plugs for fouling. White/steam residue = coolant leak.',
        'severity': 'AMBER',
    },

    # Misfires — often coil packs on this engine
    'P0300': {
        'desc': 'Random/Multiple Cylinder Misfire',
        'cause': 'On the AJ-V6 this is usually failing coil packs (COP), worn plugs, '
                 'or a vacuum leak affecting multiple cylinders.',
        'action': 'Swap coil packs between cylinders and see if misfire follows. '
                  'Replace all 6 plugs if over 30k miles. Check for vacuum leaks.',
        'severity': 'AMBER',
    },
    'P0301': {
        'desc': 'Cylinder 1 Misfire',
        'cause': 'Coil pack failure is the #1 cause on X-Type. Cylinder 1 is '
                 'front-left (Bank 1, nearest radiator).',
        'action': 'Swap coil from Cyl 1 to Cyl 4. If misfire moves → replace coil. '
                  'If not → check plug, compression, injector.',
        'severity': 'AMBER',
    },
    'P0302': {
        'desc': 'Cylinder 2 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 2 is mid Bank 1.',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },
    'P0303': {
        'desc': 'Cylinder 3 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 3 is rear Bank 1.',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },
    'P0304': {
        'desc': 'Cylinder 4 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 4 is front Bank 2 (nearest alternator).',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },
    'P0305': {
        'desc': 'Cylinder 5 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 5 is mid Bank 2.',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },
    'P0306': {
        'desc': 'Cylinder 6 Misfire',
        'cause': 'Coil pack or spark plug. Cyl 6 is rear Bank 2 (hardest to access).',
        'action': 'Swap coil to another cylinder and retest.',
        'severity': 'AMBER',
    },

    # Sensors
    'P0340': {
        'desc': 'Camshaft Position Sensor A — Bank 1',
        'cause': 'CMP sensor failure or wiring corrosion. Common on ageing X-Types. '
                 'Located behind the timing cover on Bank 1 side.',
        'action': 'Replace CMP sensor (cheap part). Check connector for green corrosion.',
        'severity': 'RED',
    },
    'P0345': {
        'desc': 'Camshaft Position Sensor A — Bank 2',
        'cause': 'Same as P0340 but Bank 2 side.',
        'action': 'Replace CMP sensor Bank 2. Check for coolant contamination '
                  'from thermostat housing leak (they are close together).',
        'severity': 'RED',
    },

    # Catalyst
    'P0420': {
        'desc': 'Catalyst Efficiency Below Threshold — Bank 1',
        'cause': 'Catalytic converter degraded, or downstream O2 sensor lazy. '
                 'UK MOT relevant. On X-Type often caused by prolonged rich running '
                 'from a bad coil pack fouling the cat.',
        'action': 'Fix any upstream fuel trim or misfire codes FIRST. '
                  'Then clear and retest. If cat is truly dead, budget $400-800 for replacement.',
        'severity': 'AMBER',
    },
    'P0430': {
        'desc': 'Catalyst Efficiency Below Threshold — Bank 2',
        'cause': 'Same as P0420 but Bank 2. The X-Type has 2 pre-cats and 1 main cat.',
        'action': 'Same approach — fix fuel/ignition first, then re-evaluate cat health.',
        'severity': 'AMBER',
    },

    # EGR / Purge
    'P0401': {
        'desc': 'EGR Flow Insufficient',
        'cause': 'Carbon buildup in EGR passages (very common on X-Type). '
                 'EGR valve sticking or vacuum actuator leaking.',
        'action': 'Remove and clean EGR valve. Clean EGR passages with carb cleaner. '
                  'Check vacuum hoses to EGR actuator.',
        'severity': 'AMBER',
    },
    'P0443': {
        'desc': 'EVAP Purge Control Valve Circuit',
        'cause': 'Purge valve solenoid failed or wiring fault. Located on the '
                 'firewall side of the engine bay.',
        'action': 'Test purge valve with 12V — should click. Replace if stuck open '
                  '(causes rich condition) or stuck closed (fuel tank pressure).',
        'severity': 'AMBER',
    },

    # Idle / Throttle — X-Type uses electronic throttle body (drive-by-wire)
    'P0507': {
        'desc': 'Idle Air Control RPM Higher Than Expected',
        'cause': 'Vacuum leak, dirty throttle body, or sticking IAC. On X-Type '
                 'the electronic throttle body gets carbon buildup inside.',
        'action': 'Clean throttle body with carb cleaner (remove to clean properly). '
                  'Then do idle relearn: key on 30s, start, idle 2 min, drive 10 min.',
        'severity': 'AMBER',
    },
    'P1000': {
        'desc': 'OBD-II System Readiness Not Complete',
        'cause': 'Not a fault — monitors have not run since last battery disconnect '
                 'or code clear. Normal after work.',
        'action': 'Drive a mixed cycle: cold start, idle 2 min, accelerate to 60 mph, '
                  'cruise 5 min, decelerate with foot off gas. Monitors will complete.',
        'severity': 'INFO',
    },

    # O2 Sensors
    'P0131': {
        'desc': 'O2 Sensor Low Voltage — Bank 1 Sensor 1 (upstream)',
        'cause': 'Upstream O2 sensor degraded or exhaust leak before sensor. '
                 'On X-Type, check the flex joint near the manifold for cracks.',
        'action': 'Check exhaust for leaks at manifold-to-flex joint. '
                  'Test O2 heater fuse. Replace sensor if >80k miles.',
        'severity': 'AMBER',
    },
    'P1131': {
        'desc': 'O2 Sensor Lack of Switching — Bank 1',
        'cause': 'Upstream O2 sensor stuck lean. Common on ageing X-Types. '
                 'Can also be triggered by persistent vacuum leak.',
        'action': 'Fix vacuum leaks first. If still present, replace Bank 1 sensor 1.',
        'severity': 'AMBER',
    },
    'P1151': {
        'desc': 'O2 Sensor Lack of Switching — Bank 2',
        'cause': 'Same as P1131 but Bank 2 side.',
        'action': 'Fix vacuum leaks first, then replace Bank 2 sensor 1 if needed.',
        'severity': 'AMBER',
    },

    # Fuel pump
    'P1235': {
        'desc': 'Fuel Pump Control Out of Range',
        'cause': 'Fuel pump relay failing, wiring fault, or fuel pump itself wearing out. '
                 'On X-Type the pump is in the tank, access via rear seat.',
        'action': 'Check fuel pump relay in engine bay fuse box first (swap with identical relay). '
                  'Listen for pump prime when turning key to ON. '
                  'Check fuel pressure at rail (spec: 3.0-3.5 bar).',
        'severity': 'RED',
    },

    # Throttle body (drive-by-wire specific to X-Type)
    'P1518': {
        'desc': 'Intake Manifold Runner Control Stuck Open',
        'cause': 'IMRC actuator failure or vacuum leak to the runner control. '
                 'Affects power band above 3500 RPM.',
        'action': 'Check vacuum hose to IMRC actuator. Test actuator with vacuum pump. '
                  'Common on high-mileage X-Types.',
        'severity': 'AMBER',
    },
    'P2106': {
        'desc': 'Throttle Actuator — Forced Limited Power',
        'cause': 'PCM has put the engine in limp mode. Usually triggered by another '
                 'fault code. The X-Type throttle body motor can fail internally.',
        'action': 'LIMP MODE. Read ALL codes — fix the root cause. '
                  'If throttle body related, try cleaning first before replacing ($250+ part).',
        'severity': 'RED',
    },
    'P2111': {
        'desc': 'Throttle Actuator Stuck Open',
        'cause': 'Throttle plate sticking or motor failure. Carbon buildup is '
                 'the usual cause on X-Type.',
        'action': 'Remove and clean throttle body. Check for scored bore. '
                  'After refitting: idle relearn procedure required.',
        'severity': 'RED',
    },
    'P2112': {
        'desc': 'Throttle Actuator Stuck Closed',
        'cause': 'Throttle plate jammed shut. Will cause loss of power or no-start.',
        'action': 'Emergency: key off/on may reset. Clean or replace throttle body. '
                  'Do NOT force the plate open with tools.',
        'severity': 'RED',
    },
    'P2135': {
        'desc': 'Throttle Position Sensor Correlation',
        'cause': 'The two TPS signals inside the throttle body disagree. '
                 'Wiring fault or internal throttle body failure.',
        'action': 'Check TPS connector for corrosion. Wiggle-test wiring. '
                  'If intermittent, throttle body replacement likely needed.',
        'severity': 'RED',
    },

    # Communication
    'U0100': {
        'desc': 'Lost Communication with ECM/PCM',
        'cause': 'CAN bus wiring fault, ECM power supply issue, or ECM failure. '
                 'On X-Type, check the main engine fuse box and ECM connector.',
        'action': 'Check battery voltage. Inspect ECM connector (behind glovebox). '
                  'Check CAN bus termination with multimeter (should be ~60 ohms).',
        'severity': 'RED',
    },
    'U0121': {
        'desc': 'Lost Communication with ABS Module',
        'cause': 'ABS module failure or CAN bus fault. The X-Type ABS module '
                 'is known to fail internally (common issue).',
        'action': 'Check ABS fuse first. If module dead, specialist rebuild '
                  '(BBA Reman, ECU Testing) typically $250-400.',
        'severity': 'AMBER',
    },
}
