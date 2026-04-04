#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Field Operations Knowledge Base
Emergency comms, RF operations, Kali tools, survival procedures.
Used by MechanicRAG for LLM context retrieval.
UNCAGED TECHNOLOGY — EST 1991

TODO: Populate with full knowledge entries across 4 domains:
  - emergency_comms: Australian UHF CB, RFDS HF, Marine VHF, aviation, PMR446, EPIRB, Morse
  - rf_operations: RTL-SDR tools, signal ID, TPMS, ADS-B, antenna theory, Australian freq table
  - kali_tools: nmap, aircrack-ng, kismet, bettercap, tcpdump, hashcat, metasploit, etc.
  - survival: vehicle shelter, water, signaling, navigation, first aid, snake bite, bushfire
"""

# Placeholder — full entries to be populated in follow-up
FIELD_OPS_ENTRIES = []


def search(query):
    """Search the field operations knowledge base. Returns list of matching results."""
    if not query or not query.strip():
        return []
    terms = query.lower().split()
    results = []
    for entry in FIELD_OPS_ENTRIES:
        score = 0
        searchable = ' '.join([
            entry['title'].lower(),
            entry['category'].lower(),
            ' '.join(entry.get('tags', [])).lower(),
            entry.get('content', '').lower()[:500],
        ])
        for term in terms:
            if term in searchable:
                score += searchable.count(term)
                if term in [t.lower() for t in entry.get('tags', [])]:
                    score += 3
        if score > 0:
            results.append({
                'type': 'field_ops',
                'title': entry['title'],
                'score': score,
                'data': entry,
            })
    return sorted(results, key=lambda x: x['score'], reverse=True)
