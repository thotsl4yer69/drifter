#!/usr/bin/env python3
"""
MZ1312 DRIFTER — Spotify Bridge
Spotify Connect / Web API bridge using spotipy. Exposes voice-friendly
commands over MQTT: play/pause/next/prev/volume/playlist/transfer/mood,
plus smooth volume ducking and a "now playing" stream back to the dashboard.
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
    SPOTIFY_DUCK_LEVEL, SPOTIFY_DUCK_FADE_MS, SPOTIFY_MOODS,
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
    moods = dict(SPOTIFY_MOODS)
    moods.update(cfg.get('moods') or {})
    return {
        'client_id': cfg.get('client_id'),
        'client_secret': cfg.get('client_secret'),
        'redirect_uri': cfg.get('redirect_uri', SPOTIFY_REDIRECT_URI),
        'scopes': cfg.get('scopes', SPOTIFY_SCOPES),
        'device_name': cfg.get('device_name', SPOTIFY_DEVICE_NAME),
        'moods': moods,
        'duck_level': int(cfg.get('duck_level', SPOTIFY_DUCK_LEVEL)),
        'duck_fade_ms': int(cfg.get('duck_fade_ms', SPOTIFY_DUCK_FADE_MS)),
    }


def _make_client(creds: dict):
    """Construct a spotipy.Spotify client. Returns (client, auth_manager) or (None, None)."""
    if not creds.get('client_id') or not creds.get('client_secret'):
        log.warning("Spotify creds missing — populate /opt/drifter/spotify.yaml")
        return None, None
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        log.warning("spotipy not installed — pip install spotipy")
        return None, None
    SPOTIFY_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    auth = SpotifyOAuth(
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
        redirect_uri=creds['redirect_uri'],
        scope=creds['scopes'],
        cache_path=str(SPOTIFY_TOKEN_FILE),
        open_browser=False,
    )
    return spotipy.Spotify(auth_manager=auth), auth


def _ensure_token(auth) -> bool:
    """Proactively refresh the access token if expired. Returns True if a usable token exists."""
    if auth is None:
        return False
    try:
        tok = auth.get_cached_token()
        if not tok:
            return False
        if auth.is_token_expired(tok):
            auth.refresh_access_token(tok['refresh_token'])
        return True
    except Exception as e:
        log.warning(f"token refresh failed: {e}")
        return False


def _resolve_device(sp, name: str) -> Optional[str]:
    try:
        for dev in sp.devices().get('devices', []):
            if dev.get('name') == name or dev.get('id') == name:
                return dev['id']
    except Exception as e:
        log.warning(f"devices lookup: {e}")
    return None


def _current_volume(sp) -> Optional[int]:
    try:
        cur = sp.current_playback()
        if cur and cur.get('device'):
            return int(cur['device'].get('volume_percent', 0))
    except Exception as e:
        log.debug(f"current_volume: {e}")
    return None


def _retry_after_seconds(exc) -> Optional[float]:
    """Pull a Retry-After value from a spotipy SpotifyException (429), if present."""
    headers = getattr(exc, 'headers', None) or {}
    val = headers.get('Retry-After') or headers.get('retry-after')
    if not val:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _call_with_429(fn, *args, **kwargs):
    """Invoke a spotipy call, honouring a single Retry-After on 429."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        status = getattr(exc, 'http_status', None) or getattr(exc, 'status', None)
        if status == 429:
            wait = _retry_after_seconds(exc) or 1.0
            wait = min(wait, 10.0)
            log.warning(f"Spotify rate-limited, sleeping {wait:.1f}s")
            time.sleep(wait)
            return fn(*args, **kwargs)
        raise


class DuckController:
    """Smooth volume ducking with cancellable fades.

    duck() fades the device down to duck_level; unduck() restores the pre-duck volume.
    Concurrent calls cancel any in-flight fade so the latest request always wins.
    """

    def __init__(self, sp, device_name: str, duck_level: int, fade_ms: int) -> None:
        self.sp = sp
        self.device_name = device_name
        self.duck_level = max(0, min(100, duck_level))
        self.fade_ms = max(50, fade_ms)
        self._lock = threading.Lock()
        self._fade_token = 0
        self._pre_duck_volume: Optional[int] = None
        self._ducked = False

    def _set_volume(self, level: int, device_id: Optional[str]) -> None:
        try:
            _call_with_429(self.sp.volume, max(0, min(100, level)), device_id=device_id)
        except Exception as e:
            log.debug(f"volume set failed: {e}")

    def _fade(self, target: int, device_id: Optional[str], token: int) -> None:
        start = _current_volume(self.sp)
        if start is None:
            self._set_volume(target, device_id)
            return
        steps = max(2, int(self.fade_ms / 50))
        step_sleep = (self.fade_ms / 1000.0) / steps
        for i in range(1, steps + 1):
            with self._lock:
                if token != self._fade_token:
                    return
            level = int(round(start + (target - start) * (i / steps)))
            self._set_volume(level, device_id)
            time.sleep(step_sleep)

    def duck(self, device_id: Optional[str]) -> None:
        with self._lock:
            self._fade_token += 1
            token = self._fade_token
            if not self._ducked:
                self._pre_duck_volume = _current_volume(self.sp)
            self._ducked = True
        threading.Thread(target=self._fade,
                         args=(self.duck_level, device_id, token),
                         daemon=True).start()

    def unduck(self, device_id: Optional[str]) -> None:
        with self._lock:
            self._fade_token += 1
            token = self._fade_token
            target = self._pre_duck_volume if self._pre_duck_volume is not None else 60
            self._ducked = False
        threading.Thread(target=self._fade,
                         args=(target, device_id, token),
                         daemon=True).start()


COMMANDS = {
    'play', 'pause', 'next', 'previous', 'volume',
    'transfer', 'shuffle', 'repeat', 'playlist', 'search', 'queue',
    'duck', 'unduck', 'mood',
}


def _execute(sp, ducker: DuckController, creds: dict, payload: dict) -> dict:
    cmd = (payload.get('command') or '').lower()
    if cmd not in COMMANDS:
        return {'ok': False, 'error': f"unknown command: {cmd}"}

    device_id = _resolve_device(sp, payload.get('device') or SPOTIFY_DEVICE_NAME)

    try:
        if cmd == 'play':
            uri = payload.get('uri')
            if uri:
                _call_with_429(sp.start_playback, device_id=device_id, uris=[uri])
            else:
                _call_with_429(sp.start_playback, device_id=device_id)
        elif cmd == 'pause':
            _call_with_429(sp.pause_playback, device_id=device_id)
        elif cmd == 'next':
            _call_with_429(sp.next_track, device_id=device_id)
        elif cmd == 'previous':
            _call_with_429(sp.previous_track, device_id=device_id)
        elif cmd == 'volume':
            level = int(payload.get('level', 50))
            _call_with_429(sp.volume, max(0, min(100, level)), device_id=device_id)
        elif cmd == 'transfer':
            if not device_id:
                return {'ok': False, 'error': 'device not found'}
            _call_with_429(sp.transfer_playback, device_id=device_id, force_play=True)
        elif cmd == 'shuffle':
            _call_with_429(sp.shuffle, bool(payload.get('on', True)), device_id=device_id)
        elif cmd == 'repeat':
            state = payload.get('state', 'off')
            if state not in ('track', 'context', 'off'):
                state = 'off'
            _call_with_429(sp.repeat, state, device_id=device_id)
        elif cmd == 'playlist':
            playlist = payload.get('uri') or payload.get('playlist')
            if not playlist:
                return {'ok': False, 'error': 'playlist uri required'}
            _call_with_429(sp.start_playback, device_id=device_id, context_uri=playlist)
        elif cmd == 'mood':
            mood = (payload.get('mood') or '').lower()
            uri = creds['moods'].get(mood)
            if not uri:
                return {'ok': False, 'error': f"unknown mood: {mood}",
                        'known': sorted(creds['moods'].keys())}
            _call_with_429(sp.start_playback, device_id=device_id, context_uri=uri)
            return {'ok': True, 'mood': mood, 'uri': uri}
        elif cmd == 'search':
            query = payload.get('query', '')
            results = _call_with_429(sp.search, q=query, limit=5)
            return {'ok': True, 'results': results}
        elif cmd == 'queue':
            uri = payload.get('uri')
            if not uri:
                return {'ok': False, 'error': 'uri required for queue'}
            _call_with_429(sp.add_to_queue, uri, device_id=device_id)
        elif cmd == 'duck':
            ducker.duck(device_id)
            return {'ok': True, 'ducked': True, 'level': ducker.duck_level}
        elif cmd == 'unduck':
            ducker.unduck(device_id)
            return {'ok': True, 'ducked': False}
        return {'ok': True}
    except Exception as e:
        log.warning(f"command {cmd} failed: {e}")
        return {'ok': False, 'error': str(e)}


def _now_playing(sp) -> dict:
    try:
        cur = _call_with_429(sp.current_playback)
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
    sp, auth = _make_client(creds)
    ducker = DuckController(sp, creds['device_name'],
                            creds['duck_level'], creds['duck_fade_ms']) if sp else None

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    client = mqtt.Client(client_id="drifter-spotify")

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if msg.topic != TOPICS['spotify_command']:
            return
        payload = data if isinstance(data, dict) else {}
        if sp is None or not _ensure_token(auth):
            client.publish(TOPICS['spotify_status'], json.dumps({
                'state': 'offline',
                'reason': 'no_credentials' if sp is None else 'token_unavailable',
                'last_command': payload.get('command'),
                'request_id': payload.get('request_id'),
                'ts': time.time(),
            }), retain=True)
            return
        if payload.get('command') in ('duck', 'unduck'):
            res = _execute(sp, ducker, creds, payload)
            client.publish(TOPICS['spotify_duck'], json.dumps({
                'state': 'ducked' if payload['command'] == 'duck' else 'normal',
                'result': res, 'ts': time.time(),
            }), retain=True)
        else:
            res = _execute(sp, ducker, creds, payload)
        client.publish(TOPICS['spotify_status'], json.dumps({
            'last_command': payload.get('command'),
            'request_id': payload.get('request_id'),
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
        'moods': sorted(creds['moods'].keys()),
        'ts': time.time(),
    }), retain=True)
    log.info("Spotify Bridge LIVE" if sp else "Spotify Bridge waiting for creds")

    last_poll = 0.0
    last_token_check = 0.0
    while running:
        now = time.time()
        if sp and now - last_token_check >= 60:
            _ensure_token(auth)
            last_token_check = now
        if sp and now - last_poll >= 5:
            np = _now_playing(sp)
            if np:
                client.publish(TOPICS['spotify_track'], json.dumps(np), retain=True)
            last_poll = now
        time.sleep(0.5)

    client.loop_stop()
    client.disconnect()
    log.info("Spotify Bridge stopped")


if __name__ == '__main__':
    main()
