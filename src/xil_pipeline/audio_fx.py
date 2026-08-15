# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ffmpeg-backed audio treatments for dialogue stems.

The rest of the mixing pipeline uses pydub, which offers only single-pole
high/low-pass filters, gain and fades.  Treatments that need compression,
saturation, parametric EQ or delay are built here instead, as ffmpeg filter
graphs.  ffmpeg is already a hard requirement (pydub shells out to it for
decode/encode), so this adds no new dependency.

Audio is round-tripped through ffmpeg as raw PCM over pipes rather than temp
files: there is no lossy intermediate re-encode, and no filesystem churn on
drvfs-mounted checkouts where temp-file I/O is dramatically slower than a pipe.

Every treatment is **length-preserving**.  Some ffmpeg filters (notably
``aecho``) extend their output, and the pipeline derives cue positions from
unfiltered MP3 header durations — a treatment that changed a stem's length
would silently desync the rendered mix from the label/dry-run timeline.
:func:`run_ffmpeg_filter` therefore trims or pads output back to the input
length by default.

Failures degrade rather than abort.  A DAW export runs ffmpeg once per treated
stem across a whole episode; killing a multi-minute render because one
invocation failed is worse than emitting an untreated stem plus a warning.  Set
``XIL_STRICT_FX`` in the environment to raise :class:`AudioFxError` instead.

Module Attributes:
    TREATMENTS: Registry of named treatments, keyed by treatment name.
    STRICT_ENV_VAR: Environment variable that switches failures from
        warn-and-passthrough to raising :class:`AudioFxError`.
"""

import hashlib
import os
import subprocess
from dataclasses import dataclass

from pydub import AudioSegment

from xil_pipeline.log_config import get_logger

logger = get_logger(__name__)

STRICT_ENV_VAR: str = "XIL_STRICT_FX"

# pydub sample_width (bytes) -> ffmpeg raw PCM format.
_RAW_FORMATS: dict[int, str] = {1: "u8", 2: "s16le", 3: "s24le", 4: "s32le"}

# Silence byte used when padding a short ffmpeg result back to input length.
# Unsigned 8-bit PCM centres on 0x80; every signed format centres on 0x00.
_SILENCE_BYTE: dict[int, bytes] = {1: b"\x80", 2: b"\x00", 3: b"\x00", 4: b"\x00"}

# Cap on cached results, to bound memory on long episodes.  FIFO eviction.
_CACHE_MAX_ENTRIES: int = 512

_fx_cache: dict[tuple, bytes] = {}
_warned: set[tuple[str, str]] = set()


class AudioFxError(RuntimeError):
    """Raised when an ffmpeg treatment fails and strict mode is enabled."""


@dataclass(frozen=True)
class Treatment:
    """A named ffmpeg filter graph applied to dialogue stems.

    Attributes:
        name: Registry key, as written in a cast config ``filter`` field.
        graph: ffmpeg ``-filter_complex`` graph producing a ``[out]`` pad.
            May contain ``{rate}`` and ``{layout}`` placeholders, substituted
            with the input segment's sample rate and channel layout.
        summary: One-line description of the sound, for docs and logs.
    """

    name: str
    graph: str
    summary: str


# --- Treatment definitions -------------------------------------------------
#
# Both graphs are written as a single filter_complex producing [out], even when
# the chain is purely serial, so that treatments which introduce a source
# filter (film's noise bed) need no separate code path.

FILM = Treatment(
    name="film",
    summary="Warm, reflective 1990s indie film print — rolled-off top, recessed "
            "presence, tape-style saturation and an audible pink-noise grain.",
    graph=(
        "[0:a]"
        # 90 Hz, not 300: film dialogue keeps its chest weight.  This is the
        # main thing separating `film` from `phone` and `vintage`, both of which
        # thin the voice out.
        "highpass=f=90:poles=2,"
        # 5.5 kHz ceiling.  Still clear of `vintage`'s 5 kHz, and `vintage` also
        # collapses to mono, so the two stay distinguishable.
        "lowpass=f=5500:poles=2,"
        "lowshelf=f=180:g=3,"
        "equalizer=f=350:w=1.2:t=q:g=2,"
        # The presence dip is what stops this reading as a modern podcast mic;
        # it does more for the illusion than the low-pass does.
        "equalizer=f=2800:w=1.6:t=q:g=-6,"
        "highshelf=f=7000:g=-4,"
        "acompressor=threshold=-24dB:ratio=3.5:attack=12:release=280:makeup=2:knee=6,"
        # Drive into the clipper, then recover.  asoftclip's `param` scales the
        # input, so a value below 1 attenuates instead of saturating — drive has
        # to come from an explicit gain stage either side of it.
        "volume=12dB,"
        "asoftclip=type=tanh:param=1:oversample=4,"
        "volume=-12dB,"
        # Output trim, calibrated against real dialogue stems so the treatment
        # lands ~1.5 dB under unity rather than jumping against untreated voices.
        "volume=8.4dB"
        "[v];"
        # Grain sits ~29 dB under dialogue.  An earlier, subtler setting
        # (0.004, ~43 dB down) was inaudible in context and read as no
        # treatment at all.
        "anoisesrc=color=pink:amplitude=0.018:sample_rate={rate},"
        "aformat=channel_layouts={layout}[n];"
        "[v][n]amix=inputs=2:duration=first:normalize=0[out]"
    ),
)

SPEAKERPHONE = Treatment(
    name="speakerphone",
    summary="Narrow-band speakerphone — steep 350 Hz/3.4 kHz skirts, hard AGC, "
            "odd-harmonic crunch and a short tabletop slap.",
    graph=(
        "[0:a]"
        # Cascaded 2-pole sections give 24 dB/oct skirts.  pydub's single-pole
        # filters are why the legacy `phone` treatment reads as merely thin.
        "highpass=f=350:poles=2,"
        "highpass=f=350:poles=2,"
        "lowpass=f=3400:poles=2,"
        "lowpass=f=3400:poles=2,"
        "equalizer=f=700:w=1.0:t=q:g=-4,"
        "equalizer=f=1800:w=1.1:t=q:g=5,"
        "acompressor=threshold=-24dB:ratio=6:attack=5:release=120:makeup=3:knee=2,"
        # See the note in FILM: drive has to be explicit, param=1 is unity.
        # `atan` is harsher and more odd-harmonic than `tanh` — small overdriven amp.
        "volume=8dB,"
        "asoftclip=type=atan:param=1:oversample=4,"
        "volume=-8dB,"
        # 55 ms sits above the Haas fusion threshold, so it reads as a room
        # reflection; a 5-15 ms delay would comb-filter into a flange instead.
        # Fields are in_gain:out_gain:delays:decays — out_gain must stay near
        # unity or the whole treatment loses ~9 dB.
        "aecho=0.9:0.9:55:0.22,"
        # Output trim, calibrated against real dialogue stems (see FILM).
        "volume=13.6dB"
        "[out]"
    ),
)

TREATMENTS: dict[str, Treatment] = {t.name: t for t in (FILM, SPEAKERPHONE)}


def _ffmpeg_binary() -> str:
    """Return the ffmpeg executable pydub is configured to use.

    Returns:
        Path or bare name of the ffmpeg binary.
    """
    converter = getattr(AudioSegment, "converter", None)
    if converter:
        return str(converter)
    try:  # pragma: no cover - only hit on unusual pydub installs
        from pydub.utils import get_encoder_name

        return str(get_encoder_name())
    except Exception:  # pragma: no cover
        return "ffmpeg"


def ffmpeg_available() -> bool:
    """Report whether the configured ffmpeg binary can be executed.

    Returns:
        True if ffmpeg responds to ``-version``, False otherwise.
    """
    try:
        proc = subprocess.run(
            [_ffmpeg_binary(), "-hide_banner", "-version"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _warn_once(label: str, reason: str, message: str, *args) -> None:
    """Log a warning at most once per (treatment, reason) for this process.

    Layer builders iterate hundreds of stems; an undeduplicated warning would
    emit one line per stem and bury the rest of the log.

    Args:
        label: Treatment name, used as part of the dedup key.
        reason: Short failure category, used as part of the dedup key.
        message: ``logger.warning`` format string.
        *args: Format arguments.
    """
    key = (label, reason)
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(message, *args)


def _fail(label: str, reason: str, message: str, *args) -> None:
    """Warn once, or raise :class:`AudioFxError` when strict mode is on.

    Args:
        label: Treatment name.
        reason: Short failure category.
        message: ``logger.warning`` format string.
        *args: Format arguments.

    Raises:
        AudioFxError: If ``XIL_STRICT_FX`` is set in the environment.
    """
    if os.environ.get(STRICT_ENV_VAR):
        raise AudioFxError(message % args if args else message)
    _warn_once(label, reason, message + " — leaving audio untreated", *args)


def _fit_length(raw: bytes, target_len: int, sample_width: int) -> bytes:
    """Trim or pad raw PCM so it is exactly ``target_len`` bytes.

    Args:
        raw: Raw PCM returned by ffmpeg.
        target_len: Byte length of the original segment's raw data.
        sample_width: Bytes per sample, selecting the silence fill byte.

    Returns:
        Raw PCM of exactly ``target_len`` bytes.
    """
    if len(raw) > target_len:
        return raw[:target_len]
    if len(raw) < target_len:
        fill = _SILENCE_BYTE.get(sample_width, b"\x00")
        return raw + fill * (target_len - len(raw))
    return raw


def run_ffmpeg_filter(
    segment: AudioSegment,
    graph: str,
    *,
    preserve_length: bool = True,
    label: str = "",
) -> AudioSegment:
    """Push a segment through an ffmpeg ``-filter_complex`` graph.

    Args:
        segment: Input audio.
        graph: Filter graph producing an ``[out]`` pad.  ``{rate}`` and
            ``{layout}`` placeholders are substituted from ``segment``.
        preserve_length: Trim or pad the result back to the input length.
            Leave enabled unless the caller genuinely wants a length change;
            the pipeline's cue timeline assumes stems keep their duration.
        label: Treatment name, used in log messages and cache keys.

    Returns:
        The treated segment, or ``segment`` unchanged if ffmpeg is unavailable
        or the invocation fails and strict mode is off.

    Raises:
        AudioFxError: On failure when ``XIL_STRICT_FX`` is set.
    """
    raw_fmt = _RAW_FORMATS.get(segment.sample_width)
    if raw_fmt is None:
        _fail(
            label,
            "sample_width",
            "Unsupported sample width %d bytes for treatment %r",
            segment.sample_width,
            label,
        )
        return segment

    resolved = graph.format(
        rate=segment.frame_rate,
        layout="mono" if segment.channels == 1 else "stereo",
    )

    source = segment.raw_data
    cache_key = (
        hashlib.blake2b(source, digest_size=16).digest(),
        segment.frame_rate,
        segment.channels,
        segment.sample_width,
        resolved,
        preserve_length,
    )
    cached = _fx_cache.get(cache_key)
    if cached is not None:
        return segment._spawn(cached)

    cmd = [
        _ffmpeg_binary(),
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", raw_fmt,
        "-ar", str(segment.frame_rate),
        "-ac", str(segment.channels),
        "-i", "pipe:0",
        "-filter_complex", resolved,
        "-map", "[out]",
        "-vn", "-sn", "-dn",
        "-f", raw_fmt,
        "-ar", str(segment.frame_rate),
        "-ac", str(segment.channels),
        "pipe:1",
    ]

    try:
        proc = subprocess.run(cmd, input=source, capture_output=True)
    except FileNotFoundError:
        _fail(label, "missing", "ffmpeg not found for treatment %r", label)
        return segment
    except OSError as exc:
        _fail(label, "oserror", "ffmpeg failed for treatment %r: %s", label, exc)
        return segment

    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        _fail(
            label,
            "returncode",
            "ffmpeg treatment %r failed (rc=%s): %s",
            label,
            proc.returncode,
            detail[-1] if detail else "no stderr output",
        )
        return segment

    out = proc.stdout
    if preserve_length:
        out = _fit_length(out, len(source), segment.sample_width)

    if len(_fx_cache) >= _CACHE_MAX_ENTRIES:
        # FIFO eviction: dicts preserve insertion order.
        del _fx_cache[next(iter(_fx_cache))]
    _fx_cache[cache_key] = out

    return segment._spawn(out)


def apply_treatment(segment: AudioSegment, name: str) -> AudioSegment:
    """Apply a named treatment from :data:`TREATMENTS` to a segment.

    Args:
        segment: Input audio.
        name: Treatment name, e.g. ``"film"`` or ``"speakerphone"``.

    Returns:
        The treated segment, or ``segment`` unchanged if the name is unknown
        or the ffmpeg invocation fails outside strict mode.
    """
    treatment = TREATMENTS.get(name)
    if treatment is None:
        _warn_once(
            name,
            "unknown",
            "Unknown ffmpeg treatment %r — known treatments: %s",
            name,
            ", ".join(sorted(TREATMENTS)),
        )
        return segment
    return run_ffmpeg_filter(segment, treatment.graph, label=treatment.name)


def clear_cache() -> None:
    """Empty the treatment result cache.

    Intended for tests; the cache is otherwise self-limiting.
    """
    _fx_cache.clear()
