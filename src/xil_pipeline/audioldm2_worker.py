# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Persistent AudioLDM 2 sound-effect/music/ambience worker process.

Run with the AudioLDM 2 venv Python, not the main pipeline venv::

    venv-audioldm2/bin/python3 audioldm2_worker.py [cuda|cpu]

The worker loads ``cvssp/audioldm2-large`` once at startup and generates audio
on demand, keeping the model resident across all requests in a session.

Protocol (newline-delimited JSON on stdin/stdout):

  Startup:  worker prints  {"ready": true, "sr": 16000, "device": "<device>"}
  Request:  {"prompt": "...", "out_path": "...", "duration_seconds": 5.0,
             "guidance_scale": 3.5, "num_inference_steps": 200,
             "negative_prompt": "low quality, noise"}
  Response: {"done": true} | {"error": "..."}

AudioLDM 2 emits a 16 kHz mono float waveform.  The worker writes it to a temp
WAV, transcodes to 44.1 kHz MP3 via pydub (matching the rest of the SFX
library), and atomically replaces ``out_path``.

Parameter notes
---------------
``duration_seconds`` maps to the pipeline's SFX ``duration_seconds`` /
ElevenLabs duration.  AudioLDM 2 has no ``prompt_influence``; adherence to the
prompt is controlled by ``guidance_scale`` instead.  ``audio_length_in_s``
quantises to the model's latent rate, so very short clips may run slightly long.
"""

import contextlib
import json
import os
import sys
import tempfile

_MODEL_ID = "cvssp/audioldm2-large"
_SAMPLE_RATE = 16000  # AudioLDM 2 output sample rate


def main() -> None:
    device = sys.argv[1] if len(sys.argv) > 1 else "cuda"

    # Suppress noisy deprecation warnings from diffusers / torch internals
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)

    import scipy.io.wavfile  # type: ignore[import]
    import torch  # type: ignore[import]
    from diffusers import AudioLDM2Pipeline  # type: ignore[import]
    from pydub import AudioSegment  # type: ignore[import]

    # CUDA may be requested but unavailable (no GPU / bad driver); fall back to CPU
    # before reporting ready so the parent never waits on a doomed worker.
    if device == "cuda" and not torch.cuda.is_available():
        print("[audioldm2] CUDA unavailable, falling back to cpu", file=sys.stderr, flush=True)
        device = "cpu"

    torch_dtype = torch.float16 if device == "cuda" else torch.float32
    pipe = AudioLDM2Pipeline.from_pretrained(_MODEL_ID, torch_dtype=torch_dtype)
    pipe = pipe.to(device)

    print(json.dumps({"ready": True, "sr": _SAMPLE_RATE, "device": device}), flush=True)

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"JSON decode: {exc}"}), flush=True)
            continue

        prompt = (req.get("prompt") or "").strip()
        out_path = req.get("out_path", "")
        duration_seconds = float(req.get("duration_seconds", 5.0))
        guidance_scale = float(req.get("guidance_scale", 3.5))
        num_inference_steps = int(req.get("num_inference_steps", 200))
        negative_prompt = req.get("negative_prompt") or None

        if not prompt:
            print(json.dumps({"error": "prompt is required"}), flush=True)
            continue
        if not out_path:
            print(json.dumps({"error": "out_path is required"}), flush=True)
            continue

        tmp_wav = None
        tmp_mp3 = None
        try:
            result = pipe(
                prompt,
                negative_prompt=negative_prompt,
                audio_length_in_s=duration_seconds,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
            )
            waveform = result.audios[0]  # float32 ndarray, 16 kHz mono

            # Waveform → temp WAV → MP3 → final path (atomic replace)
            tmp_fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(tmp_fd)
            scipy.io.wavfile.write(tmp_wav, _SAMPLE_RATE, waveform)

            stem_dir = os.path.dirname(out_path) or "."
            os.makedirs(stem_dir, exist_ok=True)
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
