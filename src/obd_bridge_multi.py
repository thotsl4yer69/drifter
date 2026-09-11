#!/usr/bin/env python3
"""MZ1312 DRIFTER — Backward-compatible multi-link bridge entry point.
All transports now share the same implementation without import-time patches.
UNCAGED TECHNOLOGY — EST 1991
"""
from obd_bridge import main

if __name__ == '__main__':
    main()
