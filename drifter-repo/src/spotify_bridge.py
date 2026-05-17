#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Spotify Bridge
Spotify Connect / Web API bridge using spotipy. Exposes voice-friendly
commands over MQTT: play/pause/next/prev/volume/playlist/transfer, plus
"now playing" snapshots back to the dashboard.
UNCAGED TECHNOLOGY — EST 1991
"""

import json
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST, MQTT_PORT, TOPICS,
    DRIFTER_DIR, SPOTIFY_REDIRECT_URI, SPOTIFY_SCOPES,
    SPOTIFY_TOKEN_FILE, SPOTIFY_DEVICE_NAME,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [SPOTIFY] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

CONFIG_PATH = DRIFTER_DIR / "spotify.yaml"


def _load_credentials() -> dict:
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            import yaml
            cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        except Exception as e:
            log.warning(f"spotify.yaml load failed: {e}")
    return {
        'client_id': cfg.get('client_id'),
        'client_secret': cfg.get('client_secret'),
        'redirect_uri': cfg.get('redirect_uri', SPOTIFY_REDIRECT_URI),
        'scopes': cfg.get('scopes', SPOTIFY_SCOPES),
        'device_name': cfg.get('device_name', SPOTIFY_DEVICE_NAME),
    }


def _make_client(creds: dict):
    """Construct a spotipy.Spotify client. Returns None if spotipy/auth missing."""
    if not creds.get('client_id') or not creds.get('client_secret'):
        log.warning("Spotify creds missing — populate /opt/drifter/spotify.yaml")
        return None
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        log.warning("spotipy not installed — pip install spotipy")
        return None
    SPOTIFY_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    auth = SpotifyOAuth(
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
        redirect_uri=creds['redirect_uri'],
        scope=creds['scopes'],
        cache_path=str(SPOTIFY_TOKEN_FILE),
        open_browser=False,
    )
    return spotipy.Spotify(auth_manager=auth)


def _resolve_device(sp, name: str) -> Optional[str]:
    try:
        for dev in sp.devices().get('devices', []):
            if dev.get('name') == name or dev.get('id') == name:
                return dev['id']
    except Exception as e:
        log.warning(f"devices lookup: {e}")
    return None


COMMANDS = {
    'play', 'pause', 'next', 'previous', 'volume',
    'transfer', 'shuffle', 'repeat', 'playlist', 'search', 'queue',
}


def _execute(sp, payload: dict) -> dict:
    cmd = (payload.get('command') or '').lower()
    if cmd not in COMMANDS:
        return {'ok': False, 'error': f"unknown command: {cmd}"}

    device_id = _resolve_device(sp, payload.get('device') or SPOTIFY_DEVICE_NAME)

    try:
        if cmd == 'play':
            uri = payload.get('uri')
            if uri:
                sp.start_playback(device_id=device_id, uris=[uri])
            else:
                sp.start_playback(device_id=device_id)
        elif cmd == 'pause':
            sp.pause_playback(device_id=device_id)
        elif cmd == 'next':
            sp.next_track(device_id=device_id)
        elif cmd == 'previous':
            sp.previous_track(device_id=device_id)
        elif cmd == 'volume':
            level = int(payload.get('level', 50))
            sp.volume(max(0, min(100, level)), device_id=device_id)
        elif cmd == 'transfer':
            if device_id:
                sp.transfer_playback(device_id=device_id, force_play=True)
        elif cmd == 'shuffle':
            sp.shuffle(bool(payload.get('on', True)), device_id=device_id)
        elif cmd == 'repeat':
            state = payload.get('state', 'off')
            if state not in ('track', 'context', 'off'):
                state = 'off'
            sp.repeat(state, device_id=device_id)
        elif cmd == 'playlist':
            playlist = payload.get('uri') or payload.get('playlist')
            if not playlist:
                return {'ok': False, 'error': 'playlist uri required'}
            sp.start_playback(device_id=device_id, context_uri=playlist)
        elif cmd == 'search':
            query = payload.get('query', '')
            results = sp.search(q=query, limit=5)
            return {'ok': True, 'results': results}
        elif cmd == 'queue':
            uri = payload.get('uri')
            if not uri:
                return {'ok': False, 'error': 'uri required for queue'}
            sp.add_to_queue(uri, device_id=device_id)
        return {'ok': True}
    except Exception as e:
        log.warning(f"command {cmd} failed: {e}")
        return {'ok': False, 'error': str(e)}


def _now_playing(sp) -> dict:
    try:
        cur = sp.current_playback()
    except Exception as e:
        log.debug(f"current_playback: {e}")
        return {}
    if not cur:
        return {'is_playing': False}
    item = cur.get('item') or {}
    artists = ', '.join(a.get('name', '') for a in item.get('artists', []))
    return {
        'is_playing': cur.get('is_playing', False),
        'device': cur.get('device', {}).get('name'),
        'track': item.get('name'),
        'artists': artists,
        'album': item.get('album', {}).get('name'),
        'uri': item.get('uri'),
        'progress_ms': cur.get('progress_ms'),
        'duration_ms': item.get('duration_ms'),
        'ts': time.time(),
    }


def main() -> None:
    log.info("DRIFTER Spotify Bridge starting...")
    creds = _load_credentials()
    sp = _make_client(creds)

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-spotify")

    def on_message(_c, _u, msg) -> None:
        if sp is None:
            client.publish(TOPICS['spotify_status'], json.dumps({
                'state': 'offline', 'reason': 'no_credentials', 'ts': time.time(),
            }), retain=True)
            return
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if msg.topic == TOPICS['spotify_command']:
            res = _execute(sp, data if isinstance(data, dict) else {})
            client.publish(TOPICS['spotify_status'], json.dumps({
                'last_command': data.get('command') if isinstance(data, dict) else None,
                'result': res,
                'ts': time.time(),
            }))

    client.on_message = on_message

    connected = False
    while not connected and running:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            connected = True
        except Exception as e:
            log.warning(f"Waiting for MQTT broker... ({e})")
            time.sleep(3)

    if not running:
        return

    client.subscribe(TOPICS['spotify_command'], 0)
    client.loop_start()

    client.publish(TOPICS['spotify_status'], json.dumps({
        'state': 'online' if sp else 'offline',
        'device_name': creds['device_name'],
        'ts': time.time(),
    }), retain=True)
    log.info("Spotify Bridge LIVE" if sp else "Spotify Bridge waiting for creds")

    last_poll = 0.0
    while running:
        if sp and time.time() - last_poll >= 5:
            np = _now_playing(sp)
            if np:
                client.publish(TOPICS['spotify_track'], json.dumps(np), retain=True)
            last_poll = time.time()
        time.sleep(0.5)

    client.loop_stop()
    client.disconnect()
    log.info("Spotify Bridge stopped")


if __name__ == '__main__':
    main()
