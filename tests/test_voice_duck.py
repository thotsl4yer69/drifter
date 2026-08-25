"""TX-duck tests — Vivi/alert TTS must mute drifter-voicein capture.

Covers the three failure modes the design called out:
- refcount/tail logic in vivi_v2 (_acquire_duck/_release_duck), including
  the inter-sentence race where sentence N's clear would unmute during
  sentence N+1;
- stale-duck expiry on the voice_input side so a dead publisher can't mute
  the mic forever;
- the Last-Will payload shape that clears a crash mid-playback.
"""
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import vivi_v2
import voice_input


# ═══════════════════════════════════════════════════════════════════
#  vivi_v2 — publisher side
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_vivi_duck_state():
    """Fresh refcount + fast tail for every test."""
    vivi_v2._active_speakers = 0
    old_tail = vivi_v2._DUCK_TAIL_S
    vivi_v2._DUCK_TAIL_S = 0.01
    yield
    vivi_v2._DUCK_TAIL_S = old_tail
    vivi_v2._active_speakers = 0


@pytest.fixture
def duck_events():
    """Record every _publish_duck call instead of touching MQTT."""
    events = []
    with patch.object(vivi_v2, '_publish_duck', side_effect=events.append):
        yield events


class TestDuckRefcount:
    def test_acquire_publishes_true_once(self, duck_events):
        vivi_v2._acquire_duck()
        vivi_v2._acquire_duck()
        assert duck_events == [True]
        assert vivi_v2._active_speakers == 2

    def test_release_waits_out_tail_before_false(self, duck_events):
        vivi_v2._acquire_duck()
        done = threading.Event()

        def _rel():
            vivi_v2._release_duck()
            done.set()

        t = threading.Thread(target=_rel)
        t.start()
        # Still inside the 10ms tail (or just past it) — join covers it, but
        # the point is the clear arrives only AFTER the tail, never before.
        t.join(timeout=5)
        assert done.is_set()
        assert duck_events == [True, False]

    def test_inter_sentence_race_does_not_prematurely_unmute(self, duck_events):
        """The core race: sentence N releases while sentence N+1 acquires
        mid-tail — no *clear* may be published until BOTH are done. (A
        re-acquire that lands after the releaser's decrement legitimately
        republishes true — the window closed and reopened — so the invariant
        under test is that false never appears while a speaker is active.)"""
        vivi_v2._DUCK_TAIL_S = 0.30
        vivi_v2._acquire_duck()
        t = threading.Thread(target=vivi_v2._release_duck)
        t.start()
        time.sleep(0.05)          # releaser is now parked in the tail sleep
        vivi_v2._acquire_duck()   # sentence N+1 grabs the window mid-tail
        t.join(timeout=5)
        assert False not in duck_events, f"clear leaked mid-sentence: {duck_events}"
        vivi_v2._release_duck()
        deadline = time.time() + 5
        while time.time() < deadline and duck_events[-1] is not False:
            time.sleep(0.01)
        assert duck_events[-1] is False

    def test_concurrent_workers_balance_to_cleared_state(self, duck_events):
        errors = []

        def worker():
            try:
                vivi_v2._acquire_duck()
                time.sleep(0.005)
                vivi_v2._release_duck()
            except Exception as e:  # pragma: no cover — surfaced via assert
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)
        assert errors == []
        assert vivi_v2._active_speakers == 0
        assert duck_events[0] is True
        assert duck_events[-1] is False

    def test_overrelease_clamps_and_stays_clear(self, duck_events):
        vivi_v2._acquire_duck()
        vivi_v2._release_duck()
        vivi_v2._release_duck()  # bug guard: extra release must not corrupt
        assert vivi_v2._active_speakers == 0


class TestSpeakWindow:
    def test_speak_brackets_playback_with_duck(self, duck_events, tmp_path):
        """speak() must raise the duck before playback work and release it
        even when piper produced nothing usable (timeout/error paths)."""
        fake_proc = MagicMock()
        fake_proc.communicate.return_value = (b"", b"")
        with patch.object(vivi_v2, 'AUDIO_DIR', tmp_path), \
             patch.object(vivi_v2.subprocess, 'Popen', return_value=fake_proc), \
             patch.object(vivi_v2, '_aplay_ready', return_value=True):
            vivi_v2.speak("duck check")
        assert duck_events == [True, False]
        assert vivi_v2._active_speakers == 0

    def test_speak_no_text_never_opens_window(self, duck_events):
        vivi_v2.speak("   ")
        assert duck_events == []


class TestLwtPayload:
    def test_lwt_clears_duck_and_identifies_source(self):
        payload = vivi_v2._duck_lwt_payload()
        assert payload['duck'] is False
        assert payload['src'] == 'vivi2-lwt'
        assert isinstance(payload['ts'], float)

    def test_lwt_payload_json_round_trip(self):
        decoded = json.loads(json.dumps(vivi_v2._duck_lwt_payload()))
        assert decoded['duck'] is False


# ═══════════════════════════════════════════════════════════════════
#  voice_input — subscriber side
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_voicein_duck_state():
    for attr in ('_speaker_duck', '_rfaudio_duck'):
        setattr(voice_input, attr, False)
    voice_input._speaker_duck_ts = 0.0
    voice_input._rfaudio_duck_ts = 0.0
    yield


def _msg(topic, payload):
    return SimpleNamespace(topic=topic, payload=payload)


class TestDuckStaleness:
    def test_fresh_speaker_duck_is_ducked(self):
        voice_input._speaker_duck = True
        voice_input._speaker_duck_ts = time.time()
        assert voice_input._is_ducked() is True

    def test_stale_speaker_duck_expires(self):
        voice_input._speaker_duck = True
        voice_input._speaker_duck_ts = time.time() - (voice_input._DUCK_MAX_AGE_S + 1)
        assert voice_input._is_ducked() is False

    def test_rfaudio_playback_is_ducked_while_fresh(self):
        voice_input._rfaudio_duck = True
        voice_input._rfaudio_duck_ts = time.time()
        assert voice_input._is_ducked() is True

    def test_rfaudio_playback_expires_when_publisher_dies(self):
        voice_input._rfaudio_duck = True
        voice_input._rfaudio_duck_ts = time.time() - (voice_input._DUCK_MAX_AGE_S + 1)
        assert voice_input._is_ducked() is False

    def test_no_flags_not_ducked(self):
        assert voice_input._is_ducked() is False


class TestDuckMessaging:
    def test_duck_true_payload_sets_flag(self):
        voice_input.on_voice_message(None, None, _msg(
            voice_input.TOPICS['voice_duck'],
            json.dumps({'duck': True, 'src': 'vivi2', 'ts': time.time()}).encode(),
        ))
        assert voice_input._is_ducked() is True

    def test_duck_false_payload_clears_flag(self):
        voice_input._speaker_duck = True
        voice_input._speaker_duck_ts = time.time()
        voice_input.on_voice_message(None, None, _msg(
            voice_input.TOPICS['voice_duck'],
            json.dumps({'duck': False, 'src': 'vivi2', 'ts': time.time()}).encode(),
        ))
        assert voice_input._is_ducked() is False

    def test_rfaudio_playing_and_scanning_raise_duck_idle_clears(self):
        topic = voice_input.TOPICS['rfaudio_status']
        for state in ('playing', 'scanning'):
            voice_input.on_voice_message(None, None, _msg(
                topic, json.dumps({'state': state, 'ts': time.time()}).encode(),
            ))
            assert voice_input._is_ducked() is True, state
        voice_input.on_voice_message(None, None, _msg(
            topic, json.dumps({'state': 'idle', 'ts': time.time()}).encode(),
        ))
        assert voice_input._is_ducked() is False

    def test_malformed_duck_payload_is_ignored(self):
        voice_input.on_voice_message(None, None, _msg(
            voice_input.TOPICS['voice_duck'], b'not-json{',
        ))
        assert voice_input._speaker_duck is False
        assert voice_input._is_ducked() is False


class TestDuckSubscription:
    def test_on_connect_subscribes_duck_and_rfaudio_topics(self):
        client = MagicMock()
        voice_input.on_connect(client, None, None, 0)
        subscribed = {c.args[0] for c in client.subscribe.call_args_list}
        from config import TOPICS
        assert TOPICS['voice_listen_now'] in subscribed
        assert TOPICS['voice_duck'] in subscribed
        assert TOPICS['rfaudio_status'] in subscribed
