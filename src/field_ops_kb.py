#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Field Operations Knowledge Base
Emergency comms, RF operations, Kali tools, survival procedures.
Used by MechanicRAG for LLM context retrieval.
UNCAGED TECHNOLOGY — EST 1991
"""

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
