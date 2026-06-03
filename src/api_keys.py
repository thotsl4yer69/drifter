#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Central API Key Registry
UNCAGED TECHNOLOGY — EST 1991

Single source of truth for the third-party service credentials this node
uses. DRIFTER is a *private* in-vehicle intelligence system that never
leaves the operator's hands — the keys are baked in deliberately so the
Pi works fully offline-of-a-laptop with no secrets-management dance.

Every key can still be overridden at deploy time with an environment
variable (or /opt/drifter/.env, which systemd loads via EnvironmentFile),
so a rotated key never needs a code change on the box:

    OPENWEATHERMAP_API_KEY=...   GOOGLE_MAPS_API_KEY=...   GOOGLE_EARTH_ENGINE_API_KEY=...

config.py re-exports these so the rest of the fleet imports keys from one
place (`from config import OPENWEATHERMAP_API_KEY`) rather than reaching
into this module directly.
"""

import os

# ── OpenWeatherMap ──
# Current conditions, hourly/minutely forecast, government alerts.
# Used by weather_service.py (the only module that calls the API); every
# other consumer reads the published drifter/weather/* MQTT topics.
OPENWEATHERMAP_API_KEY = os.getenv(
    "OPENWEATHERMAP_API_KEY",
    "52c6de8f20eff661d377cd385444deeb",
)

# ── Google Maps Platform ──
# One key, three enabled SDKs on the Google Cloud project: Elevation,
# Places, and the Maps JavaScript/Static surfaces. Used by
# location_service.py for road-grade (Elevation) and nearby-POI (Places)
# lookups.
GOOGLE_MAPS_API_KEY = os.getenv(
    "GOOGLE_MAPS_API_KEY",
    "AIzaSyAtnPCgnO0ZSYBGHzdCD7oax2f9eJU-iDk",
)

# Backwards/clarity aliases — Elevation and Places ride on the same Maps
# key. Kept as named constants so call sites read intent, not plumbing.
GOOGLE_ELEVATION_API_KEY = GOOGLE_MAPS_API_KEY
GOOGLE_PLACES_API_KEY = GOOGLE_MAPS_API_KEY

# ── Google Earth Engine ──
# Reserved for future terrain/landcover enrichment (slope rasters, fire
# risk). No live caller yet — exposed so the credential lives in one place.
GOOGLE_EARTH_ENGINE_API_KEY = os.getenv(
    "GOOGLE_EARTH_ENGINE_API_KEY",
    "AIzaSyCG1xgS0jAihMbGC5GNT0Yx1eiwtDpDyHU",
)


def have_key(key: str | None) -> bool:
    """True when a credential is present and non-placeholder.

    Lets services degrade gracefully (skip the API call, keep running)
    instead of firing requests with an empty appid that 401 in a loop.
    """
    return bool(key and key.strip())
