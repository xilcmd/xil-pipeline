# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pluggable backends for SFX / music / ambience asset generation.

The pipeline generates every non-silence sound effect through the ElevenLabs
Sound Effects API.  This module keeps a thin :class:`SfxBackend` adapter so the
shared generation path in :mod:`xil_pipeline.sfx_common` does not talk to the
ElevenLabs client directly:

* :class:`ElevenLabsSfxBackend` — wraps ``client.text_to_sound_effects.convert``
  with stream-to-temp, atomic rename, and 429 / 5xx / network retry handling.

Two local diffusion backends (AudioLDM 2, Stable Audio Open) were removed in #62
after both trials produced unusable audio.  The adapter and factory survive them
deliberately — they are the seam a future backend plugs into, and collapsing them
into a direct ElevenLabs call would have to be undone to add one.

The contract is::

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
                "(set ELEVENLABS_API_KEY)."
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


# ── Local model workers ───────────────────────────────────────────────────────


class _WorkerClient:
    """Persistent subprocess bridge to a local model worker script.

    Subclasses set the class attributes ``_WORKER`` (worker script path,
    monkeypatched to a stub in tests), ``_LABEL`` (log/error prefix), and
    ``_READY_HINT`` (appended to the exited-before-ready error), and may
    override :meth:`_request_extras` to add backend-specific request fields.

    The worker runs under a dedicated venv Python and keeps the model loaded
    across all generation requests.  Communication uses newline-delimited JSON
    on stdin/stdout, mirroring
    :class:`xil_pipeline.XILP002_producer._ChatterboxClient`.

    Originally written for the audioldm2/stableaudio trials removed in #62 and
    restored for MMAudio in #64; the name is model-agnostic because MMAudio is
    flow-matching rather than diffusion.
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



class _MMAudioClient(_WorkerClient):
    """Worker bridge for MMAudio text-to-audio generation."""

    _WORKER = os.path.join(os.path.dirname(__file__), "mmaudio_worker.py")
    _LABEL = "MMAudio"
    _READY_HINT = (
        "Check that venv-mmaudio is set up (git clone hkchengrex/MMAudio, then "
        "pip install -e .) and the weights have downloaded."
    )

    def __init__(self, python_path: str, device: str = "cuda",
                 guidance: float = 4.5, steps: int = 25,
                 negative_prompt: str = "", seed: int | None = None) -> None:
        super().__init__(python_path, device, guidance, steps, negative_prompt)
        self._seed = seed

    def _request_extras(self) -> dict:
        return {"seed": self._seed}


class MMAudioSfxBackend:
    """Local SFX backend backed by MMAudio (text-to-audio mode).

    **The model weights are CC BY-NC 4.0 — non-commercial use only.**  The code
    is MIT, the checkpoints are not.  Audio produced here must not end up in a
    monetised episode.  Construction therefore requires an explicit
    ``accept_noncommercial=True``, every session logs the constraint, and
    generated assets carry it in their ID3 comment (:attr:`asset_comment`) on
    top of the ``.mmaudio`` filename infix that
    :func:`xil_pipeline.sfx_common.shared_sfx_path` already applies.  Between
    the two, an asset stays identifiable even if it is renamed.

    Duration handling is the interesting part.  MMAudio is trained at 8 seconds
    and the project warns that a large deviation degrades quality, but SFX cues
    here are typically shorter.  So generation always runs at the native
    duration and the result is **trimmed afterwards** to the caller's
    ``duration_seconds``.  The trim cannot be left to the mixer: for a
    prompt-generated cue ``duration_seconds`` is the *requested generation
    length* and there is no mix-time clip (that only applies to ``source=``
    cues), so an untrimmed asset would simply play long.
    """

    name = "mmaudio"

    #: Written into every generated asset's ID3 comment.
    asset_comment = (
        "Generated by MMAudio (hkchengrex/MMAudio). Model weights are "
        "CC BY-NC 4.0 — NON-COMMERCIAL USE ONLY."
    )

    #: MMAudio's training duration. Generating here and trimming beats asking
    #: the model for a short clip directly.
    NATIVE_DURATION_S = 8.0

    def __init__(self, client: _MMAudioClient, *,
                 accept_noncommercial: bool = False,
                 native_duration: float = NATIVE_DURATION_S) -> None:
        if not accept_noncommercial:
            raise ValueError(
                "MMAudio weights are CC BY-NC 4.0 (non-commercial only). Pass "
                "--mmaudio-accept-noncommercial to acknowledge that generated "
                "audio must not be used in a monetised production."
            )
        self._client = client
        self._native_duration = native_duration
        logger.warning(
            "MMAudio weights are CC BY-NC 4.0 — NON-COMMERCIAL USE ONLY. "
            "Generated assets are tagged .mmaudio and carry the notice in ID3."
        )

    def generate_to(
        self,
        out_path: str,
        prompt: str,
        duration_seconds: float,
        prompt_influence: float,
    ) -> None:
        """Generate at MMAudio's native duration, then trim to *duration_seconds*.

        ``prompt_influence`` has no direct MMAudio analogue; it is not silently
        dropped — the client's ``cfg_strength`` plays the equivalent role and is
        set from ``--mmaudio-cfg``.
        """
        target = max(0.0, float(duration_seconds or 0.0))
        gen_seconds = max(self._native_duration, target)
        logger.info(
            "   [mmaudio] %r — generating %.1fs (native), trimming to %.1fs",
            prompt, gen_seconds, target or gen_seconds,
        )
        self._client.generate(prompt, out_path, gen_seconds)
        if target and target < gen_seconds:
            _trim_audio_file(out_path, target)

    def close(self) -> None:
        self._client.close()


def _trim_audio_file(path: str, seconds: float) -> None:
    """Trim *path* in place to the first *seconds*, atomically."""
    from pydub import AudioSegment

    clip = AudioSegment.from_file(path)
    target_ms = int(seconds * 1000)
    if len(clip) <= target_ms:
        return
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".mp3")
    os.close(tmp_fd)
    try:
        clip[:target_ms].export(tmp_path, format="mp3")
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise


# ── Factory ───────────────────────────────────────────────────────────────────


def _find_mmaudio_python(explicit: str | None) -> str:
    """Resolve the venv-mmaudio Python via :func:`resolve_venv_python`.

    Exits with an actionable error when it cannot be found — MMAudio installs
    from a git clone rather than PyPI, so the message names that step.
    """
    from xil_pipeline.models import resolve_venv_python

    py = resolve_venv_python("venv-mmaudio", explicit)
    if py is None:
        logger.error(
            "Cannot find the venv-mmaudio Python. Pass --mmaudio-python PATH, "
            "set XIL_CODEROOT to the directory containing venv-mmaudio/, or "
            "create it: python -m venv venv-mmaudio && "
            "git clone https://github.com/hkchengrex/MMAudio && "
            "venv-mmaudio/bin/pip install -e MMAudio"
        )
        sys.exit(1)
    return py


def make_sfx_backend(
    name: str,
    client: ElevenLabs | None = None,
    *,
    mmaudio_python: str | None = None,
    device: str = "cuda",
    mmaudio_cfg: float = 4.5,
    mmaudio_steps: int = 25,
    mmaudio_negative_prompt: str = "",
    mmaudio_seed: int | None = None,
    mmaudio_duration: float = MMAudioSfxBackend.NATIVE_DURATION_S,
    accept_noncommercial: bool = False,
) -> SfxBackend:
    """Construct an :class:`SfxBackend` for the given backend *name*.

    ``"elevenlabs"`` calls the Sound Effects API; ``"mmaudio"`` runs MMAudio
    locally in ``venv-mmaudio`` (**CC BY-NC 4.0 weights — non-commercial only**,
    hence ``accept_noncommercial``).

    The factory is kept rather than inlined because it is the seam a backend
    plugs into — the audioldm2/stableaudio trials were removed through it in #62
    and MMAudio was added through it in #64 without touching call sites.

    Args:
        name: ``"elevenlabs"`` or ``"mmaudio"``.
        client: ElevenLabs client (used only for ``"elevenlabs"``).
        mmaudio_python: Explicit venv-mmaudio interpreter; auto-detected when ``None``.
        device: ``"cuda"`` (default) or ``"cpu"`` for the local backend.
        mmaudio_cfg: Classifier-free guidance strength.
        mmaudio_steps: Flow-matching sampling steps.
        mmaudio_negative_prompt: Optional negative prompt.
        mmaudio_seed: Reproducibility seed (``None`` = nondeterministic).
        mmaudio_duration: Generation length before trimming (default: the 8 s
            training duration).
        accept_noncommercial: Required acknowledgement of the CC BY-NC weights.

    Returns:
        A ready-to-use backend instance.

    Raises:
        ValueError: For an unknown name, or for ``"mmaudio"`` without
            ``accept_noncommercial`` — a stale script must fail loudly rather
            than quietly producing audio that cannot be used commercially.
    """
    if name == "elevenlabs":
        return ElevenLabsSfxBackend(client)
    if name == "mmaudio":
        # Check the licence acknowledgement BEFORE resolving the venv: someone
        # who has not accepted the CC BY-NC terms should be told that, not sent
        # off to install a venv they may then be unable to use.
        if not accept_noncommercial:
            raise ValueError(
                "MMAudio weights are CC BY-NC 4.0 (non-commercial only). Pass "
                "--mmaudio-accept-noncommercial to acknowledge that generated "
                "audio must not be used in a monetised production."
            )
        worker = _MMAudioClient(
            python_path=_find_mmaudio_python(mmaudio_python),
            device=device,
            guidance=mmaudio_cfg,
            steps=mmaudio_steps,
            negative_prompt=mmaudio_negative_prompt,
            seed=mmaudio_seed,
        )
        return MMAudioSfxBackend(
            worker,
            accept_noncommercial=accept_noncommercial,
            native_duration=mmaudio_duration,
        )
    raise ValueError(f"Unknown sfx backend: {name!r}")
