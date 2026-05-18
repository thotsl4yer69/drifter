"""Tests for rfaudio — MQTT-controlled rtl_fm → aplay bridge."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import rfaudio


@pytest.fixture(autouse=True)
def _reset_module_state():
    """rfaudio holds module-level state for the active stream + scan thread.
    Reset between tests so leakage doesn't bleed across cases."""
    rfaudio._stream.stop()
    rfaudio._state = 'idle'
    yield
    rfaudio._stream.stop()
    rfaudio._state = 'idle'


def _fake_proc(returncode=None):
    """Stub for subprocess.Popen — looks alive when returncode is None."""
    p = MagicMock()
    p.poll.return_value = returncode
    p.stdout = MagicMock()
    return p


def test_audiostream_start_invokes_rtl_fm_and_aplay():
    with patch('rfaudio.subprocess.Popen') as popen:
        popen.side_effect = [_fake_proc(), _fake_proc()]
        ok = rfaudio._stream.start(476.525, 'nfm', 0)
    assert ok is True
    # Two subprocess.Popen calls: rtl_fm then aplay
    assert popen.call_count == 2
    rtl_cmd = popen.call_args_list[0][0][0]
    aplay_cmd = popen.call_args_list[1][0][0]
    assert rtl_cmd[0] == 'rtl_fm'
    assert '-f' in rtl_cmd and '476525000' in rtl_cmd
    assert '-M' in rtl_cmd and 'nfm' in rtl_cmd
    assert aplay_cmd[0] == 'aplay'
    assert rfaudio._stream.freq_mhz == 476.525
    assert rfaudio._stream.mode == 'nfm'


def test_audiostream_start_passes_explicit_gain():
    """Gain > 0 must reach the rtl_fm command line; gain 0 means auto and is omitted."""
    with patch('rfaudio.subprocess.Popen') as popen:
        popen.side_effect = [_fake_proc(), _fake_proc()]
        rfaudio._stream.start(476.525, 'nfm', 28.0)
    rtl_cmd = popen.call_args_list[0][0][0]
    assert '-g' in rtl_cmd
    g_idx = rtl_cmd.index('-g')
    assert rtl_cmd[g_idx + 1] == '28.0'


def test_audiostream_start_gain_zero_means_auto():
    with patch('rfaudio.subprocess.Popen') as popen:
        popen.side_effect = [_fake_proc(), _fake_proc()]
        rfaudio._stream.start(476.525, 'nfm', 0)
    rtl_cmd = popen.call_args_list[0][0][0]
    assert '-g' not in rtl_cmd


def test_audiostream_stop_is_safe_when_nothing_running():
    """Stopping an already-stopped stream must not raise."""
    rfaudio._stream.stop()
    assert rfaudio._stream.freq_mhz is None
    assert rfaudio._stream.mode is None


def test_audiostream_stop_terminates_both_processes():
    rtl, ap = _fake_proc(), _fake_proc()
    rfaudio._stream._rtl = rtl
    rfaudio._stream._aplay = ap
    rfaudio._stream.freq_mhz = 476.525
    rfaudio._stream.mode = 'nfm'
    rfaudio._stream.stop()
    rtl.terminate.assert_called_once()
    ap.terminate.assert_called_once()
    assert rfaudio._stream.freq_mhz is None


_PRESENT_SDR = {'device': 'rtl_sdr', 'connected': True, 'detail': 'RTL-SDR dongle on USB bus', 'action': '', 'ts': 0}
_MISSING_SDR = {'device': 'rtl_sdr', 'connected': False, 'detail': 'No RTL-SDR detected', 'action': 'Plug in RTL-SDR dongle', 'ts': 0}


def test_handle_command_start_paused_rtl_433_first():
    """start must publish pause_rtl_433 BEFORE spawning rtl_fm so the
    drifter-rf service releases the SDR. Order matters here."""
    client = MagicMock()
    publish_calls = []
    client.publish.side_effect = lambda topic, payload, **kw: publish_calls.append((topic, payload))

    with patch('rfaudio.probe_rtl_sdr', return_value=_PRESENT_SDR), \
         patch('rfaudio.subprocess.Popen') as popen, \
         patch('rfaudio.time.sleep'):
        popen.side_effect = [_fake_proc(), _fake_proc()]
        rfaudio._handle_command(client, {
            'action': 'start', 'freq_mhz': 476.525, 'mode': 'nfm',
        })

    # First publish should be the rf_command pause; status publish comes after
    topics = [c[0] for c in publish_calls]
    assert 'drifter/rf/command' in topics
    rf_idx = topics.index('drifter/rf/command')
    assert json.loads(publish_calls[rf_idx][1])['command'] == 'pause_rtl_433'
    assert rfaudio._state == 'playing'


def test_handle_command_start_refused_when_no_sdr():
    """The SDR guard must fail fast with a clear error, not spawn rtl_fm."""
    client = MagicMock()
    with patch('rfaudio.probe_rtl_sdr', return_value=_MISSING_SDR), \
         patch('rfaudio.subprocess.Popen') as popen:
        rfaudio._handle_command(client, {'action': 'start', 'freq_mhz': 476.525})
    popen.assert_not_called()
    assert rfaudio._state == 'idle'
    # Last publish must be an error payload
    last = client.publish.call_args_list[-1]
    payload = json.loads(last[0][1])
    assert 'error' in payload


@pytest.mark.parametrize('bad', [
    {'action': 'start', 'freq_mhz': 1e9},          # absurdly high
    {'action': 'start', 'freq_mhz': 0.5},           # below RTL-SDR range
    {'action': 'start', 'mode': 'wbfm-typo'},       # not in allowlist
    {'action': 'start', 'gain': -1},                # negative gain
    {'action': 'start', 'gain': 9999},              # absurd gain
    {'action': 'start', 'freq_mhz': 'banana'},      # not a number
])
def test_handle_command_start_rejects_invalid_params(bad):
    """Bounds + type validation must reject and not spawn rtl_fm."""
    client = MagicMock()
    with patch('rfaudio.probe_rtl_sdr', return_value=_PRESENT_SDR), \
         patch('rfaudio.subprocess.Popen') as popen, \
         patch('rfaudio.time.sleep'):
        rfaudio._handle_command(client, bad)
    popen.assert_not_called()
    last = client.publish.call_args_list[-1]
    payload = json.loads(last[0][1])
    assert 'error' in payload
    assert 'start refused' in payload['error']


def test_handle_command_scan_refused_when_no_sdr():
    client = MagicMock()
    with patch('rfaudio.probe_rtl_sdr', return_value=_MISSING_SDR):
        rfaudio._handle_command(client, {'action': 'scan'})
    last = client.publish.call_args_list[-1]
    payload = json.loads(last[0][1])
    assert 'error' in payload
    assert 'No RTL-SDR' in payload['error']


def test_handle_command_test_tone_calls_speaker_test():
    """test_tone must run speaker-test against the configured ALSA device."""
    client = MagicMock()
    with patch('rfaudio.subprocess.run') as run:
        rfaudio._handle_command(client, {'action': 'test_tone'})
    run.assert_called_once()
    cmd = run.call_args[0][0]
    assert cmd[0] == 'speaker-test'
    assert '-f' in cmd and '1000' in cmd  # 1kHz tone
    assert '-D' in cmd
    d_idx = cmd.index('-D')
    assert cmd[d_idx + 1] == rfaudio.RFAUDIO_APLAY_DEVICE


def test_handle_command_list_bands_publishes_full_band_list():
    client = MagicMock()
    rfaudio._handle_command(client, {'action': 'list_bands'})
    last = client.publish.call_args_list[-1]
    assert last[0][0] == 'drifter/rfaudio/status'
    payload = json.loads(last[0][1])
    assert 'bands' in payload
    assert isinstance(payload['bands'], list)
    assert len(payload['bands']) == len(rfaudio.EMERGENCY_AUDIO_BANDS)
    # Must include the UHF CB ch 5 emergency frequency as the default tune-in
    freqs = [b['freq_mhz'] for b in payload['bands']]
    assert 476.525 in freqs


def test_handle_command_stop_resumes_rtl_433():
    client = MagicMock()
    publish_calls = []
    client.publish.side_effect = lambda topic, payload, **kw: publish_calls.append((topic, payload))
    rfaudio._stream._rtl = _fake_proc()
    rfaudio._stream._aplay = _fake_proc()
    rfaudio._stream.freq_mhz = 476.525
    rfaudio._stream.mode = 'nfm'
    rfaudio._state = 'playing'

    rfaudio._handle_command(client, {'action': 'stop'})

    resume_published = any(
        topic == 'drifter/rf/command'
        and json.loads(payload).get('command') == 'resume_rtl_433'
        for topic, payload in publish_calls
    )
    assert resume_published, 'stop must publish resume_rtl_433'
    assert rfaudio._state == 'idle'


def test_handle_command_unknown_action_does_not_crash():
    client = MagicMock()
    rfaudio._handle_command(client, {'action': 'fly_to_the_moon'})
    assert rfaudio._state == 'idle'


def test_handle_command_uses_defaults_when_fields_missing():
    """start with no freq_mhz / mode must use the RFAUDIO_DEFAULT_* knobs."""
    client = MagicMock()
    with patch('rfaudio.probe_rtl_sdr', return_value=_PRESENT_SDR), \
         patch('rfaudio.subprocess.Popen') as popen, \
         patch('rfaudio.time.sleep'):
        popen.side_effect = [_fake_proc(), _fake_proc()]
        rfaudio._handle_command(client, {'action': 'start'})
    rtl_cmd = popen.call_args_list[0][0][0]
    expected_hz = str(int(rfaudio.RFAUDIO_DEFAULT_FREQ_MHZ * 1_000_000))
    assert expected_hz in rtl_cmd


def test_on_message_rejects_non_json_payload():
    """Bad payload must not crash the service; just log + return."""
    client = MagicMock()
    msg = MagicMock()
    msg.topic = 'drifter/rfaudio/command'
    msg.payload = b'this is not json'
    rfaudio.on_message(client, None, msg)
    # No state change, no exception
    assert rfaudio._state == 'idle'


def test_on_message_rejects_non_object_payload():
    """A bare JSON array or string is not a command."""
    client = MagicMock()
    msg = MagicMock()
    msg.topic = 'drifter/rfaudio/command'
    msg.payload = b'"hello"'
    rfaudio.on_message(client, None, msg)
    assert rfaudio._state == 'idle'
