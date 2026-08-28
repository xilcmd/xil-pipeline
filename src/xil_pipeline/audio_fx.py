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
        codec: Optional ffmpeg encoder to round-trip the filtered audio through,
            for treatments whose character comes from real codec artifacts
            rather than from EQ alone.  ``None`` skips the round-trip entirely,
            which is the behaviour every filter-only treatment relies on.
        container: Muxer for that round-trip.  Codec and container are not
            freely interchangeable — ``libgsm`` needs the raw ``gsm`` format and
            errors out inside a WAV container.
        codec_rate: Sample rate to encode at.  This is where a telephone
            treatment gets its hard ceiling: encoding at 8 kHz brickwalls at
            4 kHz far more steeply than any practical filter cascade.
    """

    name: str
    graph: str
    summary: str
    codec: str | None = None
    container: str | None = None
    codec_rate: int | None = None


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

PHONE = Treatment(
    name="phone",
    summary="Mobile call — steep 300 Hz/3.4 kHz skirts, earpiece presence lift, "
            "hard AGC and genuine GSM codec grit, sitting just under the room.",
    graph=(
        "[0:a]"
        # The legacy pydub implementation was a single-pole tilt: only ~11 dB
        # down at 80 Hz and ~9 dB at 8 kHz, which reads as a slightly muffled
        # voice rather than a phone.  Cascaded 2-pole sections give 24 dB/oct.
        "highpass=f=300:poles=2,"
        "highpass=f=300:poles=2,"
        # The 8 kHz codec rate already brickwalls at 4 kHz, so this pair looks
        # redundant — it is not.  It is what makes the treatment degrade
        # gracefully: with no libgsm, the filter-only result is still a proper
        # band-limit instead of a half-finished effect.
        "lowpass=f=3400:poles=2,"
        "lowpass=f=3400:poles=2,"
        "equalizer=f=500:w=1.0:t=q:g=-4,"
        # Earpiece presence.  Lower and wider than speakerphone's 1800 Hz honk,
        # which is a small loudspeaker in a room rather than against an ear.
        "equalizer=f=1700:w=1.2:t=q:g=6,"
        "acompressor=threshold=-22dB:ratio=8:attack=3:release=90:makeup=3:knee=2,"
        # See the note in FILM: asoftclip's param scales the input, so drive has
        # to come from the volume stages either side.
        "volume=6dB,"
        "asoftclip=type=atan:param=1:oversample=4,"
        "volume=-6dB,"
        # Output trim, calibrated across 20 real dialogue stems: band-limiting
        # speech costs ~12 dB, so this lands a caller ~1.4 dB UNDER the person
        # in the room.  The legacy +5 dB made the distant voice the loudest
        # thing in the scene, which fought the illusion.
        "volume=10dB"
        "[out]"
    ),
    # GSM 06.10 is the 2G mobile codec; a round-trip through it is the real
    # artifact rather than an imitation of one.  The raw `gsm` muxer is not
    # optional — the codec is rejected inside a WAV container.
    codec="libgsm",
    container="gsm",
    codec_rate=8000,
)

TREATMENTS: dict[str, Treatment] = {t.name: t for t in (FILM, SPEAKERPHONE, PHONE)}


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


def _fail(label: str, reason: str, message: str, *args,
          consequence: str = "leaving audio untreated") -> None:
    """Warn once, or raise :class:`AudioFxError` when strict mode is on.

    Args:
        label: Treatment name.
        reason: Short failure category.
        message: ``logger.warning`` format string.
        *args: Format arguments.
        consequence: What the caller falls back to, appended to the warning.
            The default suits a whole-treatment failure; a partial failure such
            as a missing codec should say what it actually kept, or the log
            claims the stem is dry when it is not.

    Raises:
        AudioFxError: If ``XIL_STRICT_FX`` is set in the environment.
    """
    if os.environ.get(STRICT_ENV_VAR):
        raise AudioFxError(message % args if args else message)
    _warn_once(label, reason, message + " — " + consequence, *args)


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


_CODEC_FALLBACK = "keeping the filtered audio without codec character"


def _codec_round_trip(
    raw: bytes,
    segment: AudioSegment,
    raw_fmt: str,
    label: str,
    *,
    codec: str,
    container: str | None,
    codec_rate: int | None,
) -> bytes:
    """Encode raw PCM through a lossy codec and decode it straight back.

    Some treatments are defined by an artifact no filter reproduces honestly —
    a mobile call is a GSM 06.10 round-trip, not an EQ curve that resembles one.
    Encoding also band-limits at the codec's Nyquist far more steeply than a
    practical filter cascade, which is why the rate matters as much as the codec.

    Two ffmpeg invocations, piped: raw PCM in, encoded bytes out, then decoded
    back to raw PCM at the segment's own rate and channel count.

    Failure returns *raw* untouched, so the caller keeps the filtered audio and
    loses only the codec character.  Under ``XIL_STRICT_FX`` the failure raises
    like any other, via :func:`_fail`.

    Args:
        raw: Filtered raw PCM from the main pass.
        segment: The original segment, for rate/channel/width.
        raw_fmt: ffmpeg raw PCM format matching the segment's sample width.
        label: Treatment name, for log messages.
        codec: ffmpeg encoder name.
        container: Muxer to wrap it in; codec and container must be compatible.
        codec_rate: Sample rate to encode at, or ``None`` for the input's.

    Returns:
        Decoded raw PCM, or *raw* unchanged when the round-trip fails.
    """
    binary = _ffmpeg_binary()
    rate = str(segment.frame_rate)
    channels = str(segment.channels)
    encode = [
        binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", raw_fmt, "-ar", rate, "-ac", channels, "-i", "pipe:0",
        "-ar", str(codec_rate or segment.frame_rate), "-ac", "1",
        "-c:a", codec, "-f", container or codec, "pipe:1",
    ]
    decode = [
        binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", container or codec, "-i", "pipe:0",
        "-vn", "-sn", "-dn",
        "-f", raw_fmt, "-ar", rate, "-ac", channels, "pipe:1",
    ]

    try:
        enc = subprocess.run(encode, input=raw, capture_output=True)
        if enc.returncode != 0 or not enc.stdout:
            raise _CodecStageError("encode", enc.stderr)
        dec = subprocess.run(decode, input=enc.stdout, capture_output=True)
        if dec.returncode != 0 or not dec.stdout:
            raise _CodecStageError("decode", dec.stderr)
    except _CodecStageError as exc:
        _fail(
            label, "codec",
            "codec %s unavailable for treatment %r (%s): %s",
            codec, label, exc.stage, exc.detail,
            consequence=_CODEC_FALLBACK,
        )
        return raw
    except FileNotFoundError:
        _fail(label, "codec", "ffmpeg not found for codec stage of %r", label,
              consequence=_CODEC_FALLBACK)
        return raw
    except OSError as exc:
        _fail(label, "codec", "codec stage failed for treatment %r: %s", label, exc,
              consequence=_CODEC_FALLBACK)
        return raw

    return dec.stdout


class _CodecStageError(Exception):
    """Internal: one stage of a codec round-trip returned non-zero."""

    def __init__(self, stage: str, stderr: bytes) -> None:
        self.stage = stage
        lines = stderr.decode("utf-8", "replace").strip().splitlines()
        self.detail = lines[-1] if lines else "no stderr output"
        super().__init__(f"{stage}: {self.detail}")


def run_ffmpeg_filter(
    segment: AudioSegment,
    graph: str,
    *,
    preserve_length: bool = True,
    label: str = "",
    codec: str | None = None,
    container: str | None = None,
    codec_rate: int | None = None,
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
        codec: Optional encoder to round-trip the filtered audio through, for
            treatments whose character is a real codec artifact.
        container: Muxer for that round-trip; required alongside *codec*.
        codec_rate: Sample rate to encode at, or ``None`` to keep the input's.

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
        codec,
        container,
        codec_rate,
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
    if codec:
        # Degrades to the filter-only result, not to the dry input: losing the
        # codec should cost the grit, not the whole treatment.
        out = _codec_round_trip(
            out, segment, raw_fmt, label,
            codec=codec, container=container, codec_rate=codec_rate,
        )
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
    return run_ffmpeg_filter(
        segment,
        treatment.graph,
        label=treatment.name,
        codec=treatment.codec,
        container=treatment.container,
        codec_rate=treatment.codec_rate,
    )


def clear_cache() -> None:
    """Empty the treatment result cache.

    Intended for tests; the cache is otherwise self-limiting.
    """
    _fx_cache.clear()
