#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Offline Mechanical Advisor
Complete 2004 Jaguar X-Type 2.5L V6 knowledge base.
Australian-delivered RHD. AWD with Jatco JF506E.

This module is the REASONING REFERENCE for the LLM mechanic.
It provides facts, specifications, and context — NOT hardcoded
diagnostic trees. The LLM should use this data to THINK THROUGH
problems using its own reasoning ability.

No internet needed. Searchable from the dashboard.
UNCAGED TECHNOLOGY — EST 1991
"""

# ═══════════════════════════════════════════════════════════════════
#  Vehicle Specs — 2004 Jaguar X-Type 2.5L V6 (AJ-V6 / Duratec)
#  Australian-spec, RHD, AWD
# ═══════════════════════════════════════════════════════════════════

VEHICLE_SPECS = {
    'identity': {
        'make': 'Jaguar',
        'model': 'X-Type',
        'platform': 'X400 (based on Ford CD132)',
        'year': '2004',
        'body': 'Sedan',
        'market': 'Australian delivery (ROW spec), Right-Hand Drive',
        'model_years_produced': '2001-2009',
    },
    'engine': {
        'name': 'AJ-V6 (Ford Duratec 25)',
        'code': 'AJ-V6 / Duratec 25',
        'displacement': '2495cc (2.5L)',
        'configuration': 'V6, 60-degree bank angle',
        'bore_stroke': '82.4mm x 79.5mm',
        'compression': '10.0:1',
        'power': '196 PS (144 kW) @ 6800 RPM',
        'torque': '245 Nm (181 lb-ft) @ 3500 RPM',
        'valvetrain': 'DOHC 24-valve (4 valves per cylinder)',
        'firing_order': '1-4-2-5-3-6',
        'bank_layout': 'Bank 1 = RH (cylinders 1-3), Bank 2 = LH (cylinders 4-6)',
        'ignition': 'Coil-on-plug (COP) — 6 individual coils',
        'fuel_system': 'Sequential multi-point injection (SEFI)',
        'fuel_pressure': '3.0-3.5 bar (43-51 PSI)',
        'idle_rpm': '650-780 RPM (warm)',
        'cold_idle': '1000-1400 RPM (below 40°C)',
        'max_rpm': '6800 RPM (rev limiter)',
        'oil_capacity': '5.5 litres with filter',
        'oil_spec': '5W-30 Ford WSS-M2C913-A/B (or Castrol Magnatec 5W-30)',
        'spark_plugs': 'Motorcraft AGSF-32PM (or NGK ITR6F-13, gapped 1.3mm)',
        'spark_plug_torque': '15 Nm — CRITICAL: do not overtighten, aluminium heads',
        'coolant_capacity': '7.5 litres (inc. heater)',
        'coolant_type': 'Organic Acid Technology (OAT) — orange/pink',
        'thermostat_opens': '88°C, fully open 97°C',
        'timing': 'Chain driven (no belt to replace)',
        'variable_valve_timing': 'VVT solenoids on both banks',
        'intake_manifold_tuning': 'IMT valve with vacuum-operated runner flaps',
        'pcv_system': 'PCV valve on Bank 1 valve cover, breather hose to intake',
        'ecm_connector': 'EN16 / 134-way / Black',
        'ecm_location': 'Engine compartment, front bulkhead, RH side',
        'ecm_can_pins': 'EN16-123 (CAN-), EN16-124 (CAN+)',
    },
    'transmission': {
        'type': 'Jatco JF506E (JA5A-EL) 5-speed automatic',
        'tcm_location': 'Transmission top, integral with valve body',
        'tcm_variants': '16-bit and 32-bit TCM variants exist',
        'fluid': 'Mercon V ATF (Ford XT-5-QMC)',
        'capacity_total': '9.5 litres total',
        'capacity_drain_fill': '4.0 litres drain-and-fill',
        'drain_interval': 'Every 60,000 km / 40,000 miles — NOT lifetime fill despite Jaguar claim',
        'filter': 'Internal — replace at fluid change',
        'pan_bolt_torque': '6-8 Nm in cross pattern',
        'modes': 'P, R, N, D, Sport',
        'j_gate': 'Jaguar J-Gate shift pattern',
        'towing_limit': 'Max 0.8km at 50km/h with front wheels on ground',
        'adaptive_learning': 'TCM learns driving style — reset adaptives after fluid change with scanner',
        'limp_mode_gear': '3rd gear only when limp mode activated',
        'solenoid_count': '5 shift solenoids (A through E)',
        'weak_point': 'Shift Solenoid C (controls 2-4 shifts) — most common failure',
        'notes': 'Cold-start limp mode that clears when warm = early solenoid failure sign',
    },
    'drivetrain': {
        'type': 'All-Wheel Drive (AWD)',
        'coupling': 'Haldex electro-hydraulic coupling',
        'split_normal': '95% front / 5% rear (normal driving)',
        'split_max': 'Up to 50/50 under slip detection',
        'haldex_fluid': 'Haldex AOC fluid — change every 60,000 km',
        'rear_diff': 'Open differential (no limited-slip), integral with Haldex unit',
        'rear_diff_fluid': '75W-90 LS synthetic, 1.2-1.5 litres',
        'propshaft': 'Two-piece with centre bearing (known wear item)',
        'propshaft_length': 'Approx 1.2-1.5 metres',
        'ptu_location': 'Mounted to transmission rear, feeds propshaft',
        'ptu_fluid': '75W-90 synthetic (MTF-94 spec), 1.5-2.0 litres',
        'ptu_drain_plug': 'NO drain plug — vacuum extraction required through filler port',
        'ptu_notes': 'Known weak point. Factory "sealed for life" claim is false. '
                     'Fluid degrades rapidly in Australian heat. Service every 50,000 km.',
        'cv_boots': 'Inner and outer CV joints on front halfshafts — inspect for splits',
    },
    'brakes': {
        'front_discs': '300mm ventilated discs, single-piston floating caliper',
        'rear_discs': '280mm solid discs, single-piston floating caliper',
        'handbrake': 'Cable-operated drum-in-disc (rear)',
        'fluid': 'DOT 4 (Ford PM-1-C)',
        'bleed_order': 'RR → LR → RF → LF (furthest from master first)',
        'pad_thickness_min': '2mm',
        'disc_min_front': '22mm thickness (new ~28mm)',
        'disc_min_rear': '8mm thickness (new ~12mm)',
        'abs_module_location': 'Behind battery / engine compartment, RH side',
        'abs_variants': 'ABS only / ABS+TC / Dynamic Stability Control (DSC)',
        'dsc_inputs': 'Steering angle sensor, yaw rate sensor, 4x wheel speed sensors, brake pressure sensor',
    },
    'suspension': {
        'front': 'MacPherson strut, lower wishbone, anti-roll bar',
        'rear': 'Multi-link, coil springs, anti-roll bar',
        'front_camber': '-0.54° ± 0.75°',
        'front_castor': '2.25° ± 0.75°',
        'front_total_toe': '-0.1° ± 0.2° (slight toe-out)',
        'rear_camber': '-0.7° ± 0.75°',
        'rear_individual_toe': '0.125° ± 0.14° per side',
        'rear_total_toe': '0.25° ± 0.2° (toe-in)',
        'rear_thrust_angle': '0° ± 0.14°',
        'ride_height_front': '371mm ± 15mm (wheel centre to arch apex)',
        'ride_height_rear': '370mm ± 15mm',
        'alignment_conditions': 'All fluids full, full fuel tank, tires at normal pressure',
        'ball_joint_max_play': '0.8mm radial',
        'rear_toe_adjustment': 'Cam bolts on rear lower arms — 115 Nm final tightening ON WHEELS',
        'front_toe_adjustment': 'Tie rod end rotation — locknut 40 Nm',
    },
    'electrical': {
        'battery': '12V, 70Ah (Group 48/H6)',
        'alternator': '130A',
        'voltage_running': '13.8-14.4V',
        'voltage_key_on': '12.4-12.7V (healthy battery)',
        'voltage_cranking_min': '9.6V (below = battery or starter issue)',
        'parasitic_draw_normal': '50-100mA at rest',
        'parasitic_draw_problem': '200mA+ indicates fault — check GEM sleep mode',
        'ecu': 'Ford EEC-V (PCM located behind glovebox on X-Type, but ECM in engine bay on V6)',
        'can_bus_powertrain': '500 kbaud, ISO 11898, two-wire twisted pair, 120Ω termination',
        'can_bus_body': 'SCP (Standard Corporate Protocol), 10.4 kbaud',
        'can_bus_audio': 'D2B fiber optic network (entertainment only)',
        'obd_protocol': 'ISO 15765-4 CAN (11-bit, 500kbps)',
        'obd_port_fuse': 'F2.15 (15A) — passenger fuse box',
        'pats': 'Passive Anti-Theft System — key transponder through instrument cluster',
        'gem_location': 'Behind instrument panel, RH side',
        'gem_functions': 'Interior lighting, door locks, battery saver, SCP network gateway',
        'instrument_cluster_role': 'CAN/SCP network gateway — bridges powertrain and body networks',
    },
    'tires_wheels': {
        'factory_size': '205/55R16',
        'pressure_front': '2.1 bar (30 PSI)',
        'pressure_rear': '2.1 bar (30 PSI)',
        'pressure_loaded': '2.4 bar (35 PSI) front and rear when fully loaded',
        'wheel_torque_alloy': '103 Nm ± 15.5 Nm',
        'wheel_torque_steel': '80 Nm ± 12 Nm',
        'bolt_pattern': '5x108',
        'centre_bore': '63.4mm',
        'offset': 'ET52.5',
        'spare': 'Space-saver under boot floor — max 80km/h',
    },
    'fluids_capacities': {
        'engine_oil': '5.5L — 5W-30 (WSS-M2C913-A/B)',
        'coolant': '7.5L — OAT (orange/pink)',
        'transmission_total': '9.5L — Mercon V ATF',
        'transmission_drain_fill': '4.0L',
        'haldex_coupling': 'Haldex AOC fluid',
        'ptu': '1.5-2.0L — 75W-90 synthetic',
        'rear_diff': '1.2-1.5L — 75W-90 LS synthetic',
        'brake_fluid': 'DOT 4',
        'power_steering': 'Mercon V ATF',
        'washer': 'Any screenwash',
        'fuel_tank': '61.5 litres (56.5L refill, 5L reserve)',
        'fuel_type': 'Unleaded 95 RON minimum, 98 RON recommended',
    },
    'dimensions': {
        'kerb_weight': 'Approx 1530 kg (AWD)',
        'gvm': 'Approx 1970 kg',
        'towing_braked': '1500 kg max',
        'fuel_tank_litres': '61.5',
    },
}


# ═══════════════════════════════════════════════════════════════════
#  Owner's Vehicle History — This car's known issues
#  The LLM should use this context when reasoning about symptoms
# ═══════════════════════════════════════════════════════════════════

OWNER_VEHICLE_HISTORY = {
    'description': 'Known history and current issues for THIS specific vehicle',
    'known_repairs': [
        {
            'issue': 'Spark plug exploded due to improper torque',
            'details': 'A spark plug was overtightened and eventually failed catastrophically. '
                       'The aluminium cylinder head threads may be damaged. Anti-seize and a '
                       'torque wrench are essential for all future plug changes (15 Nm max).',
            'cylinder': 'Unknown — inspect all wells',
            'implications': 'Possible thread damage requiring Helicoil repair. Check compression '
                           'on the affected cylinder.',
        },
        {
            'issue': 'Valve cover gasket oil leak into spark plug wells',
            'details': 'Oil has been found in spark plug wells, indicating the valve cover gasket '
                       'seals have failed. Oil in the wells conducts electricity and causes coil '
                       'pack secondary voltage breakdown, leading to misfires.',
            'affected_banks': 'Likely both banks given vehicle age',
            'implications': 'Root cause of coil pack failures. Must be fixed before replacing '
                           'coils or the new coils will fail too.',
        },
    ],
    'current_symptoms': [
        {
            'symptom': 'P0303 — Cylinder 3 misfire',
            'details': 'Active misfire on cylinder 3. Runs fine under 3000 RPM but misfires '
                       'become apparent above that. This is consistent with a coil pack failing '
                       'under load (higher RPM = higher secondary voltage demand).',
            'related_codes': ['P0303'],
        },
        {
            'symptom': 'Cruise control disabled — "Cruise Unavailable" message',
            'details': 'Cruise control is disabled by the ECM when powertrain faults are detected. '
                       'The misfire code P0303 triggers this safety feature. Cruise will return '
                       'once the misfire is resolved and codes are cleared.',
            'trigger_rpm': 'Above 3000 RPM',
        },
        {
            'symptom': 'Rough idle',
            'details': 'Slight roughness at idle. Could be from the misfire, vacuum leaks, or '
                       'a combination. Check STFT/LTFT values to determine if lean condition exists.',
        },
    ],
    'suspected_vacuum_leaks': [
        'PCV hose — common split point on AJ-V6',
        'IMT valve O-ring — original nitrile rubber degrades from fuel vapours. Upgrade to Viton.',
        'Brake booster hose — large vacuum hose at rear of intake manifold',
        'Intake manifold gaskets — check with smoke test',
    ],
    'maintenance_notes': 'Australian climate accelerates rubber and fluid degradation. '
                         'All service intervals should be reduced by ~20% vs European schedule.',
}


# ═══════════════════════════════════════════════════════════════════
#  Common Problems & Fixes — X-Type Specific
#  These are REFERENCE entries for the LLM to reason with.
#  Each entry provides facts about a known issue — the LLM
#  combines these with live telemetry to form its own diagnosis.
# ═══════════════════════════════════════════════════════════════════

COMMON_PROBLEMS = [
    {
        'title': 'Thermostat Housing Failure (Plastic)',
        'symptoms': ['Coolant loss with no visible leak', 'Temperature gauge swings',
                     'Overheating then cooling repeatedly', 'Sweet smell from engine bay',
                     'Coolant pooling in the V between cam covers'],
        'cause': 'The plastic thermostat housing behind the timing cover develops hairline cracks. '
                 'This is the #1 most common X-Type fault. The housing sits between the V of the engine '
                 'and is subjected to constant heat cycling. In Australian conditions the plastic '
                 'degrades faster due to higher ambient temperatures.',
        'fix': 'Replace with aluminium aftermarket housing ($40-70 AUD from eBay/Sparesbox). '
               'The plastic OEM part will fail again. Requires removing the intake manifold for access. '
               'Budget 3-4 hours. Replace thermostat and O-ring seals at the same time.',
        'parts': ['Thermostat housing (aluminium aftermarket)', 'Thermostat (88°C)',
                  'New O-ring seals', 'Coolant 7.5L OAT'],
        'difficulty': 'Medium — need to remove intake manifold',
        'cost': '$60-120 AUD parts, $300-500 AUD if mechanic does it',
        'related_dtcs': ['P0125 (ECT insufficient for closed loop)', 'P0116/P0117/P0118 (ECT sensor)'],
        'tags': ['coolant', 'overheating', 'thermostat', 'leak', 'temperature', 'p0125',
                 'p0116', 'p0117', 'p0118', 'housing', 'plastic', 'aluminium'],
    },
    {
        'title': 'Coil Pack Failure (COP)',
        'symptoms': ['Misfire at idle or under load', 'Rough running', 'Check engine light',
                     'P0301-P0306 codes', 'Loss of power', 'Hesitation on acceleration',
                     'Misfire worse in damp weather or under high load/RPM',
                     'Cruise control disabled ("Cruise Unavailable")'],
        'cause': 'The COP (coil-on-plug) coils on the AJ-V6 degrade over time due to heat and vibration. '
                 'They develop internal cracks in the insulation, causing intermittent spark failure. '
                 'Often worse in damp weather. Oil in plug wells from valve cover gasket failure '
                 'DRAMATICALLY accelerates coil death — oil conducts electricity and causes secondary '
                 'voltage breakdown. The coil cannot generate enough voltage to fire the plug '
                 'under high load (above 3000 RPM) but may work fine at idle.',
        'fix': 'FIRST check for oil in plug wells — if present, the valve cover gasket must be replaced '
               'BEFORE replacing coils, or the new coils will fail. Swap the suspect coil to a different '
               'cylinder — if the misfire code follows the coil, replace it. Recommended: replace all 6 '
               'at once with new plugs. Use Motorcraft or equivalent quality.',
        'diagnostic_test': 'Swap coil from misfiring cylinder to a known-good cylinder. Clear codes. '
                           'If misfire follows the coil → bad coil. If misfire stays on original '
                           'cylinder → check plug, compression, injector.',
        'parts': ['6x coil packs (Motorcraft DG513 or equivalent)',
                  '6x spark plugs (AGSF-32PM or NGK ITR6F-13, gapped 1.3mm)'],
        'difficulty': 'Easy — 30 minutes for all 6',
        'cost': '$90-180 AUD for 6 coils + plugs',
        'related_dtcs': ['P0300 (random misfire)', 'P0301-P0306 (cylinder specific)',
                         'P0351-P0356 (coil circuit fault)'],
        'tags': ['misfire', 'coil', 'spark', 'rough', 'hesitation', 'p0300', 'p0301', 'p0302',
                 'p0303', 'p0304', 'p0305', 'p0306', 'p0351', 'p0352', 'p0353', 'p0354',
                 'p0355', 'p0356', 'cruise', 'unavailable', 'oil', 'plug well'],
    },
    {
        'title': 'Valve Cover Gasket Failure (Oil in Plug Wells)',
        'symptoms': ['Oil visible in spark plug wells', 'Misfire codes (P0301-P0306)',
                     'Oil smell from engine bay', 'Coil packs failing repeatedly',
                     'Smoke from coil area on startup'],
        'cause': 'The rubber valve cover gaskets degrade from heat cycling (typically 80,000-150,000 km). '
                 'Oil seeps past the spark plug tube seals into the wells. This is the ROOT CAUSE '
                 'of most coil pack failures on the AJ-V6. In Australian heat, gaskets may fail '
                 'earlier. Both banks should be inspected.',
        'fix': 'Remove intake manifold and accessories for access. Remove 6 bolts per valve cover. '
               'Clean gasket surfaces thoroughly. Install new gasket set with light silicone at corners. '
               'Torque cover bolts to 3.6-5.5 Nm in cross pattern. Allow 24hr cure before starting. '
               'After gasket replacement, dry out plug wells with compressed air, clean or replace '
               'coils, and install fresh plugs.',
        'parts': ['Valve cover gasket set (both banks)', 'Spark plug tube seals',
                  'Silicone gasket maker (sparingly)', 'New spark plugs and coils if contaminated'],
        'difficulty': 'Medium — 3-4 hours, intake manifold removal required',
        'cost': '$60-120 AUD for gasket set, $400-600 if done by a mechanic',
        'related_dtcs': ['P0301-P0306 (misfires from oil-fouled coils)'],
        'tags': ['valve cover', 'gasket', 'oil', 'plug well', 'seal', 'misfire', 'coil',
                 'leak', 'smoke', 'p0301', 'p0302', 'p0303', 'p0304', 'p0305', 'p0306'],
    },
    {
        'title': 'Vacuum Leak — PCV Hose, IMT Valve, Brake Booster',
        'symptoms': ['High idle', 'Lean fuel trims (STFT positive >5%)', 'Hissing from engine bay',
                     'P0171/P0174 codes (system too lean)', 'Rough idle that smooths out with RPM',
                     'Difficult cold starts', 'Cruise control disabled'],
        'cause': 'Multiple vacuum leak sources on the AJ-V6:\n'
                 '1. PCV hose — splits from heat/age at the connection points\n'
                 '2. IMT valve O-ring — original nitrile rubber degrades from fuel vapours and heat. '
                 'Viton (FKM) replacement lasts 2-3x longer.\n'
                 '3. Brake booster hose — large hose at rear of intake manifold, splits cause hard pedal too.\n'
                 '4. Intake manifold runner gaskets — require manifold removal to replace.\n'
                 '5. Plastic vacuum routing clips become brittle and break, letting hoses move and crack.',
        'fix': 'Smoke test the intake system to locate leaks. Common points in order of likelihood: '
               '1) PCV hose and valve (Bank 1 cover), 2) IMT valve O-ring (RH side of manifold — '
               'replace with Viton), 3) Brake booster hose (large hose at manifold rear), '
               '4) Small vacuum lines at throttle body, 5) Intake runner gaskets (manifold off job).',
        'viton_upgrade': 'The IMT valve O-ring should ALWAYS be upgraded to Viton (FKM) fluorocarbon '
                         'rubber. Standard nitrile has poor resistance to fuel vapours and crankcase gases. '
                         'Viton is rated -20°C to +200°C vs nitrile -40°C to +120°C.',
        'parts': ['PCV valve and hose', 'IMT valve O-ring (Viton/FKM)', 'Brake booster vacuum hose',
                  'Vacuum hose assortment', 'Hose clamps'],
        'difficulty': 'Easy (hoses) to Medium (IMT O-ring, manifold gaskets)',
        'cost': '$15-100 AUD parts depending on scope',
        'related_dtcs': ['P0171 (Bank 1 lean)', 'P0174 (Bank 2 lean)'],
        'tags': ['vacuum', 'leak', 'idle', 'lean', 'hiss', 'p0171', 'p0174', 'stft', 'ltft',
                 'intake', 'gasket', 'pcv', 'imt', 'viton', 'brake booster', 'hose'],
    },
    {
        'title': 'Throttle Body Carbon Buildup / Electronic Throttle Failure',
        'symptoms': ['Rough idle', 'Hesitation from stop', 'Idle hunting (RPM bouncing)',
                     'P0507 code', 'Limp mode (P2106)', 'Stalling at junctions',
                     'P2111/P2112 (throttle stuck open/closed)', 'P2135 (TPS correlation)'],
        'cause': 'The electronic throttle body (drive-by-wire) accumulates carbon deposits on the '
                 'throttle plate and bore. This causes the plate to stick, and the ECU cannot maintain '
                 'stable idle. The throttle body motor can also fail internally. '
                 'After battery disconnect, throttle adaptations must be relearned or P0121/P2135 will set.',
        'fix': 'Remove throttle body (4 bolts). Clean with carb cleaner and a soft cloth — '
               'do NOT use a wire brush on the bore. After refitting, perform idle relearn: '
               'Key ON 30 seconds (do not start) → Start engine → Let idle 2 min without touching '
               'anything → Drive normally 15 min. If motor has failed, replace entire throttle body.',
        'parts': ['Throttle body gasket', 'Carb/throttle body cleaner', 'Throttle body assembly (if motor dead)'],
        'difficulty': 'Easy (cleaning) to Medium (replacement) — 45 min',
        'cost': '$8-25 AUD cleaning, $200-400 AUD for new throttle body',
        'related_dtcs': ['P0507 (idle speed high)', 'P2106 (throttle actuator control forced limited)',
                         'P2111 (stuck open)', 'P2112 (stuck closed)', 'P2135 (TPS correlation)',
                         'P0121/P0122/P0123 (TP sensor range)'],
        'tags': ['throttle', 'idle', 'hunting', 'stall', 'carbon', 'p0507', 'p2106',
                 'p2111', 'p2112', 'p2135', 'limp', 'hesitation', 'rough', 'p0121'],
    },
    {
        'title': 'MAF Sensor Contamination',
        'symptoms': ['Poor fuel economy', 'Sluggish acceleration', 'LTFT positive (lean correction)',
                     'Black smoke on hard acceleration', 'P0101/P0102/P0103 codes'],
        'cause': 'The hot-film MAF sensor element gets coated with oil vapour from the PCV system '
                 'and road grime. This causes it to underreport airflow, making the ECU think '
                 'less air is entering than actually is. The ECU commands less fuel → lean → LTFT rises. '
                 'Under WOT, the ECU goes open-loop rich → black smoke.',
        'fix': 'Remove MAF sensor (2 Torx screws). Spray with dedicated MAF cleaner only (CRC MAF cleaner). '
               'Let air dry completely (10+ min). Do NOT touch the hot-film element. '
               'Do NOT use carb cleaner, WD-40, or brake cleaner — they leave residue that destroys the element.',
        'parts': ['CRC MAF cleaner spray (or equivalent MAF-specific cleaner)'],
        'difficulty': 'Very easy — 15 minutes',
        'cost': '$10-15 AUD',
        'related_dtcs': ['P0101 (MAF out of range)', 'P0102 (MAF low)', 'P0103 (MAF high)'],
        'tags': ['maf', 'fuel', 'economy', 'lean', 'sluggish', 'power', 'ltft', 'p0101', 'p0102', 'p0103'],
    },
    {
        'title': 'Alternator Failure / Charging System',
        'symptoms': ['Battery warning light', 'Voltage below 13.5V at cruise', 'Dim headlights',
                     'Electrical gremlins', 'Battery keeps going flat', 'Whining noise from belt area'],
        'cause': 'The alternator brushes and bearings wear over time. The voltage regulator (internal) '
                 'can also fail. Often gives warning signs of gradually dropping voltage. '
                 'Belt tension should be checked first — a loose belt mimics alternator failure. '
                 'A failed diode causes parasitic drain (battery dies overnight even with good alternator output).',
        'fix': 'Check belt tension and condition first. Test alternator output: '
               'should read 13.8-14.4V at 1500+ RPM with headlights on. If low, replace alternator. '
               'Test for diode leakage: disconnect alternator B+ wire, check for current flow from '
               'battery to alternator with engine off — should be near zero.',
        'parts': ['Alternator (130A, Bosch or equivalent)', 'Drive belt (if worn)'],
        'difficulty': 'Medium — 1-2 hours',
        'cost': '$150-250 AUD for alternator',
        'related_dtcs': [],
        'tags': ['alternator', 'voltage', 'battery', 'charging', 'light', 'electrical', 'belt',
                 'parasitic', 'drain', 'whine'],
    },
    {
        'title': 'Jatco JF506E Transmission Solenoid Failure',
        'symptoms': ['Transmission stuck in 3rd gear (limp mode)', 'Harsh shifting',
                     'Delayed gear engagement', 'F4 flashing on dash', 'Cold-start limp that clears warm',
                     'Check engine light with transmission codes', 'Jerky transitions between gears'],
        'cause': 'The JF506E shift solenoids fail from electromagnetic coil insulation breakdown, '
                 'corrosion in the plunger mechanism, electrical connector corrosion, or dirty ATF '
                 'contaminating the valve body. Shift Solenoid C is the most common failure '
                 '(controls 2-4 shifts). Cold failures that clear when warm = early sign '
                 'of solenoid deterioration (resistance changes with temperature).',
        'fix': 'Scan for DTCs — P0750/P0755/P0760/P0765 identify which solenoid. '
               'Solenoid replacement requires dropping the transmission pan. Replace the solenoid '
               'and its O-ring. Always change filter and fluid at the same time. '
               'Reset TCM adaptives with diagnostic scanner after repair. '
               'Consider replacing all solenoids if one has failed — others are likely degraded.',
        'parts': ['Shift solenoid (specify A/B/C/D/E)', 'Solenoid O-ring', 'Transmission filter',
                  '4L Mercon V ATF', 'Transmission pan gasket'],
        'difficulty': 'Medium — 2-3 hours, transmission pan removal',
        'cost': '$80-200 AUD for solenoid kit + filter + fluid',
        'related_dtcs': ['P0750 (Solenoid A)', 'P0755 (Solenoid B)',
                         'P0760 (Solenoid C — most common)', 'P0765 (Solenoid D)',
                         'P0770 (Solenoid E)'],
        'tags': ['transmission', 'solenoid', 'limp', 'shift', 'harsh', 'p0750', 'p0755',
                 'p0760', 'p0765', 'p0770', 'jatco', 'jf506e', 'atf', 'gear', 'f4'],
    },
    {
        'title': 'PTU (Power Transfer Unit) Failure — AWD',
        'symptoms': ['Whining noise increasing with speed', 'Grinding/chunking noise',
                     'Oil leakage from centre/rear of vehicle', 'Vibration through drivetrain',
                     'Vehicle pulling or yawing'],
        'cause': 'The PTU is the weak point of the X-Type AWD system. It operates in a confined '
                 'space with limited cooling, causing thermal fluid breakdown. Factory "sealed for life" '
                 'means the fluid was never intended to be changed — but it MUST be. In Australian heat, '
                 'fluid degrades rapidly. Symptoms progress: slight whining → obvious whining under load → '
                 'grinding → bearing seizure (catastrophic, potential driveshaft lock-up).',
        'fix': 'Early (whining only): Vacuum-extract old fluid through filler port, refill with '
               'fresh 75W-90 synthetic. This captures 70-80% of fluid and can extend life significantly. '
               'Late (grinding): PTU replacement required. Available remanufactured or from wreckers.',
        'service_procedure': '1) Warm PTU with short drive. 2) Locate filler/breather port on top. '
                            '3) Insert vacuum pump tubing. 4) Extract old fluid (dark = bad). '
                            '5) Refill with 75W-90 synthetic to bottom of filler hole. '
                            '6) Service every 50,000 km in Australian conditions.',
        'parts': ['75W-90 synthetic differential fluid (1.5-2.0L)', 'Vacuum pump for extraction'],
        'difficulty': 'Easy (fluid change) to Hard (replacement)',
        'cost': '$30-60 AUD fluid change, $800-2000 AUD for PTU replacement',
        'related_dtcs': [],
        'tags': ['ptu', 'transfer', 'awd', 'whine', 'grinding', 'vibration', 'differential',
                 'driveshaft', 'bearing', 'oil', 'leak'],
    },
    {
        'title': 'Propshaft Centre Bearing Wear',
        'symptoms': ['Vibration at 60-100 km/h', 'Clunk when changing direction (drive/reverse)',
                     'Humming that increases with speed', 'Vibration worse under load'],
        'cause': 'The two-piece propshaft has a centre support bearing that wears. The rubber mount '
                 'deteriorates, allowing the shaft to run eccentric. Common on higher-mileage X-Types. '
                 'The Haldex coupling at the rear can also contribute to vibration if its fluid is old.',
        'fix': 'Replace the centre bearing assembly. This is a press-fit job — the shaft needs '
               'removing and the old bearing pressing out. Some workshops replace the whole propshaft. '
               'Also change Haldex fluid if it has not been done.',
        'parts': ['Centre bearing assembly', 'Haldex fluid (if due)'],
        'difficulty': 'Medium-Hard — needs a press',
        'cost': '$80-130 AUD parts, $250-400 AUD fitted',
        'related_dtcs': [],
        'tags': ['vibration', 'propshaft', 'bearing', 'clunk', 'hum', 'awd', 'haldex'],
    },
    {
        'title': 'ABS Module Failure',
        'symptoms': ['ABS warning light', 'Traction control light', 'DSC warning',
                     'U0121 code', 'No ABS function', 'All warning lights on dash'],
        'cause': 'The ABS module (hydraulic unit + ECU) has internal solder joints that crack '
                 'from thermal cycling. Sometimes just the ECU portion fails, sometimes the pump motor. '
                 'The module is located in the engine bay and is exposed to heat and moisture.',
        'fix': 'Specialist repair/rebuild is cost-effective. Australian options include sending to '
               'specialist remanufacturers. Full replacement with new/used is very expensive.',
        'parts': ['ABS module rebuild service'],
        'difficulty': 'Easy removal (4 brake lines + electrical connector), specialist rebuild',
        'cost': '$250-450 AUD for rebuild service',
        'related_dtcs': ['U0121 (lost communication with ABS)', 'C1095', 'C1185'],
        'tags': ['abs', 'brakes', 'traction', 'dsc', 'warning', 'light', 'u0121', 'module'],
    },
    {
        'title': 'Rear Subframe Bushes',
        'symptoms': ['Clunking over bumps from rear', 'Vague rear end handling',
                     'Uneven rear tire wear', 'Knocking noise on rough roads'],
        'cause': 'The rear subframe mounting bushes deteriorate with age and Australian conditions '
                 '(heat accelerates rubber degradation). The rubber tears and allows the subframe '
                 'to move, causing clunking and poor rear-end geometry.',
        'fix': 'Replace rear subframe bushes. Polybush (polyurethane) replacements last longer '
               'than OEM rubber. Requires supporting/lowering the subframe.',
        'parts': ['4x rear subframe bushes (Polybush recommended)', 'New bolts (175 Nm)'],
        'difficulty': 'Hard — 4-6 hours, need to support subframe',
        'cost': '$60-130 AUD parts (Polybush), $500-800 AUD fitted',
        'related_dtcs': [],
        'tags': ['clunk', 'rear', 'subframe', 'bush', 'handling', 'knock', 'suspension'],
    },
    {
        'title': 'Cooling System — Expansion Tank / Radiator / Water Pump',
        'symptoms': ['Coolant loss without visible drip', 'Sweet smell under bonnet',
                     'Overheating', 'Foaming in expansion tank', 'Coolant on ground',
                     'Grinding noise from front of engine (water pump)'],
        'cause': 'Multiple cooling system weak points:\n'
                 '1. Plastic expansion tank cracks from thermal cycling (80,000-150,000 km)\n'
                 '2. Radiator develops pinhole leaks at seams\n'
                 '3. Water pump bearing/seal failure (grinding noise, weep hole drip)\n'
                 '4. Thermostat housing (see separate entry)\n'
                 'Australian heat accelerates all cooling system failures.',
        'fix': 'Pressure test system to identify leak source. Expansion tank is straightforward '
               'replacement. Water pump requires timing cover access — budget 2-3 hours. '
               'Radiator replacement is 1-2 hours. Always flush system and refill with correct OAT coolant.',
        'parts': ['Expansion tank', 'Water pump', 'Radiator', 'Coolant (7.5L OAT)'],
        'difficulty': 'Easy (tank) to Medium (pump/radiator)',
        'cost': '$50-300 AUD depending on component',
        'related_dtcs': ['P0125', 'P0116-P0118'],
        'tags': ['coolant', 'overheating', 'leak', 'radiator', 'water pump', 'expansion',
                 'tank', 'temperature', 'steam'],
    },
    {
        'title': 'EGR Valve Carbon Buildup',
        'symptoms': ['Rough idle', 'Hesitation', 'P0401 code', 'Poor fuel economy',
                     'Slight misfire at low RPM', 'Failed emissions test'],
        'cause': 'The EGR valve and its passages accumulate carbon deposits, preventing proper '
                 'exhaust gas recirculation. The valve sticks partially open or closed.',
        'fix': 'Remove EGR valve (bolted to intake manifold). Clean valve and passages with '
               'carb cleaner and a wire brush. Check vacuum actuator hose for cracks. Replace gasket.',
        'parts': ['EGR gasket', 'Carb cleaner'],
        'difficulty': 'Easy-Medium — 1 hour',
        'cost': '$8-25 AUD',
        'related_dtcs': ['P0401 (EGR flow insufficient)'],
        'tags': ['egr', 'carbon', 'idle', 'emissions', 'p0401', 'rough', 'hesitation'],
    },
    {
        'title': 'Camshaft Position Sensor Failure',
        'symptoms': ['No start / long crank', 'P0340/P0345 codes', 'Engine cuts out randomly',
                     'Rough running', 'Stalling'],
        'cause': 'The CMP sensors are located behind the timing cover and exposed to heat and oil vapour. '
                 'The connector can corrode. If the thermostat housing is leaking (common), coolant '
                 'can reach the CMP connector and cause intermittent failures.',
        'fix': 'Replace the CMP sensor — cheap part, accessible from top of engine. '
               'Check connector for green corrosion. Clean with electrical contact cleaner. '
               'Fix any thermostat housing leak to prevent recurrence.',
        'parts': ['Camshaft position sensor (Bank 1 or Bank 2)'],
        'difficulty': 'Easy — 20 minutes',
        'cost': '$25-50 AUD',
        'related_dtcs': ['P0340 (CMP Bank 1)', 'P0345 (CMP Bank 2)'],
        'tags': ['camshaft', 'sensor', 'no start', 'crank', 'stall', 'p0340', 'p0345', 'cut out'],
    },
    {
        'title': 'Fuel Pump Weak / Failing',
        'symptoms': ['Long crank to start', 'Hesitation under hard acceleration',
                     'P1235 code', 'Engine cuts out at high load', 'Whining from rear'],
        'cause': 'The in-tank fuel pump wears over time. Running on low fuel accelerates wear '
                 '(the fuel cools the pump). The fuel pump relay can also fail intermittently.',
        'fix': 'First: check fuel pump relay (engine bay fuse box — swap with identical relay). '
               'Listen for pump prime: key to ON, you should hear a 2-second whir from the rear. '
               'If no prime sound, check relay and wiring. Pump access is under the rear seat.',
        'parts': ['Fuel pump assembly (in-tank)', 'Fuel pump relay (if faulty)'],
        'difficulty': 'Medium — rear seat removal, fuel system',
        'cost': '$120-250 AUD for pump',
        'related_dtcs': ['P1235 (fuel pump control)'],
        'tags': ['fuel', 'pump', 'start', 'hesitation', 'p1235', 'relay', 'stall', 'crank'],
    },
    {
        'title': 'O2 Sensor Degradation',
        'symptoms': ['Poor fuel economy', 'Fuel trim drift', 'P0131/P0133/P0134/P0137/P0139/P0140 codes',
                     'Slight rich or lean running', 'Failed emissions'],
        'cause': 'Upstream O2 sensors degrade with age and become "lazy" — slow to respond to air/fuel '
                 'changes. This causes inaccurate fuel trim correction. Expected lifespan 120,000-160,000 km.',
        'fix': 'Replace upstream O2 sensors (Bank 1 Sensor 1 and/or Bank 2 Sensor 1). '
               'Use OEM-equivalent (Bosch, Denso, NTK). Check exhaust flex joint for leaks first — '
               'an exhaust leak before the sensor gives the same symptoms.',
        'parts': ['O2 sensor upstream (Bank 1 and/or Bank 2)'],
        'difficulty': 'Easy — 30 minutes per sensor (penetrating oil helps)',
        'cost': '$50-100 AUD per sensor',
        'related_dtcs': ['P0131-P0140 (O2 sensor circuits)', 'P0133/P0139 (slow response)'],
        'tags': ['o2', 'oxygen', 'sensor', 'fuel', 'economy', 'emissions', 'trim',
                 'p0131', 'p0133', 'p0134', 'p0137', 'p0139', 'p0140', 'lambda'],
    },
    {
        'title': 'EVAP Purge Valve Stuck Open',
        'symptoms': ['Rich running at idle', 'Fuel smell', 'STFT negative (rich correction)',
                     'P0443 code', 'Hard start when hot', 'Rough idle after refuelling'],
        'cause': 'The EVAP purge valve on the firewall side of the engine bay can stick open, '
                 'allowing fuel vapour to flood the intake at idle.',
        'fix': 'Locate purge valve (firewall, small solenoid with vacuum hoses). '
               'Test: apply 12V — should click. Blow through it — should only flow when energised. '
               'If it flows freely without power, it is stuck open. Replace.',
        'parts': ['EVAP purge valve solenoid'],
        'difficulty': 'Easy — 20 minutes',
        'cost': '$30-60 AUD',
        'related_dtcs': ['P0443 (EVAP purge circuit)', 'P0455 (EVAP large leak)'],
        'tags': ['purge', 'evap', 'rich', 'fuel', 'smell', 'p0443', 'p0455', 'vapour', 'idle'],
    },
    {
        'title': 'Catalytic Converter Degradation',
        'symptoms': ['P0420/P0430 codes', 'Rotten egg smell', 'Reduced power at high RPM',
                     'Rattling from underneath', 'Failed emissions'],
        'cause': 'The catalytic converter substrate breaks down, especially if the engine has been '
                 'running rich (from bad coils, leaking injectors). The X-Type has 2 pre-cats and '
                 '1 main cat. Rattling = substrate has broken apart inside.',
        'fix': 'FIX THE ROOT CAUSE FIRST (misfires, fuel trim issues). Running rich kills cats. '
               'Clear codes and retest after root cause repair. If cat is genuinely failed, '
               'aftermarket replacement cats available. Pre-cats are welded to manifolds.',
        'parts': ['Catalytic converter (main or pre-cat)'],
        'difficulty': 'Medium-Hard — exhaust work',
        'cost': '$300-600 AUD parts + fitting',
        'related_dtcs': ['P0420 (Cat efficiency below threshold B1)',
                         'P0430 (Cat efficiency below threshold B2)'],
        'tags': ['cat', 'catalyst', 'emissions', 'p0420', 'p0430', 'exhaust',
                 'rattle', 'smell', 'egg'],
    },
    {
        'title': 'GEM Module Failure (Electrical Gremlins)',
        'symptoms': ['Windows not working (one or all)', 'Central locking unresponsive',
                     'Interior lights stay on', 'Battery drains overnight',
                     'Dashboard warning lights randomly illuminated'],
        'cause': 'The General Electronic Module (GEM) controls body electrics and can fail to enter '
                 'sleep mode, causing parasitic drain. Internal circuit board failures cause loss of '
                 'window, lock, or lighting functions. Located behind instrument panel, RH side.',
        'fix': 'Diagnose parasitic draw first (disconnect fuses one at a time, measure current). '
               'If GEM is the culprit, replacement requires programming — not a simple swap. '
               'Some specialists can repair circuit boards.',
        'parts': ['GEM module (requires programming to vehicle)'],
        'difficulty': 'Hard — specialist programming required',
        'cost': '$400-1200 AUD including programming',
        'related_dtcs': [],
        'tags': ['gem', 'module', 'window', 'lock', 'light', 'parasitic', 'drain',
                 'battery', 'electrical', 'gremlin'],
    },
    {
        'title': 'Window Regulator Failure',
        'symptoms': ['Window moves slowly or not at all', 'Grinding noise during operation',
                     'Window switch unresponsive', 'Window drops into door'],
        'cause': 'Electric motor burnout, mechanical regulator cable breakage, or motor gear stripping. '
                 'Common on all X-Type doors after 100,000+ km.',
        'fix': 'Replace full window regulator/motor assembly. Door card removal required. '
               'Mark glass position before removing.',
        'parts': ['Window regulator assembly with motor'],
        'difficulty': 'Medium — 2-3 hours per window',
        'cost': '$150-350 AUD per window',
        'related_dtcs': [],
        'tags': ['window', 'regulator', 'motor', 'grinding', 'stuck', 'slow', 'door'],
    },
]


# ═══════════════════════════════════════════════════════════════════
#  Diagnostic Trouble Codes — Comprehensive Reference
#  The LLM should match live DTCs against this database and cross-
#  reference with COMMON_PROBLEMS and live telemetry to reason
#  about root causes.
# ═══════════════════════════════════════════════════════════════════

DTC_REFERENCE = {
    # Fuel System
    'P0171': {'desc': 'System too lean — Bank 1', 'action': 'Adaptive fuel metering inhibited, purge inhibited',
              'causes': ['Vacuum leak', 'MAF sensor fault', 'Fuel pressure low', 'O2 sensor fault',
                         'PCV hose split', 'IMT O-ring failure', 'Intake manifold gasket leak']},
    'P0172': {'desc': 'System too rich — Bank 1', 'action': 'Adaptive fuel metering inhibited',
              'causes': ['Fuel pressure high', 'Leaking injector', 'Purge valve stuck open',
                         'O2 sensor fault', 'MAF sensor fault']},
    'P0174': {'desc': 'System too lean — Bank 2', 'action': 'Adaptive fuel metering inhibited, purge inhibited',
              'causes': ['Vacuum leak', 'MAF sensor fault', 'Fuel pressure low', 'O2 sensor fault']},
    'P0175': {'desc': 'System too rich — Bank 2', 'action': 'Adaptive fuel metering inhibited',
              'causes': ['Fuel pressure high', 'Leaking injector', 'Purge valve stuck open']},

    # MAF / MAP / IAT / ECT sensors
    'P0101': {'desc': 'MAF sensor out of range', 'action': 'Default air mass used, fuel adaptations inhibited',
              'causes': ['MAF sensor contaminated', 'MAF wiring fault', 'MAF sensor failure']},
    'P0102': {'desc': 'MAF sensor circuit low voltage', 'action': 'Default air mass used',
              'causes': ['MAF sensor ground open', 'MAF sensing circuit open', 'MAF failure']},
    'P0103': {'desc': 'MAF sensor circuit high voltage', 'action': 'Default air mass used',
              'causes': ['MAF sensor ground open', 'MAF sensing circuit shorted', 'MAF failure']},
    'P0107': {'desc': 'MAP sensor circuit low voltage', 'action': 'Default 1.013 BAR used',
              'causes': ['MAP circuit open/short to ground', 'MAP supply circuit open', 'MAP failure']},
    'P0108': {'desc': 'MAP sensor circuit high voltage', 'action': 'Default 1.013 BAR used',
              'causes': ['MAP ground open', 'MAP sense circuit shorted high', 'MAP failure']},
    'P0111': {'desc': 'IAT sensor out of range', 'action': 'Default air temp used, fuel adaptations inhibited',
              'causes': ['IAT sensor ground fault', 'IAT sensing circuit open', 'IAT sensor failure']},
    'P0112': {'desc': 'IAT sensor circuit low voltage', 'action': 'Default air temp used',
              'causes': ['IAT wiring fault', 'IAT sensor failure']},
    'P0113': {'desc': 'IAT sensor circuit high voltage', 'action': 'Default air temp used',
              'causes': ['IAT wiring fault', 'IAT sensor failure']},
    'P0116': {'desc': 'ECT sensor out of range', 'action': 'Default coolant temp, enrichment mode, max RPM reduced',
              'causes': ['ECT wiring fault', 'ECT sensor failure', 'Thermostat stuck']},
    'P0117': {'desc': 'ECT sensor circuit low voltage', 'action': 'Default coolant temp, enrichment mode',
              'causes': ['ECT wiring fault', 'ECT sensor failure']},
    'P0118': {'desc': 'ECT sensor circuit high voltage', 'action': 'Default coolant temp, enrichment mode',
              'causes': ['ECT wiring fault', 'ECT sensor failure']},
    'P0125': {'desc': 'ECT insufficient for closed-loop fuelling', 'action': 'Open-loop fuel metering',
              'causes': ['Thermostat stuck open', 'ECT sensor fault', 'Engine mechanical problem']},

    # Throttle
    'P0121': {'desc': 'Throttle position sensor out of range', 'action': 'TP default value, cruise inhibited',
              'causes': ['Throttle adaptations not performed after battery disconnect',
                         'TP sensor disconnected', 'TP wiring fault', 'Throttle motor failure']},
    'P0122': {'desc': 'TP sensor circuit low voltage', 'action': 'TP default, cruise inhibited',
              'causes': ['TP sensor disconnected', 'TP wiring open/high resistance', 'Throttle motor failure']},
    'P0123': {'desc': 'TP sensor circuit high voltage', 'action': 'TP default, cruise inhibited',
              'causes': ['TP sensor fault', 'TP wiring shorted', 'Throttle motor failure']},
    'P0507': {'desc': 'Idle speed higher than expected', 'action': 'None — informational',
              'causes': ['Vacuum leak', 'Dirty throttle body', 'IAC fault', 'PCV leak']},
    'P2106': {'desc': 'Throttle actuator forced limited power', 'action': 'Limp mode — reduced power',
              'causes': ['Throttle body motor failure', 'TPS correlation fault', 'Wiring issue']},
    'P2111': {'desc': 'Throttle stuck open', 'action': 'Limp mode',
              'causes': ['Throttle motor failure', 'Carbon buildup', 'Wiring fault']},
    'P2112': {'desc': 'Throttle stuck closed', 'action': 'Limp mode',
              'causes': ['Throttle motor failure', 'Carbon buildup', 'Wiring fault']},
    'P2135': {'desc': 'TP sensor 1/2 correlation', 'action': 'Limp mode, cruise inhibited',
              'causes': ['TP sensor failure', 'Wiring fault between dual TPS signals',
                         'Throttle adaptations needed after battery disconnect']},

    # Misfires
    'P0300': {'desc': 'Random/multiple cylinder misfire', 'action': 'Catalyst damage possible',
              'causes': ['Vacuum leak (affects all cylinders)', 'Fuel pressure issue',
                         'Multiple coil failures', 'Bad fuel', 'Low compression']},
    'P0301': {'desc': 'Cylinder 1 misfire', 'action': 'Speed control inhibited',
              'causes': ['Coil pack failure', 'Spark plug fouled/worn', 'Injector fault',
                         'Oil in plug well (valve cover gasket)', 'Low compression']},
    'P0302': {'desc': 'Cylinder 2 misfire', 'action': 'Speed control inhibited',
              'causes': ['Coil pack failure', 'Spark plug fouled/worn', 'Injector fault',
                         'Oil in plug well', 'Low compression']},
    'P0303': {'desc': 'Cylinder 3 misfire', 'action': 'Speed control inhibited',
              'causes': ['Coil pack failure', 'Spark plug fouled/worn', 'Injector fault',
                         'Oil in plug well', 'Low compression']},
    'P0304': {'desc': 'Cylinder 4 misfire', 'action': 'Speed control inhibited',
              'causes': ['Coil pack failure', 'Spark plug fouled/worn', 'Injector fault',
                         'Oil in plug well', 'Low compression']},
    'P0305': {'desc': 'Cylinder 5 misfire', 'action': 'Speed control inhibited',
              'causes': ['Coil pack failure', 'Spark plug fouled/worn', 'Injector fault',
                         'Oil in plug well', 'Low compression']},
    'P0306': {'desc': 'Cylinder 6 misfire', 'action': 'Speed control inhibited',
              'causes': ['Coil pack failure', 'Spark plug fouled/worn', 'Injector fault',
                         'Oil in plug well', 'Low compression']},
    'P0351': {'desc': 'Ignition coil A circuit', 'action': 'Misfire detection active',
              'causes': ['Coil pack failure', 'Coil connector fault', 'ECM driver circuit fault']},
    'P0352': {'desc': 'Ignition coil B circuit', 'action': 'Misfire detection active',
              'causes': ['Coil pack failure', 'Coil connector fault', 'ECM driver circuit fault']},
    'P0353': {'desc': 'Ignition coil C circuit', 'action': 'Misfire detection active',
              'causes': ['Coil pack failure', 'Coil connector fault', 'ECM driver circuit fault']},
    'P0354': {'desc': 'Ignition coil D circuit', 'action': 'Misfire detection active',
              'causes': ['Coil pack failure', 'Coil connector fault', 'ECM driver circuit fault']},
    'P0355': {'desc': 'Ignition coil E circuit', 'action': 'Misfire detection active',
              'causes': ['Coil pack failure', 'Coil connector fault', 'ECM driver circuit fault']},
    'P0356': {'desc': 'Ignition coil F circuit', 'action': 'Misfire detection active',
              'causes': ['Coil pack failure', 'Coil connector fault', 'ECM driver circuit fault']},

    # O2 sensors
    'P0131': {'desc': 'O2 sensor circuit malfunction — B1S1', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 sensor signal circuit fault', 'O2 sensor failure', 'Exhaust leak before sensor']},
    'P0132': {'desc': 'O2 sensor circuit malfunction — B1S2', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 sensor signal circuit fault', 'O2 sensor failure']},
    'P0133': {'desc': 'O2 sensor slow response — B1S1', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 sensor contaminated', 'O2 sensor aging', 'O2 wiring fault']},
    'P0134': {'desc': 'O2 sensor no activity — B1S1', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 sensor signal open', 'O2 sensor supply open', 'O2 sensor failure']},
    'P0137': {'desc': 'O2 sensor circuit malfunction — B2S1', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 sensor signal circuit fault', 'O2 sensor failure']},
    'P0139': {'desc': 'O2 sensor slow response — B2S1', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 sensor contaminated', 'O2 sensor aging', 'O2 wiring fault']},
    'P0140': {'desc': 'O2 sensor no activity — B2S1', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 sensor signal open', 'O2 sensor supply open', 'O2 sensor failure']},

    # O2 heater circuits
    'P0031': {'desc': 'O2 heater circuit low — B1S1', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 heater wiring fault', 'Heater element failure', 'ECM driver fault']},
    'P0032': {'desc': 'O2 heater circuit high — B1S1', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 heater shorted to voltage', 'Heater element failure']},
    'P0135': {'desc': 'O2 heater circuit malfunction — B1S1', 'action': 'Fuel adaptations inhibited',
              'causes': ['O2 heater circuit open/short', 'Heater element failure']},

    # Catalytic converters
    'P0420': {'desc': 'Catalyst efficiency below threshold — Bank 1', 'action': 'Informational',
              'causes': ['Catalytic converter degraded', 'Exhaust leak before cat',
                         'O2 sensor fault', 'Engine running rich (fix root cause first!)']},
    'P0430': {'desc': 'Catalyst efficiency below threshold — Bank 2', 'action': 'Informational',
              'causes': ['Catalytic converter degraded', 'Exhaust leak before cat', 'O2 sensor fault']},

    # Camshaft
    'P0010': {'desc': 'Camshaft position actuator circuit — Bank 1', 'action': 'Speed control inhibited',
              'causes': ['VVT solenoid circuit fault', 'VVT solenoid failure', 'ECM driver fault']},
    'P0020': {'desc': 'Camshaft position actuator circuit — Bank 2', 'action': 'Speed control inhibited',
              'causes': ['VVT solenoid circuit fault', 'VVT solenoid failure', 'ECM driver fault']},
    'P0340': {'desc': 'CMP sensor circuit malfunction — Bank 1', 'action': 'Engine may not start',
              'causes': ['CMP sensor failure', 'CMP wiring fault', 'Connector corrosion (coolant leak)']},
    'P0345': {'desc': 'CMP sensor circuit malfunction — Bank 2', 'action': 'Engine may not start',
              'causes': ['CMP sensor failure', 'CMP wiring fault', 'Connector corrosion']},

    # Fuel injectors
    'P0201': {'desc': 'Injector circuit — Cylinder 1', 'action': 'Fuel cut-off cyl 1',
              'causes': ['Injector circuit open/short', 'Injector failure', 'ECM driver fault']},
    'P0202': {'desc': 'Injector circuit — Cylinder 2', 'action': 'Fuel cut-off cyl 2',
              'causes': ['Injector circuit open/short', 'Injector failure', 'ECM driver fault']},
    'P0203': {'desc': 'Injector circuit — Cylinder 3', 'action': 'Fuel cut-off cyl 3',
              'causes': ['Injector circuit open/short', 'Injector failure', 'ECM driver fault']},
    'P0204': {'desc': 'Injector circuit — Cylinder 4', 'action': 'Fuel cut-off cyl 4',
              'causes': ['Injector circuit open/short', 'Injector failure', 'ECM driver fault']},
    'P0205': {'desc': 'Injector circuit — Cylinder 5', 'action': 'Fuel cut-off cyl 5',
              'causes': ['Injector circuit open/short', 'Injector failure', 'ECM driver fault']},
    'P0206': {'desc': 'Injector circuit — Cylinder 6', 'action': 'Fuel cut-off cyl 6',
              'causes': ['Injector circuit open/short', 'Injector failure', 'ECM driver fault']},

    # EVAP
    'P0443': {'desc': 'EVAP purge circuit malfunction', 'action': 'Purge inhibited',
              'causes': ['Purge valve stuck open', 'Purge valve circuit fault', 'Purge valve failure']},
    'P0455': {'desc': 'EVAP system large leak', 'action': 'Purge inhibited',
              'causes': ['Fuel cap loose', 'EVAP hose cracked', 'Purge valve fault', 'Charcoal canister']},

    # EGR
    'P0401': {'desc': 'EGR flow insufficient', 'action': 'EGR function limited',
              'causes': ['EGR valve carbon buildup', 'EGR vacuum line fault', 'EGR passages blocked']},

    # Transmission
    'P0706': {'desc': 'Transmission range sensor circuit', 'action': 'Limp mode possible',
              'causes': ['Range sensor failure', 'Wiring fault', 'J-gate switch fault']},
    'P0712': {'desc': 'Transmission fluid temp sensor low', 'action': 'Default temp used',
              'causes': ['Temp sensor failure', 'Wiring fault']},
    'P0750': {'desc': 'Shift Solenoid A malfunction', 'action': 'Limp mode (3rd gear)',
              'causes': ['Solenoid failure', 'Solenoid circuit fault', 'Dirty ATF', 'Connector corrosion']},
    'P0755': {'desc': 'Shift Solenoid B malfunction', 'action': 'Limp mode',
              'causes': ['Solenoid failure', 'Solenoid circuit fault', 'Dirty ATF']},
    'P0760': {'desc': 'Shift Solenoid C malfunction — MOST COMMON', 'action': 'Limp mode (3rd gear)',
              'causes': ['Solenoid C failure (controls 2-4 shifts)', 'Connector corrosion', 'Dirty ATF']},
    'P0765': {'desc': 'Shift Solenoid D malfunction', 'action': 'Limp mode',
              'causes': ['Solenoid failure', 'Solenoid circuit fault', 'Dirty ATF']},
    'P0770': {'desc': 'Shift Solenoid E malfunction', 'action': 'Limp mode',
              'causes': ['Solenoid failure', 'Solenoid circuit fault']},

    # Vehicle speed / Fuel pump
    'P0500': {'desc': 'Vehicle speed sensor malfunction', 'action': 'Cruise inhibited',
              'causes': ['VSS failure', 'VSS wiring fault', 'ABS module communication fault']},
    'P1235': {'desc': 'Fuel pump control circuit', 'action': 'Engine may stall',
              'causes': ['Fuel pump relay fault', 'Fuel pump wiring', 'Fuel pump failure']},

    # Communication
    'U0121': {'desc': 'Lost communication with ABS module', 'action': 'ABS/TC/DSC disabled',
              'causes': ['ABS module failure', 'CAN bus wiring fault', 'Module power supply fault']},
}


# ═══════════════════════════════════════════════════════════════════
#  Cruise Control Logic — How ECM Disables Cruise
#  Reference for the LLM when diagnosing "Cruise Unavailable"
# ═══════════════════════════════════════════════════════════════════

CRUISE_CONTROL_LOGIC = {
    'description': 'The ECM disables cruise control as a safety measure when powertrain faults are '
                   'detected. Cruise will not function until ALL triggering DTCs are resolved and '
                   'cleared. After clearing, 80-160 km of normal driving may be needed for adaptives.',
    'triggering_faults': [
        'Any misfire code (P0300-P0306)',
        'Throttle faults (P0121-P0123, P2106, P2111, P2112, P2135)',
        'MAF/MAP faults (P0101-P0103, P0107-P0108)',
        'Lean/rich codes (P0171, P0172, P0174, P0175)',
        'Transmission faults (P0750, P0755, P0760, P0765)',
        'Vehicle speed sensor (P0500-P0502)',
        'O2 sensor faults (P0130-P0159)',
        'VVT faults (P0010, P0020)',
        'Camshaft sensor faults (P0340, P0345)',
    ],
    'resolution_steps': [
        '1. Scan for ALL active DTCs — not just the obvious one',
        '2. Fix the root cause (e.g., coil pack, vacuum leak, solenoid)',
        '3. Clear all DTCs with diagnostic scanner',
        '4. Drive 80-160 km for adaptive strategy relearning',
        '5. Cruise should become available once monitors pass',
    ],
    'note': 'Vacuum leaks are the sneakiest cause — they trigger lean codes (P0171/P0174) which '
            'disable cruise, but the vacuum leak itself may not be obvious without a smoke test.',
}


# ═══════════════════════════════════════════════════════════════════
#  Service Schedule & Intervals — Australian Conditions
# ═══════════════════════════════════════════════════════════════════

SERVICE_SCHEDULE = [
    {'interval': 'Every 8,000 km / 6 months', 'item': 'Engine oil & filter change',
     'details': '5.5L 5W-30 (WSS-M2C913-A/B). Sump plug torque: 25 Nm. '
                'Reduced from 10,000 km factory interval due to Australian heat.'},
    {'interval': 'Every 16,000 km / 12 months', 'item': 'Air filter replacement',
     'details': 'Panel filter in airbox. Check MAF sensor condition while open. '
                'Reduced interval due to Australian dust.'},
    {'interval': 'Every 16,000 km / 12 months', 'item': 'Pollen/cabin filter',
     'details': 'Located behind glovebox. Remove glovebox liner to access.'},
    {'interval': 'Every 30,000 km / 24 months', 'item': 'Spark plugs',
     'details': 'AGSF-32PM or NGK ITR6F-13, gap 1.3mm. Torque: 15 Nm. Anti-seize on threads. '
                'Check plug wells for oil (valve cover gasket).'},
    {'interval': 'Every 24 months', 'item': 'Brake fluid flush',
     'details': 'DOT 4 fluid. Bleed all four corners: RR → LR → RF → LF order.'},
    {'interval': 'Every 50,000 km', 'item': 'Transmission fluid & filter change',
     'details': 'Drain and fill: ~4L Mercon V. Total capacity 9.5L. '
                'Drop pan, replace filter, clean magnets, refit, fill through dipstick tube. '
                'Reset TCM adaptives with scanner.'},
    {'interval': 'Every 60,000 km', 'item': 'Haldex coupling fluid',
     'details': 'Haldex AOC fluid. Drain plug on rear coupling. Fill until it overflows. '
                'Critical for AWD function.'},
    {'interval': 'Every 50,000 km', 'item': 'PTU fluid change (vacuum extraction)',
     'details': 'No drain plug — vacuum extract through filler port. Refill with 75W-90 synthetic. '
                'ESSENTIAL in Australian conditions. Factory "sealed for life" is false.'},
    {'interval': 'Every 60,000 km', 'item': 'Coolant replacement',
     'details': '7.5L OAT coolant (orange/pink). Drain from radiator petcock. Bleed by running '
                'engine with expansion tank cap off until thermostat opens and air purges.'},
    {'interval': 'Every 100,000 km', 'item': 'Drive belt replacement',
     'details': 'Serpentine belt. Check for cracks, glazing, or chirping. '
                'Automatic tensioner — check it holds tension and does not oscillate.'},
    {'interval': 'Every 100,000 km', 'item': 'Rear differential fluid',
     'details': '75W-90 LS synthetic, 1.2-1.5L. Conventional drain/fill plugs.'},
    {'interval': 'As needed', 'item': 'Brake pads and discs',
     'details': 'Minimum pad thickness: 2mm. Front discs min: 22mm. Rear discs min: 8mm. '
                'Alloy wheel bolts: 103 Nm. Steel wheel bolts: 80 Nm.'},
    {'interval': 'Every 20,000 km', 'item': 'Inspect vacuum hoses and PCV system',
     'details': 'Check PCV hose, IMT valve O-ring, brake booster hose, all small vacuum lines. '
                'Squeeze test — hoses should be firm, not mushy or cracked.'},
    {'interval': 'Every 20,000 km', 'item': 'Inspect CV boots and propshaft',
     'details': 'Check for split/torn CV boots (grease loss). Listen for centre bearing noise. '
                'Check propshaft for play at joints.'},
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
        'title': "Engine Stall — Won't Restart",
        'steps': [
            'Turn hazards on. Ensure vehicle is in Park (auto) or Neutral.',
            'Wait 30 seconds, then try starting again. Hold starter for max 10 seconds.',
            "If cranks but won't fire: check fuel pump prime (key ON — listen for whir from rear).",
            'If no prime sound: check fuel pump relay in engine bay fuse box. Swap with identical relay.',
            "If cranks strongly but no fire: possible CMP sensor failure (P0340/P0345). "
            "Try disconnecting and reconnecting the CMP sensor connector.",
            "If won't crank at all: check battery terminals for corrosion. "
            "Clean and retighten. Try jump start.",
            'If starts then dies: possible throttle body failure (P2106 limp mode). '
            'Try key off → key on → start with NO throttle input.',
        ],
    },
    {
        'title': 'Limp Mode (Reduced Power)',
        'steps': [
            'Limp mode limits RPM to ~2500 and reduces power to protect the engine/transmission.',
            "Safe to drive slowly — just won't have much power.",
            'Pull over when convenient. Turn engine off, wait 30 seconds, restart.',
            'If limp mode clears: likely a transient sensor glitch. Monitor with DRIFTER.',
            'If limp mode persists: check DRIFTER dashboard for DTC codes.',
            'Common causes: throttle body fault (P2106/P2111/P2112), '
            'TPS correlation (P2135), MAF fault, or transmission solenoid fault.',
            'Drive gently to home/mechanic. Do not force high RPM in limp mode.',
        ],
    },
    {
        'title': 'Battery / Charging Failure',
        'steps': [
            'If battery light comes on while driving: alternator may have failed.',
            'Turn off non-essential electrics: heated seats, rear screen, radio, phone charger.',
            'Keep headlights on if needed for safety.',
            'You have roughly 20-40 minutes of driving on battery alone.',
            'Drive directly to nearest safe stopping point.',
            'Do NOT turn off the engine — you may not be able to restart.',
            'If voltage drops below 11V: power steering may become heavy, engine may die.',
        ],
    },
    {
        'title': 'Tyre Pressure Loss / Puncture',
        'steps': [
            'If sudden pressure drop: slow down gradually. Do not brake hard.',
            'Pull over to a safe, flat area away from traffic.',
            'The X-Type spare is a space-saver under the boot floor.',
            'Jack point front: behind front wheel arch seam. Rear: in front of rear wheel arch seam.',
            'Alloy wheel bolts: 103 Nm. Do not overtighten.',
            'Space-saver max speed: 80 km/h. Drive to a tyre shop promptly.',
        ],
    },
    {
        'title': 'Coolant Loss Warning',
        'steps': [
            'Pull over ASAP — do NOT continue with coolant temp in RED.',
            'Once stopped, leave engine idling for 2 minutes (if not critical).',
            'Most common cause is thermostat housing crack — look for coolant pooling '
            'in the V of the engine (between cam covers).',
            'Emergency: Let engine cool completely. Top up with water. Drive slowly.',
            'Temporary repair: JB Weld epoxy on small cracks can get you home.',
            'Call roadside assist if coolant is pouring out — do not drive without coolant.',
        ],
    },
    {
        'title': 'Transmission Limp Mode (3rd Gear Only)',
        'steps': [
            'Transmission defaults to 3rd gear only — this is a protection mode.',
            'Safe to drive slowly to home or mechanic.',
            'Try: engine off, wait 60 seconds, restart. May clear transient fault.',
            'If cold-start limp that clears when warm: early solenoid failure — schedule service.',
            'Check ATF level and colour (dipstick). Dark or burnt smell = urgent service needed.',
            'Do NOT force acceleration in limp mode.',
            'DRIFTER dashboard will show transmission-related DTCs.',
        ],
    },
]


# ═══════════════════════════════════════════════════════════════════
#  Torque Specs — Quick Reference (Comprehensive)
# ═══════════════════════════════════════════════════════════════════

TORQUE_SPECS = {
    # Engine
    'Sump drain plug': '25 Nm',
    'Oil filter housing': '25 Nm',
    'Spark plugs': '15 Nm — CRITICAL: aluminium heads, do NOT overtighten',
    'Coil pack bolts': '6 Nm',
    'Valve cover bolts': '3.6-5.5 Nm (cross pattern)',
    'Intake manifold bolts': '10 Nm',
    'Exhaust manifold nuts': '25 Nm',
    'Thermostat housing': '10 Nm',
    'EGR valve bolts': '10 Nm',
    'Throttle body bolts': '10 Nm',
    'Alternator bolts': '50 Nm',
    'Drive belt tensioner': '25 Nm',
    'VVT solenoid': '10 Nm',

    # Transmission
    'Transmission drain plug': '30 Nm',
    'Transmission pan bolts': '6-8 Nm (cross pattern)',

    # Chassis
    'Wheel bolts (alloy)': '103 Nm ± 15.5 Nm',
    'Wheel bolts (steel)': '80 Nm ± 12 Nm',
    'Caliper bracket (front)': '115 Nm',
    'Caliper bracket (rear)': '90 Nm',
    'Caliper slide pins': '35 Nm',
    'Battery clamp': '8 Nm',
    'Subframe bolts (rear)': '175 Nm',
    'Lower wishbone bolt (front)': '175 Nm',
    'Track rod end nut': '55 Nm',
    'Tie rod locknut': '40 Nm',
    'Rear lower arm cam bolt nut': '115 Nm — must tighten with vehicle ON WHEELS',

    # Drivetrain
    'Propshaft centre bearing': '25 Nm',
    'Propshaft flange bolts': '80 Nm',
}


# ═══════════════════════════════════════════════════════════════════
#  Fuse & Relay Reference — All Three Fuse Boxes
# ═══════════════════════════════════════════════════════════════════

FUSE_REFERENCE = {
    'Engine Bay Fuse Box': {
        'location': 'Left side of engine bay, near the bulkhead adjacent to battery',
        'key_fuses': {
            'F1 (10A)': 'ECM power',
            'F2 (15A)': 'Headlamp levelling',
            'F4 (10A)': 'Cooling fan control',
            'F5 (10A)': 'A/C compressor control',
            'F6 (7.5A)': 'Instrument cluster feed',
            'F8 (30A)': 'ABS module',
            'F9 (30A)': 'ABS pump motor',
            'F10 (15A)': 'Fuel injectors',
            'F11 (15A)': 'Ignition coils',
            'F13 (5A)': 'EGR solenoid',
            'F14 (30A)': 'Heated windscreen LH',
            'F15 (30A)': 'Heated windscreen RH',
            'F16 (5A)': 'Oxygen sensor heaters',
            'F17 (10A)': 'EVAP canister purge',
            'F18 (10A)': 'Secondary air injection',
            'F19 (10A)': 'Speed control module',
            'F20 (30A)': 'Starter relay supply',
            'F24 (30A)': 'Windscreen wiper motor',
            'F27 (15A)': 'Horn',
            'F29 (20A)': 'Front fog lamps',
            'F30 (20A)': 'Rear fog lamps',
            'F32 (15A)': 'Brake lamps',
            'F34 (30A)': 'Blower motor relay',
            'F38 (20A)': 'Fuel pump relay',
            'F39 (150A)': 'Main distribution (mega fuse)',
            'F40 (80A)': 'High current distribution',
            'F41 (60A)': 'High current distribution',
            'F42 (50A)': 'High current distribution',
        },
        'key_relays': {
            'R1': 'Main beam / front fog relay',
            'R3': 'A/C compressor clutch relay',
            'R4': 'Windscreen wiper motor relay',
            'R5': 'Power distribution ignition relay',
            'R6': 'Windscreen heater relay',
            'R7 (V6)': 'Throttle motor relay (2.5/3.0L)',
            'R7 (2.0)': 'Fuel pump relay (2.0L)',
            'R8': 'Powerwash pump relay',
            'R9': 'Reverse lamps relay',
            'R10': 'Battery saver relay',
            'R11': 'Dip beam relay',
            'R12': 'Starter relay',
            'R13': 'Slave ignition relay',
            'R15': 'Horn relay',
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
        'key_relays': {
            'R1': 'Fold-back mirror module',
            'R2': 'Accessory relay',
            'R3': 'Rear wiper motor relay (Estate only)',
            'R4': 'Blower motor relay',
            'R5': 'Passenger junction fuse box ignition relay',
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
#  CAN Bus & Electrical Architecture
#  Reference for understanding network diagnostics
# ═══════════════════════════════════════════════════════════════════

CAN_ARCHITECTURE = {
    'networks': {
        'CAN_high_speed': {
            'type': 'Controller Area Network (ISO 11898)',
            'baud_rate': '500 kbaud',
            'physical': 'Two-wire twisted pair shielded cable',
            'termination': '120Ω resistors at both ends',
            'primary_nodes': [
                'ECM (Engine Control Module) — EN16-123/124',
                'TCM (Transmission Control Module)',
                'Instrument Cluster — IP10-17/18 (CAN gateway)',
                'ABS/TC/DSC Module',
                'Climate Control Module',
                'GEM (General Electronic Module)',
                'Speed Control Module (Cruise)',
                'Restraints Control Module (Airbags)',
                'Parking Aid Module',
            ],
        },
        'SCP_body': {
            'type': 'Standard Corporate Protocol',
            'baud_rate': '10.4 kbaud',
            'physical': 'Two-wire twisted pair',
            'nodes': {
                'GEM': 'IP5-18 (SCP-), IP5-19 (SCP+)',
                'Instrument Cluster': 'IP10-22 (SCP+), IP10-23 (SCP-)',
                'Audio Unit': 'IP65-09 (SCP+), IP65-10 (SCP-)',
            },
            'note': 'Body network for comfort/convenience. Instrument cluster bridges CAN↔SCP.',
        },
        'D2B_fiber': {
            'type': 'D2B Fiber Optic Network',
            'physical': 'Optical fiber (BOF designation)',
            'purpose': 'Real-time audio data transfer only',
            'nodes': ['Audio Unit (gateway)', 'CD Autochanger', 'Power Amplifier',
                      'Voice Activation Module', 'Navigation Module', 'Cellular Phone Module'],
        },
    },
    'key_modules': {
        'ECM': {
            'connector': 'EN16 / 134-way / Black',
            'location': 'Engine compartment, front bulkhead, RH side',
            'critical_pins': {
                'EN16-006': 'Engine crank supply (B+)',
                'EN16-031': 'Park/Neutral switch (auto)',
                'EN16-041': 'Starter relay drive output',
                'EN16-053': 'Generator control',
                'EN16-123': 'CAN - network',
                'EN16-124': 'CAN + network',
            },
        },
        'GEM': {
            'location': 'Behind instrument panel, RH side',
            'connectors': ['CA86 (23-way grey)', 'CA87 (23-way green)',
                          'IP5 (23-way brown)', 'IP6 (23-way natural)',
                          'JB172 (23-way blue)'],
        },
        'Instrument_Cluster': {
            'connectors': ['IP10 (26-way white)', 'IP11 (26-way white)'],
            'role': 'CAN/SCP network gateway + PATS processing',
            'pats_pins': 'IP10-03/04 (PATS comms), IP10-05 (ground), IP10-06 (power)',
        },
    },
    'ground_points': {
        'G13': 'Engine block — battery engine ground',
        'G16': 'Under battery tray — battery chassis ground (primary)',
        'G14': 'Rearward of power distribution fuse box — engine bay circuits, ECM returns',
        'G17': 'Generator bracket, RH engine — generator return path',
        'G10': 'Under RH headlamp — RH front corner ground',
        'G11': 'Under LH headlamp — LH front corner ground',
        'G36': 'LH cross car beam — instrument cluster, control module returns',
        'G37': 'RH cross car beam — dashboard circuits, lighting returns',
        'G33': 'ABS/DSC module bracket — ABS ground reference',
        'G8': 'RH strut tower — braking system grounds',
        'G1': 'Trunk LH rear — trunk lamp, tail light returns',
        'G35': 'LH E-post lower — rear lamp circuits',
    },
    'wire_color_codes': {
        'N': 'Brown', 'B': 'Black', 'W': 'White', 'K': 'Pink',
        'G': 'Green', 'R': 'Red', 'Y': 'Yellow', 'O': 'Orange',
        'S': 'Slate', 'L': 'Light', 'U': 'Blue', 'P': 'Purple',
        'BRD': 'Braid (shield)', 'BOF': 'Fiber optic (D2B)',
    },
    'harness_prefixes': {
        'EN': 'Engine', 'IP': 'Instrument Panel', 'CA': 'Cabin',
        'FL': 'LH Front Door', 'FR': 'RH Front Door',
        'BL': 'LH Rear Door', 'BR': 'RH Rear Door',
        'FT': 'Fuel Tank', 'GC': 'Cooling Pack', 'TL': 'Trunk Lid',
        'JB': 'Junction Box', 'AC': 'Climate Control',
    },
    'abbreviations': {
        'CKP': 'Crankshaft Position Sensor',
        'CMP': 'Camshaft Position Sensor (RH Bank /1, LH Bank /2)',
        'ECT': 'Engine Coolant Temperature Sensor',
        'IAT': 'Intake Air Temperature Sensor',
        'MAF': 'Mass Air Flow Sensor',
        'MAP': 'Manifold Absolute Pressure Sensor',
        'TP': 'Throttle Position Sensor',
        'HO2': 'Heated Oxygen Sensor',
        'KS': 'Knock Sensor',
        'IMT': 'Intake Manifold Tuning Solenoid',
        'VVT': 'Variable Valve Timing Solenoid',
        'EGR': 'Exhaust Gas Recirculation Valve',
        'EVAP': 'Evaporative Emission Control',
        'PATS': 'Passive Anti-Theft System',
        'DSC': 'Dynamic Stability Control',
        'GEM': 'General Electronic Module',
        'TCM': 'Transmission Control Module',
        'ECM': 'Engine Control Module',
    },
    'relay_pin_standard': {
        'pin_1': 'Coil positive supply (+)',
        'pin_2': 'Coil negative / NC',
        'pin_3': 'Change-over (COM)',
        'pin_4': 'Normally open (NO)',
        'pin_5': 'Coil return',
    },
}


# ═══════════════════════════════════════════════════════════════════
#  Telemetry Interpretation Guide
#  Helps the LLM understand what sensor values MEAN
# ═══════════════════════════════════════════════════════════════════

TELEMETRY_INTERPRETATION = {
    'coolant_temp': {
        'normal_range': '85-100°C',
        'cold': 'Below 80°C — thermostat not yet open, engine in warm-up enrichment',
        'hot_warning': 'Above 105°C — thermostat housing, water pump, fan, or coolant level issue',
        'critical': 'Above 115°C — PULL OVER IMMEDIATELY. Engine damage imminent.',
        'stuck_low': 'Never reaches 85°C — thermostat stuck open. Poor fuel economy, P0125.',
    },
    'rpm': {
        'warm_idle': '650-780 RPM',
        'cold_idle': '1000-1400 RPM (below 40°C coolant)',
        'hunting': 'RPM oscillating ±100 RPM at idle — vacuum leak, throttle body, IAC issue',
        'high_idle': 'Above 900 RPM warm — vacuum leak, throttle body carbon, sticking ISC',
        'rough_idle': 'RPM drops below 600 — misfire, vacuum leak, fuel delivery issue',
    },
    'fuel_trims': {
        'stft_normal': '-5% to +5%',
        'stft_lean': 'Above +10% — possible vacuum leak, fuel pressure low, MAF underreporting',
        'stft_rich': 'Below -10% — possible purge valve stuck open, fuel pressure high, injector leak',
        'ltft_normal': '-8% to +8%',
        'ltft_lean': 'Above +15% — chronic vacuum leak or failing MAF. Check both banks.',
        'ltft_rich': 'Below -15% — chronic rich condition. Check purge valve, fuel pressure.',
        'bank_imbalance': 'If Bank 1 and Bank 2 trims differ by >5%, suspect bank-specific leak',
        'both_banks_lean': 'Both banks lean = common vacuum leak (intake manifold, PCV, brake booster)',
        'one_bank_lean': 'One bank lean = bank-specific leak (valve cover gasket, runner gasket)',
    },
    'voltage': {
        'engine_running': '13.8-14.4V — normal alternator output',
        'key_on_engine_off': '12.4-12.7V — healthy battery',
        'low_running': 'Below 13.5V running — alternator weak, belt slipping, diode failure',
        'high_running': 'Above 14.8V — voltage regulator fault, overcharging, battery damage risk',
        'very_low': 'Below 11V — battery critically low, engine may stall, power steering heavy',
    },
    'vehicle_speed': {
        'unit': 'km/h (Australian spec)',
        'cruise_range': '60-110 km/h for cruise control operation',
    },
}


# ═══════════════════════════════════════════════════════════════════
#  Australian-Specific Information
# ═══════════════════════════════════════════════════════════════════

AUSTRALIAN_SPECS = {
    'fuel': {
        'minimum_octane': '95 RON (Premium Unleaded)',
        'recommended': '98 RON (Ultra Premium) for best performance and economy',
        'ethanol': 'Up to 10% ethanol (E10) acceptable. E85 NOT suitable.',
        'note': 'Lower octane causes knock sensor intervention → reduced power and economy.',
    },
    'climate_adjustments': {
        'oil_change': 'Every 8,000 km (vs 16,000 km European schedule)',
        'coolant_change': 'Every 3 years (vs 5 years European)',
        'atf_change': 'Every 50,000 km (vs "lifetime" Jaguar claim)',
        'ptu_fluid': 'Every 50,000 km (vs never per Jaguar)',
        'air_filter': 'Every 16,000 km (dust regions may need more frequent)',
        'cabin_filter': 'Every 16,000 km',
    },
    'roadworthy_critical_items': [
        'Suspension ball joints and bushes (heat degrades rubber)',
        'Brake pad minimum 3mm, disc minimum thickness',
        'All lights functioning correctly',
        'No major fluid leaks (seeping OK, dripping fails)',
        'No check engine light illuminated',
        'Horn functional',
        'Windscreen wipers and washers operative',
        'Tyre tread minimum 1.5mm',
    ],
    'parts_suppliers': {
        'Sparesbox': 'sparesbox.com.au — comprehensive X-Type parts catalog',
        'Repco': 'repco.com.au — aftermarket parts, filters, fluids',
        'Supercheap Auto': 'supercheapauto.com.au — filters, fluids, tools',
        'eBay AU': 'Good source for aftermarket thermostat housings, coils, sensors',
        'Rolan Motors': 'Jaguar specialist parts and service',
        'Gearbox City': 'Transmission parts, solenoid kits, filters',
    },
    'currency': 'AUD — all costs in this knowledge base are in Australian Dollars',
}


# ═══════════════════════════════════════════════════════════════════
#  Lighting & Bulb Reference
# ═══════════════════════════════════════════════════════════════════

LIGHTING_REFERENCE = {
    'headlamps': {
        'dip_beam': 'H7 55W',
        'main_beam': 'H1 55W',
        'hid_option': 'D2S xenon (if HID equipped)',
    },
    'front': {
        'indicator': 'PY21W (amber)',
        'side_marker': 'W5W',
        'fog': 'H11 55W',
    },
    'rear': {
        'tail_brake': 'P21/5W (dual filament)',
        'indicator': 'PY21W (amber)',
        'reverse': 'P21W',
        'fog': 'P21W',
        'high_mount_brake': 'LED array (non-serviceable)',
    },
    'interior': {
        'map_light': 'W5W',
        'courtesy': 'Festoon 10W',
        'boot': 'W5W',
        'glovebox': 'W5W',
    },
    'dashboard': {
        'warning_lights': 'Non-serviceable LEDs',
        'instrument_illumination': 'Non-serviceable',
    },
}


# ═══════════════════════════════════════════════════════════════════
#  Search Function — Enhanced for expanded knowledge base
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
            ' '.join(problem.get('related_dtcs', [])).lower(),
        ])
        for term in terms:
            if term in searchable:
                score += searchable.count(term)
                if term in [t.lower() for t in problem.get('tags', [])]:
                    score += 3
        if score > 0:
            results.append({
                'type': 'problem',
                'title': problem['title'],
                'score': score,
                'data': problem,
            })

    # Search DTC reference
    for code, info in DTC_REFERENCE.items():
        if any(term in code.lower() or term in info['desc'].lower()
               or any(term in c.lower() for c in info['causes'])
               for term in terms):
            results.append({
                'type': 'dtc',
                'title': f"{code}: {info['desc']}",
                'score': 5,
                'data': {'code': code, **info},
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
        if isinstance(specs, dict):
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

    # Search owner vehicle history
    for repair in OWNER_VEHICLE_HISTORY.get('known_repairs', []):
        searchable = f"{repair['issue']} {repair['details']}".lower()
        if any(term in searchable for term in terms):
            results.append({
                'type': 'owner_history',
                'title': f"Owner History: {repair['issue']}",
                'score': 8,  # High priority — this car's specific history
                'data': repair,
            })
    for symptom in OWNER_VEHICLE_HISTORY.get('current_symptoms', []):
        searchable = f"{symptom['symptom']} {symptom['details']}".lower()
        if any(term in searchable for term in terms):
            results.append({
                'type': 'owner_symptom',
                'title': f"Current Issue: {symptom['symptom']}",
                'score': 10,  # Highest priority — active symptoms
                'data': symptom,
            })

    # Search cruise control logic
    cruise = CRUISE_CONTROL_LOGIC
    searchable = f"{cruise['description']} {' '.join(cruise['triggering_faults'])}".lower()
    if any(term in searchable for term in terms):
        results.append({
            'type': 'cruise_logic',
            'title': 'Cruise Control Disable Logic',
            'score': 6,
            'data': cruise,
        })

    # Search telemetry interpretation
    for param, info in TELEMETRY_INTERPRETATION.items():
        searchable = f"{param} {' '.join(str(v) for v in info.values())}".lower()
        if any(term in searchable for term in terms):
            results.append({
                'type': 'telemetry_guide',
                'title': f'Telemetry: {param.replace("_", " ").title()}',
                'score': 4,
                'data': {'parameter': param, **info},
            })

    # Search CAN architecture
    for net_name, net_info in CAN_ARCHITECTURE.get('networks', {}).items():
        searchable = f"{net_name} {str(net_info)}".lower()
        if any(term in searchable for term in terms):
            results.append({
                'type': 'electrical',
                'title': f'Network: {net_name}',
                'score': 3,
                'data': net_info,
            })

    # Search service schedule
    for item in SERVICE_SCHEDULE:
        searchable = f"{item['item']} {item['details']}".lower()
        if any(term in searchable for term in terms):
            results.append({
                'type': 'service',
                'title': f"{item['interval']}: {item['item']}",
                'score': 3,
                'data': item,
            })

    # Sort by score descending
    results.sort(key=lambda r: r['score'], reverse=True)
    return results[:20]


def get_advice_for_alert(alert_msg):
    """Given a DRIFTER alert message, return relevant mechanical advice."""
    if not alert_msg:
        return None

    msg_lower = alert_msg.lower()

    patterns = [
        (['coolant', 'thermostat', 'temperature', 'overheating'], 'coolant thermostat'),
        (['vacuum leak', 'lean', 'stft'], 'vacuum leak intake'),
        (['coil', 'misfire', 'rpm stumble'], 'coil misfire'),
        (['alternator', 'voltage', 'undercharging', 'overcharging'], 'alternator voltage'),
        (['throttle', 'load mismatch', 'p2106', 'limp'], 'throttle body'),
        (['maf', 'mass air'], 'maf sensor'),
        (['idle', 'instability', 'hunting'], 'idle rough'),
        (['transmission', 'solenoid', 'limp mode', 'f4'], 'transmission solenoid limp'),
        (['ptu', 'transfer', 'whine', 'grinding'], 'ptu transfer awd'),
        (['cruise', 'unavailable', 'speed control'], 'cruise control disable'),
        (['dtc', 'p0'], 'dtc code'),
        (['tire', 'tyre', 'pressure', 'tpms'], 'tyre pressure'),
        (['stall', 'engine stall'], 'stall start'),
        (['fuel pump', 'p1235'], 'fuel pump'),
        (['battery', 'critical', 'drain'], 'battery alternator parasitic'),
        (['window', 'regulator'], 'window regulator'),
        (['abs', 'traction', 'dsc', 'u0121'], 'abs module'),
    ]

    for keywords, search_terms in patterns:
        if any(kw in msg_lower for kw in keywords):
            return search(search_terms)

    return None


def get_dtc_info(code):
    """Look up a specific DTC code. Returns dict or None."""
    code = code.upper().strip()
    info = DTC_REFERENCE.get(code)
    if info:
        return {'code': code, **info}
    return None


def get_telemetry_context(param_name, value):
    """Get interpretation of a telemetry parameter value."""
    guide = TELEMETRY_INTERPRETATION.get(param_name)
    if not guide:
        return None
    return guide
