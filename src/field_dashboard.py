#!/usr/bin/env python3
"""Field-ready dashboard entrypoint.

Keeps the established dashboard implementation untouched while layering the
vehicle-link/RF operator routes and RF operator engine used by the in-car touch
workflow. This makes rollback trivial: the service can point back at
web_dashboard.py without changing the legacy dashboard code.
"""
from __future__ import annotations

import rf_ops
import web_dashboard
from field_dashboard_handler import FieldDashboardHandler


def main() -> None:
    web_dashboard.DashboardHandler = FieldDashboardHandler
    rf_ops.start()
    try:
        web_dashboard.main()
    finally:
        rf_ops.stop()


if __name__ == "__main__":
    main()
