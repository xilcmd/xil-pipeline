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
import os
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


# ── Factory ───────────────────────────────────────────────────────────────────


def make_sfx_backend(name: str, client: ElevenLabs | None = None) -> SfxBackend:
    """Construct an :class:`SfxBackend` for the given backend *name*.

    Only ``"elevenlabs"`` remains. The local diffusion backends (audioldm2,
    stableaudio) were removed in #62 after both trials produced unusable audio.
    The factory is kept rather than inlined because it is the seam a future
    backend plugs into, and ``--sfx-backend`` still resolves through it.

    Args:
        name: Backend name; only ``"elevenlabs"`` is valid.
        client: ElevenLabs client.

    Returns:
        A ready-to-use backend instance.

    Raises:
        ValueError: For any other name — including the removed backends, so a
            stale script fails loudly instead of silently generating with a
            different model than it asked for.
    """
    if name == "elevenlabs":
        return ElevenLabsSfxBackend(client)
    raise ValueError(f"Unknown sfx backend: {name!r}")
