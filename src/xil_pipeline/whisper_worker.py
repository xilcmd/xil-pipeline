# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Persistent Faster-Whisper STT worker process.

Run with the whisper venv Python, not the main pipeline venv::

    venv-whisper/bin/python3 whisper_worker.py [cuda|cpu] [model_size]

    model_size: tiny | base | small | medium | large-v3 | large-v3-turbo (default)

Protocol (newline-delimited JSON on stdin/stdout):

  Startup:  worker prints  {"ready": true, "model": "<model_size>", "device": "<device>"}
  Request:  {"audio_path": "...", "language": "en"|null, "beam_size": 5}
  Response: {"done": true, "text": "...", "language": "...", "segments": [...]}
            | {"error": "..."}

The worker loads the model once at startup and transcribes on demand.
Use language=null to enable automatic language detection.
"""

import json
import sys


def main() -> None:
    """Load Faster-Whisper and serve transcription requests via JSON protocol."""
    device = sys.argv[1] if len(sys.argv) > 1 else "cuda"
    model_size = sys.argv[2] if len(sys.argv) > 2 else "large-v3-turbo"

    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)

    from faster_whisper import WhisperModel  # type: ignore[import]

    compute_type = "float16" if device == "cuda" else "int8"
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    # Probe CUDA: ctranslate2 loads GPU kernels lazily, so a bad CUDA install
    # won't surface until the first transcribe() call.  Run a silent 1-second
    # test now so the fallback happens before we report ready.
    if device == "cuda":
        try:
            import numpy as np  # type: ignore[import]
            _probe = np.zeros(16000, dtype=np.float32)
            list(model.transcribe(_probe)[0])
        except Exception as _exc:
            print(f"[whisper] CUDA probe failed ({_exc}), falling back to cpu/int8", file=sys.stderr, flush=True)
            device = "cpu"
            compute_type = "int8"
            model = WhisperModel(model_size, device=device, compute_type=compute_type)

    print(json.dumps({"ready": True, "model": model_size, "device": device}), flush=True)

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"JSON decode: {exc}"}), flush=True)
            continue

        audio_path = req.get("audio_path", "")
        language = req.get("language") or None
        beam_size = int(req.get("beam_size", 5))

        if not audio_path:
            print(json.dumps({"error": "audio_path is required"}), flush=True)
            continue

        try:
            segments_iter, info = model.transcribe(
                audio_path,
                language=language,
                beam_size=beam_size,
            )
            segments = [
                {"start": s.start, "end": s.end, "text": s.text.strip()}
                for s in segments_iter
            ]
            full_text = " ".join(s["text"] for s in segments).strip()
            print(json.dumps({
                "done": True,
                "text": full_text,
                "language": info.language,
                "language_probability": round(info.language_probability, 4),
                "segments": segments,
            }), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
