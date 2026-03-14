#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Offline Mechanical Advisor
Complete 2004 Jaguar X-Type 2.5L V6 knowledge base.
No internet needed. Searchable from the dashboard.
UNCAGED TECHNOLOGY — EST 1991
"""

# ═══════════════════════════════════════════════════════════════════
#  Vehicle Specs — 2004 Jaguar X-Type 2.5L V6 (AJ-V6 / Duratec)
# ═══════════════════════════════════════════════════════════════════

VEHICLE_SPECS = {
    'engine': {
        'name': 'AJ-V6 (Ford Duratec 25)',
        'code': 'AJ-V6 / Duratec 25',
        'displacement': '2495cc (2.5L)',
        'bore_stroke': '82.4mm x 79.5mm',
        'compression': '10.0:1',
        'power': '194 bhp @ 6800 RPM',
        'torque': '245 Nm (181 lb-ft) @ 3500 RPM',
        'valvetrain': 'DOHC 24-valve (4 valves per cylinder)',
        'firing_order': '1-4-2-5-3-6',
        'ignition': 'Coil-on-plug (COP) — 6 individual coils',
        'fuel_system': 'Sequential multi-point injection (SEFI)',
        'fuel_pressure': '3.0-3.5 bar (43-51 PSI)',
        'idle_rpm': '650-780 RPM (warm)',
        'cold_idle': '1000-1400 RPM (below 40°C)',
        'oil_capacity': '5.5 litres with filter',
        'oil_spec': '5W-30 Ford WSS-M2C913-A/B (or Castrol Magnatec 5W-30)',
        'spark_plugs': 'Motorcraft AGSF-32PM (or NGK ITR6F-13, gapped 1.3mm)',
        'coolant_capacity': '7.5 litres (inc. heater)',
        'coolant_type': 'Ford Supercharged coolant (orange OAT)',
        'thermostat_opens': '88°C, fully open 97°C',
        'timing': 'Chain driven (no belt to replace)',
    },
    'transmission': {
        'type': 'Jatco JF506E 5-speed automatic',
        'fluid': 'Mercon V ATF (Ford XT-5-QMC)',
        'capacity': '9.5 litres total, 4.0 litres drain-and-fill',
        'drain_interval': 'Every 60,000 miles',
        'filter': 'Internal — replace at fluid change',
        'notes': 'NO lifetime fill despite Jaguar claim. Change fluid or it will fail.',
    },
    'drivetrain': {
        'type': 'All-Wheel Drive (AWD)',
        'coupling': 'Haldex electro-hydraulic coupling',
        'split': '95/5 front/rear (normal), up to 50/50 under slip',
        'haldex_fluid': 'Haldex AOC fluid (every 40,000 miles)',
        'rear_diff': 'Integral with Haldex unit',
        'propshaft': 'Two-piece with centre bearing (known wear item)',
    },
    'brakes': {
        'front': '300mm ventilated discs, single-piston floating caliper',
        'rear': '280mm solid discs, single-piston floating caliper',
        'handbrake': 'Cable-operated drum-in-disc (rear)',
        'fluid': 'DOT 4 (Ford PM-1-C)',
        'pad_thickness_min': '2mm',
        'disc_min_front': '278mm diameter, 22mm thickness',
        'disc_min_rear': '258mm diameter, 8mm thickness',
    },
    'suspension': {
        'front': 'MacPherson strut, lower wishbone, anti-roll bar',
        'rear': 'Multi-link, coil springs, anti-roll bar',
        'alignment_front_camber': '-0.75° ± 0.75°',
        'alignment_front_toe': '0.1° ± 0.1° per side',
        'alignment_rear_camber': '-1.25° ± 0.75°',
        'alignment_rear_toe': '0.15° ± 0.15° per side',
    },
    'electrical': {
        'battery': '12V, 70Ah (Group 48/H6)',
        'alternator': '130A',
        'voltage_running': '13.8-14.4V',
        'voltage_key_on': '12.4-12.7V (healthy battery)',
        'ecu': 'Ford EEC-V (PCM located behind glovebox)',
        'can_bus': 'ISO 11898 (medium speed, 125kbps body / 500kbps powertrain)',
        'obd_protocol': 'ISO 15765-4 CAN (11-bit, 500kbps)',
    },
    'tires_wheels': {
        'factory_size': '205/55R16',
        'pressure_front': '30 PSI (2.1 bar)',
        'pressure_rear': '30 PSI (2.1 bar)',
        'wheel_torque': '100 Nm (74 lb-ft)',
        'bolt_pattern': '5x108',
        'centre_bore': '63.4mm',
        'offset': 'ET52.5',
    },
    'fluids_capacities': {
        'engine_oil': '5.5L — 5W-30',
        'coolant': '7.5L — Orange OAT',
        'transmission': '9.5L total — Mercon V',
        'brake_fluid': 'DOT 4',
        'power_steering': 'Mercon V ATF',
        'washer': 'Any screenwash',
        'fuel_tank': '61 litres',
        'fuel_type': 'Unleaded 95 RON minimum',
    },
}


# ═══════════════════════════════════════════════════════════════════
#  Common Problems & Fixes — X-Type Specific
# ═══════════════════════════════════════════════════════════════════

COMMON_PROBLEMS = [
    {
        'title': 'Thermostat Housing Failure',
        'symptoms': ['Coolant loss with no visible leak', 'Temperature gauge swings',
                     'Overheating then cooling repeatedly', 'Sweet smell from engine bay'],
        'cause': 'The plastic thermostat housing behind the timing cover develops hairline cracks. '
                 'This is the #1 most common X-Type fault. The housing sits between the V of the engine '
                 'and is subjected to constant heat cycling.',
        'fix': 'Replace with aluminium aftermarket housing (£25-40 from eBay). The plastic OEM part '
               'will fail again. Requires removing the intake manifold for access. Budget 3-4 hours.',
        'parts': ['Thermostat housing (aluminium)', 'Thermostat (88°C)', 'New O-ring seals', 'Coolant 7.5L'],
        'difficulty': 'Medium — need to remove intake manifold',
        'cost': '£40-80 parts, £200-300 if garage does it',
        'tags': ['coolant', 'overheating', 'thermostat', 'leak', 'temperature'],
    },
    {
        'title': 'Coil Pack Failure',
        'symptoms': ['Misfire at idle or under load', 'Rough running', 'Check engine light',
                     'P0301-P0306 codes', 'Loss of power', 'Hesitation on acceleration'],
        'cause': 'The COP (coil-on-plug) coils on the AJ-V6 degrade over time due to heat and vibration. '
                 'They develop internal cracks in the insulation, causing intermittent spark failure. '
                 'Often worse in damp weather.',
        'fix': 'Swap the suspect coil to a different cylinder. If the misfire follows, replace that coil. '
               'Recommended: replace all 6 at once with new plugs. Use Motorcraft or equivalent quality.',
        'parts': ['6x coil packs (Motorcraft DG513 or equivalent)', '6x spark plugs (AGSF-32PM, gapped 1.3mm)'],
        'difficulty': 'Easy — 30 minutes for all 6',
        'cost': '£60-120 for 6 coils + plugs',
        'tags': ['misfire', 'coil', 'spark', 'rough', 'hesitation', 'p0301', 'p0302', 'p0303',
                 'p0304', 'p0305', 'p0306', 'p0300'],
    },
    {
        'title': 'Vacuum Leak (Intake Manifold Gaskets)',
        'symptoms': ['High idle', 'Lean fuel trims (STFT positive)', 'Hissing from engine bay',
                     'P0171/P0174 codes', 'Rough idle that smooths out with RPM'],
        'cause': 'The intake manifold gaskets and various vacuum hoses deteriorate with age. '
                 'The upper intake plenum gasket and the brake booster vacuum hose are common failure points. '
                 'The PCV valve diaphragm on the Bank 1 valve cover also cracks.',
        'fix': 'Smoke test the intake system to find the leak. Common points: '
               'brake booster hose (large hose at rear of manifold), PCV valve (Bank 1 valve cover), '
               'intake runner gaskets (require manifold removal), and small vacuum lines at throttle body.',
        'parts': ['Intake manifold gasket set', 'PCV valve', 'Vacuum hose assortment', 'Hose clamps'],
        'difficulty': 'Easy (hose) to Medium (gaskets)',
        'cost': '£10-60 parts',
        'tags': ['vacuum', 'leak', 'idle', 'lean', 'hiss', 'p0171', 'p0174', 'stft', 'ltft',
                 'intake', 'gasket', 'pcv'],
    },
    {
        'title': 'Throttle Body Carbon Buildup',
        'symptoms': ['Rough idle', 'Hesitation from stop', 'Idle hunting (RPM bouncing)',
                     'P0507 code', 'Limp mode (P2106)', 'Stalling at junctions'],
        'cause': 'The electronic throttle body (drive-by-wire) accumulates carbon deposits on the '
                 'throttle plate and bore. This causes the plate to stick, and the ECU cannot maintain '
                 'stable idle. The X-Type throttle body motor can also fail internally.',
        'fix': 'Remove throttle body (4 bolts). Clean with carb cleaner and a soft cloth. '
               'Do NOT use a wire brush on the bore. After refitting, perform idle relearn: '
               'Turn key ON for 30 seconds (do not start). Start engine. Let idle for 2 minutes '
               'without touching anything. Drive normally for 10 minutes.',
        'parts': ['Throttle body gasket', 'Carb/throttle body cleaner'],
        'difficulty': 'Easy — 45 minutes',
        'cost': '£5-15',
        'tags': ['throttle', 'idle', 'hunting', 'stall', 'carbon', 'p0507', 'p2106',
                 'limp', 'hesitation', 'rough'],
    },
    {
        'title': 'MAF Sensor Contamination',
        'symptoms': ['Poor fuel economy', 'Sluggish acceleration', 'LTFT positive (lean correction)',
                     'Black smoke on hard acceleration', 'P0171/P0174 codes'],
        'cause': 'The hot-film MAF sensor element gets coated with oil vapour from the PCV system '
                 'and road grime. This causes it to underreport airflow, making the ECU think '
                 'less air is entering than actually is. The ECU commands less fuel → lean → LTFT rises.',
        'fix': 'Remove MAF sensor (2 Torx screws). Spray with dedicated MAF cleaner (CRC MAF cleaner). '
               'Let air dry completely (10 min). Do NOT touch the hot-film element with anything. '
               'Do NOT use carb cleaner, WD-40, or brake cleaner — they leave residue.',
        'parts': ['CRC MAF cleaner spray (or equivalent)'],
        'difficulty': 'Very easy — 15 minutes',
        'cost': '£5-8',
        'tags': ['maf', 'fuel', 'economy', 'lean', 'sluggish', 'power', 'ltft'],
    },
    {
        'title': 'Alternator Failure',
        'symptoms': ['Battery warning light', 'Voltage below 13.5V at cruise', 'Dim headlights',
                     'Electrical gremlins', 'Battery keeps going flat', 'Whining noise from belt area'],
        'cause': 'The alternator brushes and bearings wear over time. The voltage regulator (internal) '
                 'can also fail. Often gives warning signs of gradually dropping voltage before full failure. '
                 'Belt tension should be checked first — a loose belt can mimic alternator failure.',
        'fix': 'Check belt tension and condition first. Test alternator output with multimeter: '
               'should read 13.8-14.4V at 1500+ RPM with lights on. If low, replace alternator. '
               'The alternator is at the front of the engine, relatively accessible.',
        'parts': ['Alternator (130A, Bosch or equivalent)', 'Drive belt (if worn)'],
        'difficulty': 'Medium — 1-2 hours',
        'cost': '£80-150 for alternator',
        'tags': ['alternator', 'voltage', 'battery', 'charging', 'light', 'electrical', 'belt'],
    },
    {
        'title': 'Propshaft Centre Bearing',
        'symptoms': ['Vibration at 40-60 mph', 'Clunk when changing direction (drive/reverse)',
                     'Humming that increases with speed', 'Vibration worse under load'],
        'cause': 'The two-piece propshaft has a centre support bearing that wears. The rubber mount '
                 'deteriorates, allowing the shaft to run eccentric. Common on higher-mileage X-Types. '
                 'The Haldex coupling at the rear can also contribute to vibration if its fluid is old.',
        'fix': 'Replace the centre bearing assembly. This is a press-fit job — the shaft needs '
               'removing and the old bearing pressing out. Some garages replace the whole propshaft. '
               'Also change Haldex fluid if it has not been done.',
        'parts': ['Centre bearing assembly', 'Haldex fluid (if due)'],
        'difficulty': 'Medium-Hard — needs a press',
        'cost': '£50-80 parts, £150-250 fitted',
        'tags': ['vibration', 'propshaft', 'bearing', 'clunk', 'hum', 'awd', 'haldex'],
    },
    {
        'title': 'ABS Module Failure',
        'symptoms': ['ABS warning light', 'Traction control light', 'DSC warning',
                     'U0121 code', 'No ABS function', 'All warning lights on dash'],
        'cause': 'The ABS module (hydraulic unit + ECU) is a known failure point on the X-Type. '
                 'Internal solder joints crack due to thermal cycling. Sometimes just the ECU portion '
                 'fails, sometimes the pump motor.',
        'fix': 'Specialist repair/rebuild is the cost-effective option. Companies like BBA Reman, '
               'ECU Testing, and Sinspeed offer repair service — you send the module, they fix and '
               'return it. Full replacement with new/used is very expensive.',
        'parts': ['ABS module rebuild service'],
        'difficulty': 'Easy removal (4 brake lines + electrical connector), specialist rebuild',
        'cost': '£150-250 for rebuild service',
        'tags': ['abs', 'brakes', 'traction', 'dsc', 'warning', 'light', 'u0121', 'module'],
    },
    {
        'title': 'Rear Subframe Bushes',
        'symptoms': ['Clunking over bumps from rear', 'Vague rear end handling',
                     'Uneven rear tire wear', 'Knocking noise on rough roads'],
        'cause': 'The rear subframe mounting bushes deteriorate with age and mileage. '
                 'The rubber tears and allows the subframe to move, causing clunking and '
                 'poor rear-end geometry.',
        'fix': 'Replace rear subframe bushes. Polybush (polyurethane) replacements last longer '
               'than OEM rubber. Requires supporting/lowering the subframe — a big job but '
               'dramatically improves the rear end.',
        'parts': ['4x rear subframe bushes (Polybush recommended)', 'New bolts'],
        'difficulty': 'Hard — 4-6 hours, need to support subframe',
        'cost': '£40-80 parts (Polybush), £300-500 fitted',
        'tags': ['clunk', 'rear', 'subframe', 'bush', 'handling', 'knock', 'suspension'],
    },
    {
        'title': 'EGR Valve Carbon Buildup',
        'symptoms': ['Rough idle', 'Hesitation', 'P0401 code', 'Poor fuel economy',
                     'Slight misfire at low RPM', 'Failed emissions test'],
        'cause': 'The EGR valve and its passages accumulate carbon deposits over time, '
                 'preventing proper exhaust gas recirculation. The valve sticks partially open '
                 'or closed.',
        'fix': 'Remove EGR valve (bolted to intake manifold). Clean valve and passages with '
               'carb cleaner and a wire brush. Check vacuum actuator hose for cracks. '
               'Replace gasket on refit.',
        'parts': ['EGR gasket', 'Carb cleaner'],
        'difficulty': 'Easy-Medium — 1 hour',
        'cost': '£5-15',
        'tags': ['egr', 'carbon', 'idle', 'emissions', 'p0401', 'rough', 'hesitation'],
    },
    {
        'title': 'Camshaft Position Sensor Failure',
        'symptoms': ['No start / long crank', 'P0340/P0345 codes', 'Engine cuts out randomly',
                     'Rough running', 'Stalling'],
        'cause': 'The CMP sensors are located behind the timing cover and are exposed to heat '
                 'and engine oil vapour. The connector can also corrode. Failure causes the ECU '
                 'to lose cam timing reference.',
        'fix': 'Replace the CMP sensor — cheap part, accessible from the top of the engine. '
               'Check connector for green corrosion. Clean connector with electrical contact cleaner. '
               'If the thermostat housing is leaking (common), coolant can reach the CMP connector.',
        'parts': ['Camshaft position sensor (Bank 1 or Bank 2)'],
        'difficulty': 'Easy — 20 minutes',
        'cost': '£15-30',
        'tags': ['camshaft', 'sensor', 'no start', 'crank', 'stall', 'p0340', 'p0345', 'cut out'],
    },
    {
        'title': 'Fuel Pump Weak / Failing',
        'symptoms': ['Long crank to start', 'Hesitation under hard acceleration',
                     'P1235 code', 'Engine cuts out at high load', 'Whining from rear'],
        'cause': 'The in-tank fuel pump wears over time. Low fuel level accelerates wear '
                 '(the fuel cools the pump). The fuel pump relay can also fail intermittently.',
        'fix': 'First: check fuel pump relay (engine bay fuse box — swap with identical relay). '
               'Listen for pump prime: turn key to ON, you should hear a 2-second whir from the rear. '
               'If no prime sound, check relay and wiring. Pump access is under the rear seat.',
        'parts': ['Fuel pump assembly (in-tank)', 'Fuel pump relay (if faulty)'],
        'difficulty': 'Medium — rear seat removal, fuel system',
        'cost': '£80-150 for pump',
        'tags': ['fuel', 'pump', 'start', 'hesitation', 'p1235', 'relay', 'stall', 'crank'],
    },
    {
        'title': 'O2 Sensor Degradation',
        'symptoms': ['Poor fuel economy', 'Fuel trim drift', 'P0131/P1131/P1151 codes',
                     'Slight rich or lean running', 'Failed emissions'],
        'cause': 'The upstream O2 sensors (before catalytic converter) degrade with age and become '
                 '"lazy" — slow to respond to air/fuel changes. This causes inaccurate fuel trim '
                 'correction. Expected lifespan is ~80,000-100,000 miles.',
        'fix': 'Replace upstream O2 sensors (Bank 1 Sensor 1 and/or Bank 2 Sensor 1). '
               'Use OEM-equivalent (Bosch, Denso, NTK). Check exhaust flex joint for leaks first — '
               'an exhaust leak before the sensor gives the same symptoms.',
        'parts': ['O2 sensor upstream (Bank 1 and/or Bank 2)'],
        'difficulty': 'Easy — 30 minutes per sensor (penetrating oil helps)',
        'cost': '£30-60 per sensor',
        'tags': ['o2', 'oxygen', 'sensor', 'fuel', 'economy', 'emissions', 'trim',
                 'p0131', 'p1131', 'p1151', 'lambda'],
    },
    {
        'title': 'Purge Valve Stuck Open',
        'symptoms': ['Rich running at idle', 'Fuel smell', 'STFT negative (rich)',
                     'P0443 code', 'Hard start when hot', 'Rough idle after refuelling'],
        'cause': 'The EVAP purge valve on the firewall side of the engine bay can stick open, '
                 'allowing fuel vapour to flood the intake at idle. Common on ageing X-Types.',
        'fix': 'Locate purge valve (firewall, small solenoid with vacuum hoses). '
               'Test: apply 12V — should click. Blow through it — should only flow when energised. '
               'If it flows freely without power, it is stuck open. Replace.',
        'parts': ['EVAP purge valve solenoid'],
        'difficulty': 'Easy — 20 minutes',
        'cost': '£20-40',
        'tags': ['purge', 'evap', 'rich', 'fuel', 'smell', 'p0443', 'vapour', 'idle'],
    },
    {
        'title': 'Catalytic Converter Degradation',
        'symptoms': ['P0420/P0430 codes', 'Rotten egg smell', 'Failed MOT emissions',
                     'Reduced power at high RPM', 'Rattling from underneath'],
        'cause': 'The catalytic converter substrate breaks down over time, especially if the engine '
                 'has been running rich (from bad coils, leaking injectors). The X-Type has 2 pre-cats '
                 'and 1 main cat. Rattling = substrate has broken apart inside.',
        'fix': 'IMPORTANT: Fix the root cause first (misfires, fuel trim issues). Running the engine '
               'rich kills cats. Then clear codes and retest. If the cat is genuinely failed: '
               'aftermarket replacement cats are £200-400. Pre-cats are welded to manifolds.',
        'parts': ['Catalytic converter (main or pre-cat)'],
        'difficulty': 'Medium-Hard — exhaust work',
        'cost': '£200-400 parts + fitting',
        'tags': ['cat', 'catalyst', 'emissions', 'mot', 'p0420', 'p0430', 'exhaust',
                 'rattle', 'smell', 'egg'],
    },
]


# ═══════════════════════════════════════════════════════════════════
#  Service Schedule & Intervals
# ═══════════════════════════════════════════════════════════════════

SERVICE_SCHEDULE = [
    {'interval': 'Every 5,000 miles / 6 months', 'item': 'Engine oil & filter change',
     'details': '5.5L 5W-30, Ford WSS-M2C913-A/B spec. Sump plug torque: 25 Nm.'},
    {'interval': 'Every 10,000 miles / 12 months', 'item': 'Air filter replacement',
     'details': 'Panel filter in airbox. Check MAF sensor condition while open.'},
    {'interval': 'Every 10,000 miles / 12 months', 'item': 'Pollen/cabin filter',
     'details': 'Located behind glovebox. Remove glovebox liner to access.'},
    {'interval': 'Every 20,000 miles / 24 months', 'item': 'Spark plugs',
     'details': 'AGSF-32PM or NGK ITR6F-13, gap 1.3mm. Torque: 15 Nm. Anti-seize on threads.'},
    {'interval': 'Every 24 months', 'item': 'Brake fluid flush',
     'details': 'DOT 4 fluid. Bleed all four corners: RR → LR → RF → LF order.'},
    {'interval': 'Every 30,000 miles', 'item': 'Transmission fluid change',
     'details': 'Drain and fill: ~4L Mercon V. Total capacity 9.5L. '
                'Drop pan, replace filter, clean magnets, refit, fill through dipstick tube.'},
    {'interval': 'Every 40,000 miles', 'item': 'Haldex coupling fluid',
     'details': 'Haldex AOC fluid. Drain plug on rear coupling. Fill until it overflows. '
                'Critical for AWD function.'},
    {'interval': 'Every 40,000 miles', 'item': 'Coolant replacement',
     'details': '7.5L orange OAT coolant. Drain from radiator petcock. Bleed via heater hose or '
                'by running engine with expansion tank cap off until thermostat opens.'},
    {'interval': 'Every 60,000 miles', 'item': 'Drive belt replacement',
     'details': 'Serpentine belt. Check for cracks, glazing, or chirping noise. '
                'Automatic tensioner — check it holds tension and does not oscillate.'},
    {'interval': 'As needed', 'item': 'Brake pads and discs',
     'details': 'Minimum pad thickness: 2mm. Front discs min: 22mm. Rear discs min: 8mm. '
                'Torque wheel bolts: 100 Nm.'},
]


# ═══════════════════════════════════════════════════════════════════
#  Roadside Emergency Procedures
# ═══════════════════════════════════════════════════════════════════

EMERGENCY_PROCEDURES = [
    {
        'title': 'Overheating — Pull Over Procedure',
        'steps': [
            'Turn OFF air conditioning immediately — reduces engine load.',
            'Turn heater to MAX HOT with fan on high — this acts as a secondary radiator.',
            'If temperature keeps rising, pull over to a safe place as soon as possible.',
            'Do NOT open the expansion tank cap while hot — pressurised system will scald.',
            'Let engine idle for 2-3 minutes to circulate coolant, then turn off.',
            'Wait 30+ minutes before opening expansion tank.',
            'Check coolant level. Top up with WATER if needed (not ideal but safe short-term).',
            'Check for visible leaks: thermostat housing (top of engine between the V), '
            'hoses, radiator seams, water pump weep hole.',
            'If you must drive: keep RPM low, avoid hills, watch the temperature gauge closely.',
        ],
    },
    {
        'title': 'Engine Stall — Won\'t Restart',
        'steps': [
            'Turn hazards on. Ensure vehicle is in Park (auto) or Neutral.',
            'Wait 30 seconds, then try starting again. Hold starter for max 10 seconds.',
            'If cranks but won\'t fire: check fuel pump prime (key ON — listen for whir from rear).',
            'If no prime sound: check fuel pump relay in engine bay fuse box. Swap with identical relay.',
            'If cranks strongly but no fire: possible CMP sensor failure (P0340/P0345). '
            'Try disconnecting and reconnecting the CMP sensor connector.',
            'If won\'t crank at all: check battery terminals for corrosion. '
            'Clean and retighten. Try jump start.',
            'If starts then dies: possible throttle body failure (P2106 limp mode). '
            'Try key off → key on → start with NO throttle input.',
        ],
    },
    {
        'title': 'Limp Mode (Reduced Power)',
        'steps': [
            'Limp mode limits RPM to ~2500 and reduces power to protect the engine.',
            'Safe to drive slowly — just won\'t have much power.',
            'Pull over when convenient. Turn engine off, wait 30 seconds, restart.',
            'If limp mode clears: likely a transient sensor glitch. Monitor.',
            'If limp mode persists: check DRIFTER dashboard for DTC codes.',
            'Common causes: throttle body fault (P2106/P2111/P2112), '
            'TPS correlation (P2135), MAF fault, or transmission fault.',
            'Drive gently to home/garage. Do not force high RPM in limp mode.',
        ],
    },
    {
        'title': 'Battery / Charging Failure',
        'steps': [
            'If battery light comes on while driving: alternator may have failed.',
            'Turn off non-essential electrics: heated seats, rear screen, radio, phone charger.',
            'Keep headlights on if needed for safety (required by law at night).',
            'You have roughly 20-40 minutes of driving on battery alone.',
            'Drive directly to nearest safe stopping point or garage.',
            'Do NOT turn off the engine — you may not be able to restart.',
            'If voltage drops below 11V: power steering may become heavy, engine may die.',
        ],
    },
    {
        'title': 'Tire Pressure Loss / Puncture',
        'steps': [
            'If TPMS alerts to sudden pressure drop: slow down gradually. Do not brake hard.',
            'Pull over to a safe, flat area away from traffic.',
            'The X-Type spare is a space-saver under the boot floor (if present).',
            'Jack point front: behind front wheel arch seam. Rear: in front of rear wheel arch seam.',
            'Wheel bolt torque: 100 Nm. Do not overtighten with the wheelbrace.',
            'Space-saver max speed: 50 mph. Drive to a tire shop promptly.',
            'If no spare: tire sealant kit (if supplied) — good for small punctures only.',
        ],
    },
    {
        'title': 'Coolant Loss on Motorway',
        'steps': [
            'Pull onto hard shoulder or into services ASAP.',
            'Do NOT continue driving with coolant temp in RED — engine damage occurs quickly.',
            'Once stopped, leave engine idling for 2 minutes (if not already critical).',
            'The most common cause is thermostat housing crack. Look for coolant pooling '
            'in the V of the engine (between the cam covers).',
            'Emergency fix: Let engine cool completely. Top up with water. Drive slowly to garage.',
            'Temporary repair: epoxy putty (JB Weld) on a crack can get you home if it is a small crack.',
            'Call breakdown if coolant is pouring out — do not drive without coolant.',
        ],
    },
]


# ═══════════════════════════════════════════════════════════════════
#  Torque Specs — Quick Reference
# ═══════════════════════════════════════════════════════════════════

TORQUE_SPECS = {
    'Sump drain plug': '25 Nm',
    'Oil filter housing': '25 Nm',
    'Spark plugs': '15 Nm',
    'Coil pack bolts': '6 Nm',
    'Wheel bolts': '100 Nm',
    'Caliper bracket (front)': '115 Nm',
    'Caliper bracket (rear)': '90 Nm',
    'Caliper slide pins': '35 Nm',
    'Intake manifold bolts': '10 Nm',
    'Exhaust manifold nuts': '25 Nm',
    'Thermostat housing': '10 Nm',
    'Alternator bolts': '50 Nm',
    'Drive belt tensioner': '25 Nm',
    'Battery clamp': '8 Nm',
    'Subframe bolts (rear)': '175 Nm',
    'Lower wishbone bolt (front)': '175 Nm',
    'Track rod end nut': '55 Nm',
    'Propshaft centre bearing': '25 Nm',
    'Propshaft flange bolts': '80 Nm',
    'Transmission drain plug': '30 Nm',
    'EGR valve bolts': '10 Nm',
    'Throttle body bolts': '10 Nm',
}


# ═══════════════════════════════════════════════════════════════════
#  Fuse & Relay Reference
# ═══════════════════════════════════════════════════════════════════

FUSE_REFERENCE = {
    'Engine Bay Fuse Box': {
        'location': 'Left side of engine bay, black box near the bulkhead',
        'key_fuses': {
            'F1.1 (30A)': 'ABS module',
            'F1.3 (20A)': 'Fuel pump',
            'F1.4 (15A)': 'Fuel injectors',
            'F1.5 (10A)': 'PCM (engine ECU)',
            'F1.7 (10A)': 'Ignition coils',
            'F1.14 (20A)': 'Starter motor relay',
            'R1 relay': 'Fuel pump relay',
            'R2 relay': 'Main engine relay',
            'R5 relay': 'A/C compressor relay',
        },
    },
    'Passenger Fuse Box': {
        'location': 'Behind the glovebox — remove glovebox liner to access',
        'key_fuses': {
            'F2.1 (15A)': 'Instrument cluster',
            'F2.3 (20A)': 'Central locking',
            'F2.7 (15A)': 'Heated rear screen',
            'F2.11 (20A)': 'Heated seats',
            'F2.15 (15A)': 'OBD-II diagnostic port',
            'F2.19 (10A)': 'Radio/entertainment',
        },
    },
    'Boot Fuse Box': {
        'location': 'Left side of boot, behind trim panel',
        'key_fuses': {
            'F3.1 (50A)': 'Cooling fan (high speed)',
            'F3.2 (30A)': 'Cooling fan (low speed)',
            'F3.4 (40A)': 'Rear window heater',
        },
    },
}


# ═══════════════════════════════════════════════════════════════════
#  Search Function
# ═══════════════════════════════════════════════════════════════════

def search(query):
    """Search the knowledge base. Returns list of matching results."""
    if not query or not query.strip():
        return []

    terms = query.lower().split()
    results = []

    # Search common problems
    for problem in COMMON_PROBLEMS:
        score = 0
        searchable = ' '.join([
            problem['title'].lower(),
            problem['cause'].lower(),
            problem['fix'].lower(),
            ' '.join(problem.get('symptoms', [])).lower(),
            ' '.join(problem.get('tags', [])).lower(),
        ])
        for term in terms:
            if term in searchable:
                score += searchable.count(term)
                # Boost for tag match
                if term in [t.lower() for t in problem.get('tags', [])]:
                    score += 3
        if score > 0:
            results.append({
                'type': 'problem',
                'title': problem['title'],
                'score': score,
                'data': problem,
            })

    # Search emergency procedures
    for proc in EMERGENCY_PROCEDURES:
        score = 0
        searchable = proc['title'].lower() + ' ' + ' '.join(proc['steps']).lower()
        for term in terms:
            if term in searchable:
                score += searchable.count(term)
        if score > 0:
            results.append({
                'type': 'emergency',
                'title': proc['title'],
                'score': score,
                'data': proc,
            })

    # Search torque specs
    for part, torque in TORQUE_SPECS.items():
        if any(term in part.lower() for term in terms):
            results.append({
                'type': 'torque',
                'title': f'{part}: {torque}',
                'score': 5,
                'data': {'part': part, 'torque': torque},
            })

    # Search vehicle specs
    for category, specs in VEHICLE_SPECS.items():
        for key, val in specs.items():
            searchable = f'{category} {key} {val}'.lower()
            if any(term in searchable for term in terms):
                results.append({
                    'type': 'spec',
                    'title': f'{category.title()}: {key.replace("_", " ").title()}',
                    'score': 2,
                    'data': {'category': category, 'key': key, 'value': val},
                })

    # Search fuse reference
    for box_name, box_data in FUSE_REFERENCE.items():
        for fuse, desc in box_data.get('key_fuses', {}).items():
            searchable = f'{box_name} {fuse} {desc}'.lower()
            if any(term in searchable for term in terms):
                results.append({
                    'type': 'fuse',
                    'title': f'{fuse}: {desc}',
                    'score': 3,
                    'data': {'box': box_name, 'location': box_data['location'],
                             'fuse': fuse, 'description': desc},
                })

    # Sort by score descending
    results.sort(key=lambda r: r['score'], reverse=True)
    return results[:20]


def get_advice_for_alert(alert_msg):
    """Given a DRIFTER alert message, return relevant mechanical advice."""
    if not alert_msg:
        return None

    # Extract keywords from the alert
    msg_lower = alert_msg.lower()

    # Map alert patterns to search terms
    patterns = [
        (['coolant', 'thermostat', 'temperature', 'overheating'], 'coolant thermostat'),
        (['vacuum leak', 'lean', 'stft'], 'vacuum leak intake'),
        (['coil', 'misfire', 'rpm stumble'], 'coil misfire'),
        (['alternator', 'voltage', 'undercharging', 'overcharging'], 'alternator voltage'),
        (['throttle', 'load mismatch', 'p2106', 'limp'], 'throttle body'),
        (['maf', 'mass air'], 'maf sensor'),
        (['idle', 'instability', 'hunting'], 'idle rough'),
        (['dtc', 'p0'], 'dtc code'),
        (['tire', 'pressure', 'tpms'], 'tire pressure'),
        (['stall', 'engine stall'], 'stall start'),
        (['fuel pump', 'p1235'], 'fuel pump'),
        (['battery', 'critical'], 'battery alternator'),
    ]

    for keywords, search_terms in patterns:
        if any(kw in msg_lower for kw in keywords):
            return search(search_terms)

    return None
