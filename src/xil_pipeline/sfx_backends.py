# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pluggable backends for SFX / music / ambience asset generation.

The pipeline historically generated every non-silence sound effect through the
ElevenLabs Sound Effects API.  This module introduces a thin :class:`SfxBackend`
adapter so the shared generation path in :mod:`xil_pipeline.sfx_common` no longer
talks to the ElevenLabs client directly.  Three backends are provided:

* :class:`ElevenLabsSfxBackend` — wraps ``client.text_to_sound_effects.convert``
  with stream-to-temp, atomic rename, and 429 / 5xx / network retry handling.
* :class:`AudioLDM2SfxBackend` — drives a local AudioLDM 2 Large diffusion model
  via a persistent worker subprocess (:mod:`xil_pipeline.audioldm2_worker`) in a
  dedicated ``venv-audioldm2`` virtualenv.  Free, GPU-accelerated, no API credits.
* :class:`StableAudioSfxBackend` — drives a local Stable Audio Open 1.0 model
  (:mod:`xil_pipeline.stableaudio_worker`); 44.1 kHz stereo output, ≤47.55 s per
  clip.  Shares ``venv-audioldm2`` (``StableAudioPipeline`` ships in the same
  diffusers install).  Weights are license-gated on HuggingFace — accept the
  license at the model page and authenticate via ``HF_TOKEN`` or
  ``huggingface-cli login`` once.

All expose the same minimal contract::

    backend.generate_to(out_path, prompt, duration_seconds, prompt_influence)
    backend.close()

Use :func:`make_sfx_backend` to construct the right backend from a CLI flag.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Protocol, runtime_checkable

import httpx
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError

from xil_pipeline.log_config import get_logger
from xil_pipeline.models import resolve_venv_python

logger = get_logger(__name__)


@runtime_checkable
class SfxBackend(Protocol):
    """Minimal contract for a sound-effect generation backend."""

    name: str

    def generate_to(
        self,
        out_path: str,
        prompt: str,
        duration_seconds: float,
        prompt_influence: float,
    ) -> None:
        """Generate audio for *prompt* and write it to *out_path*."""
        ...

    def close(self) -> None:
        """Release any resources (subprocess, sockets). No-op for stateless backends."""
        ...


# ── ElevenLabs ────────────────────────────────────────────────────────────────


class ElevenLabsSfxBackend:
    """SFX backend backed by the ElevenLabs Sound Effects API.

    Wraps ``client.text_to_sound_effects.convert`` with the streaming
    download, atomic temp-file rename, and retry behaviour that previously
    lived inline in :func:`xil_pipeline.sfx_common.ensure_shared_sfx`.
    Retries 429 (rate limit), 5xx (server error), and network transport
    errors up to five times with linear backoff (10s, 20s, …).
    """

    name = "elevenlabs"

    def __init__(self, client) -> None:
        self._client = client

    def generate_to(
        self,
        out_path: str,
        prompt: str,
        duration_seconds: float,
        prompt_influence: float,
    ) -> None:
        if self._client is None:
            raise ValueError(
                "ElevenLabs client is required to generate SFX "
                "(set ELEVENLABS_API_KEY or use --sfx-backend audioldm2)."
            )
        logger.info("   [api] text-to-sound-effects → %r (%.1fs)", prompt, duration_seconds)
        tmp_path = None
        try:
            max_retries, delay = 5, 10
            for attempt in range(1, max_retries + 1):
                try:
                    audio_stream = self._client.text_to_sound_effects.convert(
                        text=prompt,
                        duration_seconds=duration_seconds,
                        prompt_influence=prompt_influence,
                    )
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        dir=os.path.dirname(out_path) or ".", suffix=".tmp"
                    )
                    with os.fdopen(tmp_fd, "wb") as f:
                        for chunk in audio_stream:
                            if chunk:
                                f.write(chunk)
                    os.replace(tmp_path, out_path)
                    tmp_path = None
                    logger.info("   [api] saved %s", os.path.basename(out_path))
                    return
                except (ApiError, httpx.TransportError) as exc:
                    if tmp_path is not None:
                        with contextlib.suppress(FileNotFoundError):
                            os.unlink(tmp_path)
                        tmp_path = None
                    is_rate_limit = isinstance(exc, ApiError) and exc.status_code == 429
                    is_server_error = (
                        isinstance(exc, ApiError)
                        and exc.status_code is not None
                        and exc.status_code >= 500
                    )
                    is_network_error = isinstance(exc, httpx.TransportError)
                    is_retryable = is_rate_limit or is_server_error or is_network_error
                    if is_retryable and attempt < max_retries:
                        wait = delay * attempt
                        if is_rate_limit:
                            reason = "429 rate limited"
                        elif is_server_error:
                            reason = f"{exc.status_code} server error"
                        else:
                            reason = f"network error ({type(exc).__name__})"
                        logger.warning(
                            "[%s] — retrying in %ds (attempt %d/%d)",
                            reason, wait, attempt, max_retries,
                        )
                        time.sleep(wait)
                    else:
                        raise
        finally:
            if tmp_path is not None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_path)

    def close(self) -> None:
        # The ElevenLabs client is owned by the caller; nothing to release here.
        return


# ── Local diffusion workers ───────────────────────────────────────────────────


class _DiffusionWorkerClient:
    """Persistent subprocess bridge to a local diffusion worker script.

    Subclasses set the class attributes ``_WORKER`` (worker script path,
    monkeypatched to a stub in tests), ``_LABEL`` (log/error prefix), and
    ``_READY_HINT`` (appended to the exited-before-ready error), and may
    override :meth:`_request_extras` to add backend-specific request fields.

    The worker runs under a dedicated venv Python and keeps the model loaded
    across all generation requests.  Communication uses newline-delimited JSON
    on stdin/stdout, mirroring
    :class:`xil_pipeline.XILP002_producer._ChatterboxClient`.
    """

    _WORKER: str
    _LABEL: str
    _READY_HINT: str

    def __init__(
        self,
        python_path: str,
        device: str = "cuda",
        guidance: float = 3.5,
        steps: int = 200,
        negative_prompt: str = "low quality, noise",
    ) -> None:
        self._python = python_path
        self._device = device
        self._guidance = guidance
        self._steps = steps
        self._negative_prompt = negative_prompt
        self._proc: subprocess.Popen | None = None

    def _request_extras(self) -> dict:
        """Return backend-specific fields merged into every generation request."""
        return {}

    def _start(self) -> None:
        logger.info("Starting %s worker (%s, %s)…", self._LABEL, self._python, self._device)
        self._proc = subprocess.Popen(
            [self._python, self._WORKER, self._device],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            bufsize=1,
        )
        # diffusers/transformers print progress noise to stdout before the ready
        # signal; loop until a valid JSON ready line appears.
        while True:
            raw = self._proc.stdout.readline()
            if not raw:
                raise RuntimeError(
                    f"{self._LABEL} worker exited before sending ready signal. "
                    + self._READY_HINT
                )
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("%s worker startup: %s", self._LABEL, raw)
                continue
            if msg.get("ready"):
                break
            logger.debug("%s worker startup: %s", self._LABEL, raw)
        logger.info(
            "%s worker ready (sample_rate=%d, device=%s)",
            self._LABEL, msg.get("sr", 16000), msg.get("device", self._device),
        )

    def generate(self, prompt: str, out_path: str, duration_seconds: float) -> None:
        """Send one generation request to the worker and wait for completion."""
        if self._proc is None:
            self._start()
        req = {
            "prompt": prompt,
            "out_path": out_path,
            "duration_seconds": duration_seconds,
            "guidance_scale": self._guidance,
            "num_inference_steps": self._steps,
            "negative_prompt": self._negative_prompt,
        }
        req.update(self._request_extras())
        assert self._proc is not None
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        raw = self._proc.stdout.readline()
        if not raw:
            raise RuntimeError(f"{self._LABEL} worker closed pipe unexpectedly.")
        resp = json.loads(raw)
        if "error" in resp:
            raise RuntimeError(f"{self._LABEL}: {resp['error']}")

    def close(self) -> None:
        """Shut down the worker subprocess (idempotent)."""
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.stdin.close()
            with contextlib.suppress(Exception):
                self._proc.wait(timeout=15)
            self._proc = None


class _AudioLDM2Client(_DiffusionWorkerClient):
    """Subprocess bridge to the AudioLDM 2 worker (:mod:`xil_pipeline.audioldm2_worker`)."""

    _WORKER = os.path.join(os.path.dirname(__file__), "audioldm2_worker.py")
    _LABEL = "AudioLDM 2"
    _READY_HINT = (
        "Check that venv-audioldm2 is set up (diffusers, transformers, "
        "torch, scipy, soundfile, pydub) and the model is downloaded."
    )


class _StableAudioClient(_DiffusionWorkerClient):
    """Subprocess bridge to the Stable Audio Open worker (:mod:`xil_pipeline.stableaudio_worker`)."""

    _WORKER = os.path.join(os.path.dirname(__file__), "stableaudio_worker.py")
    _LABEL = "Stable Audio"
    _READY_HINT = (
        "Check that venv-audioldm2 is set up (the stableaudio backend shares it) "
        "and that the license-gated weights are accessible: accept the license at "
        "https://huggingface.co/stabilityai/stable-audio-open-1.0 and set HF_TOKEN "
        "or run `huggingface-cli login`."
    )

    def __init__(
        self,
        python_path: str,
        device: str = "cuda",
        guidance: float = 7.0,
        steps: int = 100,
        negative_prompt: str = "low quality, average quality",
        seed: int | None = None,
    ) -> None:
        super().__init__(
            python_path,
            device=device,
            guidance=guidance,
            steps=steps,
            negative_prompt=negative_prompt,
        )
        self._seed = seed

    def _request_extras(self) -> dict:
        """Add the reproducibility seed to every request (``None`` = nondeterministic)."""
        return {"seed": self._seed}


class AudioLDM2SfxBackend:
    """SFX backend backed by a local AudioLDM 2 Large diffusion model.

    Adherence to the prompt is governed by ``guidance``/``steps`` (configured
    at construction), so the ElevenLabs-specific ``prompt_influence`` argument
    is accepted for interface compatibility but ignored.
    """

    name = "audioldm2"

    def __init__(self, client: _AudioLDM2Client) -> None:
        self._client = client

    def generate_to(
        self,
        out_path: str,
        prompt: str,
        duration_seconds: float,
        prompt_influence: float,  # noqa: ARG002 — AudioLDM 2 uses guidance_scale instead
    ) -> None:
        logger.info("   [audioldm2] generating → %r (%.1fs)", prompt, duration_seconds)
        self._client.generate(prompt, out_path, duration_seconds)
        logger.info("   [audioldm2] saved %s", os.path.basename(out_path))

    def close(self) -> None:
        self._client.close()


class StableAudioSfxBackend:
    """SFX backend backed by a local Stable Audio Open 1.0 model.

    Adherence to the prompt is governed by ``guidance``/``steps`` (configured
    at construction), so the ElevenLabs-specific ``prompt_influence`` argument
    is accepted for interface compatibility but ignored.
    """

    name = "stableaudio"

    def __init__(self, client: _StableAudioClient) -> None:
        self._client = client

    def generate_to(
        self,
        out_path: str,
        prompt: str,
        duration_seconds: float,
        prompt_influence: float,  # noqa: ARG002 — Stable Audio uses guidance_scale instead
    ) -> None:
        logger.info("   [stableaudio] generating → %r (%.1fs)", prompt, duration_seconds)
        self._client.generate(prompt, out_path, duration_seconds)
        logger.info("   [stableaudio] saved %s", os.path.basename(out_path))

    def close(self) -> None:
        self._client.close()


# ── Factory ───────────────────────────────────────────────────────────────────


def _find_audioldm2_python(explicit: str | None, flag: str = "--audioldm2-python") -> str:
    """Resolve the venv-audioldm2 Python via :func:`resolve_venv_python`.

    Resolution: explicit path → ``$XIL_CODEROOT/venv-audioldm2`` (exclusive when set)
    → auto-detect at workspace root, then repo root.  Exits with an actionable error
    (naming *flag*) if none is found.  The stableaudio backend shares this venv.
    """
    py = resolve_venv_python("venv-audioldm2", explicit)
    if py is None:
        logger.error(
            "Cannot find the venv-audioldm2 Python. Pass %s PATH, "
            "set XIL_CODEROOT to the directory containing venv-audioldm2/, "
            "or create venv-audioldm2/ in the workspace or repo root.",
            flag,
        )
        sys.exit(1)
    return py


def make_sfx_backend(
    name: str,
    client: ElevenLabs | None = None,
    *,
    audioldm2_python: str | None = None,
    stableaudio_python: str | None = None,
    device: str = "cuda",
    guidance: float = 3.5,
    steps: int = 200,
    negative_prompt: str = "low quality, noise",
    seed: int | None = None,
) -> SfxBackend:
    """Construct an :class:`SfxBackend` for the given backend *name*.

    Args:
        name: ``"elevenlabs"``, ``"audioldm2"``, or ``"stableaudio"``.
        client: ElevenLabs client (used only for ``"elevenlabs"``).
        audioldm2_python: Explicit path to the venv-audioldm2 Python; auto-detected
            when ``None``.
        stableaudio_python: Explicit Python for the stableaudio backend; defaults
            to the same venv-audioldm2 resolution (shared venv).
        device: ``"cuda"`` (default) or ``"cpu"`` for the local backends.
        guidance: ``guidance_scale`` for the local backends (callers pass the
            backend-appropriate value; defaults reflect audioldm2).
        steps: ``num_inference_steps`` for the local backends.
        negative_prompt: Negative prompt for the local backends.
        seed: Reproducibility seed (stableaudio only; ``None`` = nondeterministic).

    Returns:
        A ready-to-use backend instance.
    """
    if name == "elevenlabs":
        return ElevenLabsSfxBackend(client)
    if name == "audioldm2":
        py = _find_audioldm2_python(audioldm2_python)
        worker_client = _AudioLDM2Client(
            python_path=py,
            device=device,
            guidance=guidance,
            steps=steps,
            negative_prompt=negative_prompt,
        )
        return AudioLDM2SfxBackend(worker_client)
    if name == "stableaudio":
        py = _find_audioldm2_python(stableaudio_python, flag="--stableaudio-python")
        sa_client = _StableAudioClient(
            python_path=py,
            device=device,
            guidance=guidance,
            steps=steps,
            negative_prompt=negative_prompt,
            seed=seed,
        )
        return StableAudioSfxBackend(sa_client)
    raise ValueError(f"Unknown sfx backend: {name!r}")
