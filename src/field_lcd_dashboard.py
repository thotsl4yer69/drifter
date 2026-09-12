#!/usr/bin/env python3
"""Field-aware wrapper for the direct SPI LCD dashboard.

The legacy vehicle empty-state was CAN-specific ("can0 idle"), which is wrong
for the Jaguar/ELM327 path. Keep the mature framebuffer renderer and replace
only that empty-state with a transport-neutral field instruction.
"""
from __future__ import annotations

import lcd_dashboard

_original_vehicle = lcd_dashboard.Renderer.vehicle


def _vehicle(self, data: dict):
    vehicle = (data.get('mqtt') or {}).get('vehicle') or {}
    if vehicle:
        return _original_vehicle(self, data)

    th = self.theme
    img, draw = self._canvas()
    self._brand(draw, 'vehicle')
    self._panel(draw, (8, 40, 472, 308), label='vehicle link', bracket=True)
    self._honest(
        draw,
        (8, 40, 472, 308),
        'no-hw',
        'vehicle link waiting',
        'connect ELM/CAN in cockpit · vehicle',
    )
    return img


lcd_dashboard.Renderer.vehicle = _vehicle

if __name__ == '__main__':
    lcd_dashboard.main()
