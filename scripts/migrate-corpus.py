#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Corpus migration

Walks /opt/drifter/data/mechanic/*.json (the legacy structured KB) and
emits markdown files into /opt/drifter/corpus/<subdir>/<slug>.md with
frontmatter that the corpus retrieval layer can index.

Idempotent: each run rewrites every target file. Safe to re-run.
UNCAGED TECHNOLOGY — EST 1991
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATA_DIR = Path("/opt/drifter/data/mechanic")
CORPUS_DIR = Path("/opt/drifter/corpus")

SUBDIRS = ('vehicle', 'dtc', 'emergency', 'driving', 'jaguar-specific')


def _slug(s: str) -> str:
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s[:80] or 'item'


def _load(name: str):
    path = DATA_DIR / f'{name}.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def _write(subdir: str, slug: str, frontmatter: dict, body: str) -> Path:
    out = CORPUS_DIR / subdir / f'{slug}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = ['---']
    for k, v in frontmatter.items():
        if isinstance(v, list):
            fm_lines.append(f'{k}: [{", ".join(str(x) for x in v)}]')
        else:
            fm_lines.append(f'{k}: {v}')
    fm_lines.append('---')
    out.write_text('\n'.join(fm_lines) + '\n\n' + body.strip() + '\n', encoding='utf-8')
    return out


# ── Migrators ───────────────────────────────────────────────────────

def migrate_dtc(data: dict) -> int:
    """dtc_reference.json → corpus/dtc/<CODE>.md (one file per code)."""
    n = 0
    for code, entry in data.items():
        desc = entry.get('desc', '')
        action = entry.get('action', '')
        causes = entry.get('causes', [])
        body_parts = [
            f"# {code} — {desc}",
            "",
            f"## Description",
            desc,
        ]
        if action:
            body_parts += ["", "## ECU action", action]
        if causes:
            body_parts += ["", "## Likely causes"]
            body_parts += [f"- {c}" for c in causes]
        _write(
            'dtc', code,
            frontmatter={
                'topic': f'{code} — {desc}',
                'tags': [code, 'dtc', 'fault-code'],
                'vehicle': 'x-type',
                'confidence': 'high',
            },
            body='\n'.join(body_parts),
        )
        n += 1
    return n


def migrate_common_problems(data: list) -> int:
    """common_problems.json → corpus/vehicle/<slug>.md."""
    n = 0
    for problem in data:
        title = problem.get('title', 'untitled')
        symptoms = problem.get('symptoms', [])
        cause = problem.get('cause', '')
        fix = problem.get('fix', '')
        tags = list(problem.get('tags', []))
        related = problem.get('related_dtcs', [])
        if related:
            tags.extend(related)
        cost = problem.get('estimated_cost', '')
        difficulty = problem.get('difficulty', '')

        body_parts = [f"# {title}", ""]
        if symptoms:
            body_parts += ["## Symptoms"]
            body_parts += [f"- {s}" for s in symptoms]
            body_parts.append("")
        if cause:
            body_parts += ["## Cause", cause, ""]
        if fix:
            body_parts += ["## Fix", fix, ""]
        if cost or difficulty or related:
            body_parts.append("## Notes")
            if cost:
                body_parts.append(f"- Estimated cost: {cost}")
            if difficulty:
                body_parts.append(f"- Difficulty: {difficulty}")
            if related:
                body_parts.append(f"- Related DTCs: {', '.join(related)}")
        _write(
            'vehicle', _slug(title),
            frontmatter={
                'topic': title,
                'tags': tags or ['x-type', 'common-problem'],
                'vehicle': 'x-type',
                'confidence': 'high',
            },
            body='\n'.join(body_parts),
        )
        n += 1
    return n


def migrate_emergency(data: list) -> int:
    """emergency_procedures.json → corpus/emergency/<slug>.md."""
    n = 0
    for proc in data:
        title = proc.get('title', proc.get('scenario', 'emergency'))
        scenario = proc.get('scenario', '')
        steps = proc.get('steps', [])
        risk = proc.get('risk', '')
        body_parts = [f"# {title}", ""]
        if scenario:
            body_parts += ["## When this applies", scenario, ""]
        if steps:
            body_parts += ["## What to do"]
            body_parts += [f"{i+1}. {s}" for i, s in enumerate(steps)]
            body_parts.append("")
        if risk:
            body_parts += ["## Risk", risk]
        _write(
            'emergency', _slug(title),
            frontmatter={
                'topic': title,
                'tags': proc.get('tags', ['emergency']),
                'vehicle': 'x-type',
                'confidence': 'high',
            },
            body='\n'.join(body_parts),
        )
        n += 1
    return n


def migrate_service_schedule(data: list) -> int:
    """service_schedule.json → corpus/driving/service-<interval>.md."""
    n = 0
    for entry in data:
        interval = entry.get('interval_km') or entry.get('interval') or 'service'
        title = entry.get('title') or f'Service at {interval} km'
        items = entry.get('items', [])
        body_parts = [f"# {title}", ""]
        if items:
            body_parts += ["## Items"]
            body_parts += [f"- {i}" for i in items]
        if entry.get('notes'):
            body_parts += ["", "## Notes", entry['notes']]
        _write(
            'driving', _slug(f'service-{interval}'),
            frontmatter={
                'topic': title,
                'tags': ['service', 'maintenance', f'{interval}-km'],
                'vehicle': 'x-type',
                'confidence': 'high',
            },
            body='\n'.join(body_parts),
        )
        n += 1
    return n


def migrate_torque(data: dict) -> int:
    """torque_specs.json → corpus/jaguar-specific/torque-<part>.md (one file)."""
    if not data:
        return 0
    body_parts = ["# X-Type torque specifications", ""]
    for category, entries in data.items():
        body_parts += [f"## {category.replace('_', ' ').title()}"]
        if isinstance(entries, dict):
            for part, spec in entries.items():
                body_parts.append(f"- {part}: {spec}")
        elif isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    for part, spec in entry.items():
                        body_parts.append(f"- {part}: {spec}")
                else:
                    body_parts.append(f"- {entry}")
        body_parts.append("")
    _write(
        'jaguar-specific', 'torque-specs',
        frontmatter={
            'topic': 'X-Type torque specifications',
            'tags': ['torque', 'specs', 'workshop'],
            'vehicle': 'x-type',
            'confidence': 'high',
        },
        body='\n'.join(body_parts),
    )
    return 1


def migrate_fuses(data: dict) -> int:
    """fuse_reference.json → corpus/jaguar-specific/fuses-<location>.md."""
    n = 0
    for location, fuses in data.items():
        title = f'Fuse box — {location}'
        body_parts = [f"# {title}", ""]
        if isinstance(fuses, dict):
            for fuse_id, info in fuses.items():
                body_parts.append(f"- F{fuse_id}: {info}")
        elif isinstance(fuses, list):
            for entry in fuses:
                body_parts.append(f"- {entry}")
        _write(
            'jaguar-specific', _slug(f'fuses-{location}'),
            frontmatter={
                'topic': title,
                'tags': ['fuse', 'electrical', location],
                'vehicle': 'x-type',
                'confidence': 'high',
            },
            body='\n'.join(body_parts),
        )
        n += 1
    return n


def migrate_keyed_dict(data: dict, subdir: str, slug: str, title: str,
                      tags: list[str], confidence: str = 'high') -> int:
    """Generic catch-all for nested dict structures (vehicle_specs,
    can_architecture, telemetry_interpretation, etc.). Walks one level deep
    and writes section-style markdown."""
    if not data:
        return 0
    body_parts = [f"# {title}", ""]
    for key, value in data.items():
        body_parts.append(f"## {str(key).replace('_', ' ').title()}")
        if isinstance(value, dict):
            for k2, v2 in value.items():
                if isinstance(v2, list):
                    body_parts.append(f"- {k2}:")
                    body_parts += [f"  - {item}" for item in v2]
                else:
                    body_parts.append(f"- {k2}: {v2}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    body_parts += [f"- {k}: {v}" for k, v in item.items()]
                else:
                    body_parts.append(f"- {item}")
        else:
            body_parts.append(str(value))
        body_parts.append("")
    _write(subdir, slug,
           frontmatter={'topic': title, 'tags': tags,
                        'vehicle': 'x-type', 'confidence': confidence},
           body='\n'.join(body_parts))
    return 1


# ── Driver ──────────────────────────────────────────────────────────

def main() -> int:
    if not DATA_DIR.exists():
        print(f"ERROR: {DATA_DIR} not found", file=sys.stderr)
        return 1

    counts: dict[str, int] = {}

    if (data := _load('dtc_reference')):
        counts['dtc'] = migrate_dtc(data)
    if (data := _load('common_problems')):
        counts['vehicle/common_problems'] = migrate_common_problems(data)
    if (data := _load('emergency_procedures')):
        counts['emergency'] = migrate_emergency(data)
    if (data := _load('service_schedule')):
        counts['driving/service'] = migrate_service_schedule(data)
    if (data := _load('torque_specs')):
        counts['jaguar/torque'] = migrate_torque(data)
    if (data := _load('fuse_reference')):
        counts['jaguar/fuses'] = migrate_fuses(data)
    if (data := _load('vehicle_specs')):
        counts['vehicle/specs'] = migrate_keyed_dict(
            data, 'vehicle', 'specs-overview',
            'X-Type vehicle specifications',
            ['specs', 'engine', 'transmission', 'dimensions'])
    if (data := _load('can_architecture')):
        counts['jaguar/can'] = migrate_keyed_dict(
            data, 'jaguar-specific', 'can-architecture',
            'X-Type CAN bus architecture',
            ['can', 'obd-ii', 'electrical'])
    if (data := _load('telemetry_interpretation')):
        counts['jaguar/telemetry'] = migrate_keyed_dict(
            data, 'jaguar-specific', 'telemetry-thresholds',
            'X-Type telemetry interpretation',
            ['telemetry', 'thresholds', 'sensors'])
    if (data := _load('australian_specs')):
        counts['jaguar/australian'] = migrate_keyed_dict(
            data, 'jaguar-specific', 'australian-spec',
            'X-Type Australian-delivered specifications',
            ['australian', 'specs', 'compliance'])
    if (data := _load('lighting_reference')):
        counts['jaguar/lighting'] = migrate_keyed_dict(
            data, 'jaguar-specific', 'lighting',
            'X-Type lighting reference',
            ['lighting', 'electrical', 'bulbs'])
    if (data := _load('owner_vehicle_history')):
        counts['jaguar/history'] = migrate_keyed_dict(
            data, 'jaguar-specific', 'owner-history',
            'Owner vehicle service history',
            ['history', 'maintenance', 'records'], confidence='medium')
    if (data := _load('cruise_control_logic')):
        counts['driving/cruise'] = migrate_keyed_dict(
            data, 'driving', 'cruise-control',
            'X-Type cruise control logic',
            ['cruise', 'control', 'driving'])

    total = sum(counts.values())
    print(f"Migrated {total} markdown files:")
    for src, n in sorted(counts.items()):
        print(f"  {src:30s} {n:>4d} file(s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
