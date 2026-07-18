# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Persistent Stable Audio Open sound-effect/music/ambience worker process.

Run with the venv-audioldm2 Python (``StableAudioPipeline`` ships in the same
diffusers install), not the main pipeline venv::

    venv-audioldm2/bin/python3 stableaudio_worker.py [cuda|cpu]

The worker loads ``stabilityai/stable-audio-open-1.0`` once at startup and
generates audio on demand, keeping the model resident across all requests in
a session.

Protocol (newline-delimited JSON on stdin/stdout):

  Startup:  worker prints  {"ready": true, "sr": 44100, "device": "<device>"}
  Request:  {"prompt": "...", "out_path": "...", "duration_seconds": 5.0,
             "guidance_scale": 7.0, "num_inference_steps": 100,
             "negative_prompt": "low quality, average quality",
             "seed": null | 42}
  Response: {"done": true} | {"done": true, "clamped_to": 47.55} | {"error": "..."}

Stable Audio Open emits 44.1 kHz stereo.  The worker writes the waveform to a
temp WAV, transcodes to MP3 via pydub (matching the rest of the SFX library),
and atomically replaces ``out_path``.

Parameter notes
---------------
``duration_seconds`` maps to the pipeline's SFX ``duration_seconds`` and the
model's ``audio_end_in_s``; requests beyond the model maximum (47.55 s for
stable-audio-open-1.0) are clamped with a stderr warning and a ``clamped_to``
field in the response.  There is no ``prompt_influence``; adherence to the
prompt is controlled by ``guidance_scale``.  ``seed`` makes generation
reproducible (``null`` = nondeterministic).

The model weights are license-gated on HuggingFace: accept the license at
https://huggingface.co/stabilityai/stable-audio-open-1.0 while logged in, then
authenticate this venv by setting ``HF_TOKEN`` or running
``huggingface-cli login``.
"""

import contextlib
import json
import os
import sys
import tempfile

_MODEL_ID = "stabilityai/stable-audio-open-1.0"


def main() -> None:
    """Load Stable Audio Open and serve SFX/music generation requests via JSON protocol."""
    device = sys.argv[1] if len(sys.argv) > 1 else "cuda"

    # Suppress noisy deprecation warnings from diffusers / torch internals
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)

    import scipy.io.wavfile  # type: ignore[import]
    import torch  # type: ignore[import]
    from diffusers import StableAudioPipeline  # type: ignore[import]
    from pydub import AudioSegment  # type: ignore[import]

    # CUDA may be requested but unavailable (no GPU / bad driver); fall back to CPU
    # before reporting ready so the parent never waits on a doomed worker.
    if device == "cuda" and not torch.cuda.is_available():
        print("[stableaudio] CUDA unavailable, falling back to cpu", file=sys.stderr, flush=True)
        device = "cpu"

    torch_dtype = torch.float16 if device == "cuda" else torch.float32
    try:
        pipe = StableAudioPipeline.from_pretrained(_MODEL_ID, torch_dtype=torch_dtype)
    except Exception as exc:
        msg = str(exc).lower()
        if any(k in msg for k in ("gated", "401", "403", "authoriz", "access", "token")):
            print(
                "[stableaudio] Cannot download stabilityai/stable-audio-open-1.0 — "
                "the weights are license-gated. (1) Accept the license at "
                "https://huggingface.co/stabilityai/stable-audio-open-1.0 while "
                "logged in, and (2) authenticate: set HF_TOKEN in the environment "
                "or run `huggingface-cli login` with the venv-audioldm2 Python.",
                file=sys.stderr,
                flush=True,
            )
        raise
    pipe = pipe.to(device)

    sample_rate = int(pipe.vae.sampling_rate)  # 44100 for stable-audio-open-1.0
    max_seconds = float(pipe.projection_model.config.max_value)  # 47.55

    print(json.dumps({"ready": True, "sr": sample_rate, "device": device}), flush=True)

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
        guidance_scale = float(req.get("guidance_scale", 7.0))
        num_inference_steps = int(req.get("num_inference_steps", 100))
        negative_prompt = req.get("negative_prompt") or None
        seed = req.get("seed")

        if not prompt:
            print(json.dumps({"error": "prompt is required"}), flush=True)
            continue
        if not out_path:
            print(json.dumps({"error": "out_path is required"}), flush=True)
            continue

        clamped_to = None
        if duration_seconds > max_seconds:
            print(
                f"[stableaudio] duration {duration_seconds:.1f}s exceeds model max "
                f"{max_seconds:.2f}s; clamping",
                file=sys.stderr,
                flush=True,
            )
            duration_seconds = max_seconds
            clamped_to = max_seconds

        tmp_wav = None
        tmp_mp3 = None
        try:
            generator = None
            if seed is not None:
                generator = torch.Generator(device).manual_seed(int(seed))
            result = pipe(
                prompt,
                negative_prompt=negative_prompt,
                audio_end_in_s=duration_seconds,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                generator=generator,
            )
            audio = result.audios[0]  # torch tensor (channels, samples), 44.1 kHz
            waveform = audio.T.float().cpu().numpy()  # (samples, channels) for scipy

            # Waveform → temp WAV → MP3 → final path (atomic replace)
            tmp_fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(tmp_fd)
            scipy.io.wavfile.write(tmp_wav, sample_rate, waveform)

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

            resp = {"done": True}
            if clamped_to is not None:
                resp["clamped_to"] = clamped_to
            print(json.dumps(resp), flush=True)

        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": str(exc)}), flush=True)

        finally:
            for p in (tmp_wav, tmp_mp3):
                if p is not None:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(p)


if __name__ == "__main__":
    main()
