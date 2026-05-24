#!/usr/bin/env python3
"""
Convert the Fly Catcher Keras .h5 model into a .tflite suitable for
loading on the Pi via ai-edge-litert (no full TensorFlow at runtime).

Usage:
    # On any machine with TensorFlow installed:
    pip install tensorflow
    python convert_fly_catcher_to_tflite.py \\
        /opt/drifter/state/fly_catcher/notebook/Spoof_Detection.h5

The .tflite is written next to the .h5. Once it lands at
/opt/drifter/state/fly_catcher/**/Spoof_Detection.tflite, restart
drifter-fly-catcher and the service picks it up.

Run on the Pi itself, OR convert on a laptop and scp the resulting
.tflite over — either works.

UNCAGED TECHNOLOGY — EST 1991
"""

from __future__ import annotations

import sys
from pathlib import Path


def convert(h5_path: Path) -> Path:
    """Convert a Keras .h5 into a .tflite next to it. Returns the .tflite path."""
    if not h5_path.exists():
        raise SystemExit(f"model not found: {h5_path}")
    if h5_path.suffix != '.h5':
        raise SystemExit(f"expected an .h5 model, got {h5_path.suffix}")

    try:
        import tensorflow as tf  # type: ignore
    except ImportError:
        raise SystemExit(
            "tensorflow is required for this one-off conversion.\n"
            "Install briefly with:  pip install tensorflow\n"
            "(LiteRT alone can't do .h5 -> .tflite — only TF can.)"
        )

    print(f"[convert] loading {h5_path}")
    model = tf.keras.models.load_model(str(h5_path))
    print(f"[convert] model loaded: inputs={model.inputs} outputs={model.outputs}")

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    # Float32 ops only — keeps the binary small and avoids quant headaches.
    # If size becomes a problem on real Pi RAM, switch to:
    #   converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite_bytes = converter.convert()

    out_path = h5_path.with_suffix('.tflite')
    out_path.write_bytes(tflite_bytes)
    print(f"[convert] wrote {out_path}  ({len(tflite_bytes):,} bytes)")
    return out_path


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    convert(Path(sys.argv[1]))


if __name__ == '__main__':
    main()
