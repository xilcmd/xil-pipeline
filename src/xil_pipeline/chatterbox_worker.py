# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Persistent Chatterbox TTS worker process.

Run with the chatterbox venv Python, not the main pipeline venv::

    venv-chatterbox/bin/python3 chatterbox_worker.py [cuda|cpu]

Protocol (newline-delimited JSON on stdin/stdout):

  Startup:  worker prints  {"ready": true, "sr": <int>}
  Request:  {"text": "...", "out_path": "...", "ref_audio": "<path>|null",
             "cond_path": "<path>|null", "exaggeration": 0.5, "cfg_weight": 0.5}
  Response: {"done": true} | {"done": true, "skipped": true} | {"error": "..."}

cond_path caching
-----------------
If ``cond_path`` points to an existing ``.conds.pt`` file the worker loads the
pre-computed ``Conditionals`` object directly, skipping reference-audio processing
(fast path).  On the first run, if ``ref_audio`` is provided together with
``cond_path``, the computed conditionals are saved to ``cond_path`` for reuse in
subsequent sessions (slow path → write).  Deleting the ``.conds.pt`` file forces
a rebuild — required when ``--exaggeration`` changes, because that value is baked
into the cached conditionals.

eleven_v3 inline tags ([pause], [exhausted], etc.) are stripped before
generation so they are not read aloud verbatim.
"""

import contextlib
import json
import os
import re
import sys
import tempfile

TAG_RE = re.compile(r'\[[^\]]*\]')


def main() -> None:
    device = sys.argv[1] if len(sys.argv) > 1 else "cuda"

    # Suppress noisy deprecation warnings from diffusers / torch internals
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)

    import torchaudio  # type: ignore[import]
    from chatterbox.tts import ChatterboxTTS  # type: ignore[import]
    from pydub import AudioSegment  # type: ignore[import]

    model = ChatterboxTTS.from_pretrained(device=device)
    print(json.dumps({"ready": True, "sr": model.sr}), flush=True)

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"JSON decode: {exc}"}), flush=True)
            continue

        text = TAG_RE.sub("", req["text"]).strip()
        out_path = req["out_path"]
        ref_audio = req.get("ref_audio") or None
        cond_path = req.get("cond_path") or None
        exaggeration = float(req.get("exaggeration", 0.5))
        cfg_weight = float(req.get("cfg_weight", 0.5))

        if not text:
            print(json.dumps({"done": True, "skipped": True}), flush=True)
            continue

        tmp_wav = None
        tmp_mp3 = None
        try:
            if cond_path and os.path.exists(cond_path):
                # Fast path: pre-computed conditioning — skip ref audio processing
                from chatterbox.tts import Conditionals  # type: ignore[import]
                model.conds = Conditionals.load(cond_path, map_location=device)
                print(f"[conds] loaded ← {os.path.basename(cond_path)}", file=sys.stderr, flush=True)
                wav = model.generate(text, cfg_weight=cfg_weight)
            elif ref_audio:
                # Slow path: compute from ref audio, save conds for next session
                wav = model.generate(
                    text,
                    audio_prompt_path=ref_audio,
                    exaggeration=exaggeration,
                    cfg_weight=cfg_weight,
                )
                if cond_path and model.conds is not None:
                    os.makedirs(os.path.dirname(os.path.abspath(cond_path)), exist_ok=True)
                    model.conds.save(cond_path)
                    print(f"[conds] saved  → {os.path.basename(cond_path)}", file=sys.stderr, flush=True)
            else:
                # No ref, no cache: use model default voice
                wav = model.generate(text, cfg_weight=cfg_weight)

            # WAV → temp file → MP3 → final path (atomic replace)
            tmp_fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(tmp_fd)
            torchaudio.save(tmp_wav, wav, model.sr)

            stem_dir = os.path.dirname(out_path) or "."
            tmp_fd2, tmp_mp3 = tempfile.mkstemp(suffix=".mp3", dir=stem_dir)
            os.close(tmp_fd2)
            AudioSegment.from_wav(tmp_wav).export(
                tmp_mp3,
                format="mp3",
                bitrate="128k",
                parameters=["-ar", "44100"],
            )
            os.replace(tmp_mp3, out_path)
            tmp_mp3 = None  # replaced — don't clean up

            print(json.dumps({"done": True}), flush=True)

        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": str(exc)}), flush=True)

        finally:
            for p in (tmp_wav, tmp_mp3):
                if p is not None:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(p)


if __name__ == "__main__":
    main()
