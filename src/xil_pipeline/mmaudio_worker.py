# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Persistent MMAudio sound-effect worker process (text-to-audio mode).

Run with the venv-mmaudio Python, not the main pipeline venv::

    venv-mmaudio/bin/python3 mmaudio_worker.py [cuda|cpu]

The worker loads MMAudio once at startup and generates audio on demand, keeping
the model resident across all requests in a session.

**Licence:** MMAudio's code is MIT but its **weights are CC BY-NC 4.0 —
non-commercial use only**.  Audio produced here must not be used in a monetised
production.  The parent process gates construction behind an explicit
acknowledgement and tags every generated asset; this worker restates the
constraint on startup so it is visible in the logs of any run.

MMAudio is primarily a *video*-to-audio model.  This worker uses its
text-to-audio path (``clip_frames=None, sync_frames=None``), which the project
supports by "simply omitting the ``--video`` option".

Protocol (newline-delimited JSON on stdin/stdout):

  Startup:  worker prints  {"ready": true, "sr": 44100, "device": "<device>"}
  Request:  {"prompt": "...", "out_path": "...", "duration_seconds": 8.0,
             "guidance_scale": 4.5, "num_inference_steps": 25,
             "negative_prompt": "", "seed": null | 42}
  Response: {"done": true} | {"error": "..."}

Duration
--------
MMAudio is trained at 8 seconds and the project warns that a large deviation
degrades quality.  The parent therefore asks for the native duration and trims
the result itself — this worker generates exactly what it is told and does not
second-guess the length.

Output is 44.1 kHz.  The waveform is written to a temp WAV, transcoded to MP3
via pydub to match the rest of the SFX library, and atomically moved into place.
"""

import contextlib
import json
import os
import sys
import tempfile

_DEFAULT_VARIANT = "large_44k_v2"


def main() -> None:
    """Load MMAudio and serve text-to-audio SFX requests via the JSON protocol."""
    device = sys.argv[1] if len(sys.argv) > 1 else "cuda"

    print(
        "[mmaudio] NOTE: MMAudio weights are CC BY-NC 4.0 — NON-COMMERCIAL USE ONLY.",
        file=sys.stderr,
        flush=True,
    )

    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)

    import torch  # type: ignore[import]
    import torchaudio  # type: ignore[import]
    from pydub import AudioSegment  # type: ignore[import]

    try:
        from mmaudio.eval_utils import ModelConfig, all_model_cfg, generate
        from mmaudio.model.flow_matching import FlowMatching
        from mmaudio.model.networks import get_my_mmaudio
        from mmaudio.model.utils.features_utils import FeaturesUtils
    except ImportError as exc:
        print(
            "[mmaudio] Cannot import the mmaudio package. It is not on PyPI — "
            "clone it and install into venv-mmaudio:\n"
            "  git clone https://github.com/hkchengrex/MMAudio\n"
            "  venv-mmaudio/bin/pip install -e MMAudio\n"
            f"  (import error: {exc})",
            file=sys.stderr,
            flush=True,
        )
        raise

    # CUDA may be requested but unavailable (no GPU / bad driver); fall back to CPU
    # before reporting ready so the parent never waits on a doomed worker.
    if device == "cuda" and not torch.cuda.is_available():
        print("[mmaudio] CUDA unavailable, falling back to cpu", file=sys.stderr, flush=True)
        device = "cpu"

    dtype = torch.bfloat16 if device == "cuda" else torch.float32

    model: ModelConfig = all_model_cfg[_DEFAULT_VARIANT]
    model.download_if_needed()
    seq_cfg = model.seq_cfg

    net = get_my_mmaudio(model.model_name).to(device, dtype).eval()
    net.load_weights(torch.load(model.model_path, map_location=device, weights_only=True))

    feature_utils = FeaturesUtils(
        tod_vae_ckpt=model.vae_path,
        synchformer_ckpt=model.synchformer_ckpt,
        enable_conditions=True,
        mode=model.mode,
        bigvgan_vocoder_ckpt=model.bigvgan_16k_path,
        need_vae_encoder=False,
    ).to(device, dtype).eval()

    print(
        json.dumps({"ready": True, "sr": int(seq_cfg.sampling_rate), "device": device}),
        flush=True,
    )

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
        duration_seconds = float(req.get("duration_seconds", 8.0))
        cfg_strength = float(req.get("guidance_scale", 4.5))
        num_steps = int(req.get("num_inference_steps", 25))
        negative_prompt = req.get("negative_prompt") or ""
        seed = req.get("seed")

        if not prompt:
            print(json.dumps({"error": "prompt is required"}), flush=True)
            continue
        if not out_path:
            print(json.dumps({"error": "out_path is required"}), flush=True)
            continue

        tmp_wav = None
        tmp_mp3 = None
        try:
            # seq_cfg carries the latent/clip lengths derived from duration; the
            # network has to be told about the change before sampling.
            seq_cfg.duration = duration_seconds
            net.update_seq_lengths(
                seq_cfg.latent_seq_len, seq_cfg.clip_seq_len, seq_cfg.sync_seq_len
            )

            rng = torch.Generator(device=device)
            if seed is None:
                rng.seed()
            else:
                rng.manual_seed(int(seed))

            fm = FlowMatching(min_sigma=0, inference_mode="euler", num_steps=num_steps)

            # torch.inference_mode() is required, not merely an optimisation:
            # MMAudio's demo.py decorates its whole entry point with it, and
            # without it generation dies with "Inference tensors cannot be saved
            # for backward" the moment a feature-extractor tensor reaches a
            # grad-enabled op.
            with torch.inference_mode():
                # Text-only: no video conditioning frames.
                audios = generate(
                    None,
                    None,
                    [prompt],
                    negative_text=[negative_prompt],
                    feature_utils=feature_utils,
                    net=net,
                    fm=fm,
                    rng=rng,
                    cfg_strength=cfg_strength,
                )
                audio = audios.float().cpu()[0]

            tmp_fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(tmp_fd)
            torchaudio.save(tmp_wav, audio, int(seq_cfg.sampling_rate))

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
