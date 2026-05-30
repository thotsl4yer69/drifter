"""TTS audio generation for the web dashboard.

Generates WAV bytes from alert text using piper (preferred) or espeak-ng
(fallback). The dashboard streams these bytes to connected phones over a
WebSocket so the Jag speaks through Pioneer/Android Auto.

Kept in its own module so the audio pipeline can be unit-tested without
pulling in the HTTP/WS server.
"""
from __future__ import annotations

import struct
import subprocess
from pathlib import Path

from config import DRIFTER_DIR

PIPER_MODEL_PATH = DRIFTER_DIR / "piper-models" / "en_GB-alan-medium.onnx"

_PIPER_TIMEOUT = 10
_ESPEAK_TIMEOUT = 10


def _raw_to_wav(raw_data: bytes, rate: int = 22050,
                channels: int = 1, width: int = 2) -> bytes:
    """Wrap raw PCM in a WAV header.

    width = bytes per sample (2 = 16-bit). rate/channels match the piper
    model output for en_GB-alan-medium: 22050 Hz mono 16-bit.
    """
    data_size = len(raw_data)
    header = struct.pack('<4sI4s', b'RIFF', 36 + data_size, b'WAVE')
    fmt = struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, channels,
                      rate, rate * channels * width, channels * width,
                      width * 8)
    data_header = struct.pack('<4sI', b'data', data_size)
    return header + fmt + data_header + raw_data


def generate_audio_wav(text: str) -> bytes | None:
    """Return WAV-encoded TTS bytes for ``text``, or None on failure.

    Tries piper first (nicer voice) then falls back to espeak-ng.
    Never raises — returning None lets the caller skip the alert silently.
    """
    if not text:
        return None

    # piper is preferred — higher quality voice, model loaded from disk.
    if PIPER_MODEL_PATH.exists():
        try:
            proc = subprocess.Popen(
                ['piper', '--model', str(PIPER_MODEL_PATH), '--output-raw'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            raw, _ = proc.communicate(input=text.encode(),
                                      timeout=_PIPER_TIMEOUT)
            if raw:
                return _raw_to_wav(raw, rate=22050, channels=1, width=2)
        except Exception:
            pass

    # espeak-ng fallback — already emits a WAV on stdout, no wrapping needed.
    try:
        proc = subprocess.run(
            ['espeak-ng', '-v', 'en-gb', '-s', '150', '-p', '40',
             '--stdout', text],
            capture_output=True, timeout=_ESPEAK_TIMEOUT,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except Exception:
        pass

    return None
