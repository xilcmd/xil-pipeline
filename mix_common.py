"""Shared multi-track mixing utilities for the audio pipeline.

Provides timeline construction and per-layer audio building used by
XILP003 (automated two-pass mix) and XILP005 (DAW layer export).
Both stages classify stems by direction_type from the parsed script
JSON, then build foreground (dialogue/SFX) and background (ambience/
music) layers independently before combining.

Module Attributes:
    BACKGROUND_DIRECTION_TYPES: direction_type values routed to the
        background layer rather than the foreground timeline.
    AMBIENCE_LEVEL_DB: Default dB reduction applied to ambience overlays
        in the automated mix (Option A). 0 for DAW export (Option C).
    MUSIC_LEVEL_DB: Default dB reduction applied to music overlays
        in the automated mix. 0 for DAW export.
"""

import glob
import json
import math
import os
import re
from dataclasses import dataclass, field

from pydub import AudioSegment

try:
    from mutagen.mp3 import MP3 as _MutagenMP3
except ImportError:  # pragma: no cover
    _MutagenMP3 = None

# Background direction types — excluded from the foreground timeline,
# overlaid at their cue positions in a separate background pass.
BACKGROUND_DIRECTION_TYPES: frozenset[str] = frozenset({"AMBIENCE", "MUSIC"})

# Default level adjustments for the automated mixed master (Option A).
# Use 0 for DAW export layers so the producer controls levels in-DAW.
AMBIENCE_LEVEL_DB: float = -10.0
MUSIC_LEVEL_DB: float = -6.0


@dataclass
class StemPlan:
    """Resolved metadata for a single audio stem file.

    Attributes:
        seq: Sequence number extracted from the stem filename.
        filepath: Absolute or relative path to the MP3 stem file.
        direction_type: Parsed direction category for this entry
            (``"SFX"``, ``"MUSIC"``, ``"AMBIENCE"``, ``"BEAT"``),
            or ``None`` for dialogue stems.
        entry_type: Parsed entry classification (``"dialogue"``,
            ``"direction"``, etc.), or ``None`` if not in index.
        foreground_override: When ``True``, forces the stem into the
            foreground timeline even if ``direction_type`` would normally
            route it to the background (e.g. preamble intro music that
            must play sequentially, not as an overlay).
    """

    seq: int
    filepath: str
    direction_type: str | None
    entry_type: str | None
    text: str | None = None
    foreground_override: bool = False
    volume_percentage: float | None = None
    ramp_in_seconds: float | None = None
    ramp_out_seconds: float | None = None
    play_duration: float | None = None
    pre_trimmed: bool = False
    loop: bool = True

    @property
    def is_background(self) -> bool:
        """True if this stem belongs in the background layer."""
        if self.foreground_override:
            return False
        return self.direction_type in BACKGROUND_DIRECTION_TYPES


def extract_seq(filepath: str) -> int:
    """Extract the sequence number from a stem filename.

    Positive stems are named ``{seq:03d}_{section}[-{scene}]_{speaker}.mp3``.
    Negative (preamble) stems use an ``n`` prefix: ``n{abs(seq):03d}_...mp3``.

    Args:
        filepath: Path like ``stems/S01E01/003_cold-open_adam.mp3`` or
            ``stems/S02E03/n002_preamble_tina.mp3``.

    Returns:
        Integer sequence number (e.g. ``"003"`` → ``3``, ``"n002"`` → ``-2``).
    """
    basename = os.path.splitext(os.path.basename(filepath))[0]
    prefix = basename.split("_")[0]
    if prefix.startswith("n") and prefix[1:].isdigit():
        return -int(prefix[1:])
    return int(prefix)


def load_entries_index(parsed_path: str) -> dict[int, dict]:
    """Load a parsed script JSON and return a ``{seq: entry}`` index.

    Args:
        parsed_path: Path to the parsed script JSON produced by XILP001.

    Returns:
        Dict mapping each sequence number to its full entry dict.
    """
    with open(parsed_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {entry["seq"]: entry for entry in data["entries"]}


def _volume_pct_to_db(volume_percentage: float) -> float:
    """Convert a volume percentage to a dB offset.

    Args:
        volume_percentage: Volume as a percentage (100 = unity gain).

    Returns:
        dB offset: ``20 * log10(volume_percentage / 100)``.
        Returns ``-inf`` (silence) for zero or negative values.
    """
    if volume_percentage <= 0:
        return -math.inf
    return 20.0 * math.log10(volume_percentage / 100.0)


def _apply_clip_effects(
    clip: AudioSegment,
    volume_percentage: float | None,
    ramp_in_ms: int,
    ramp_out_ms: int,
    level_db: float = 0,
) -> AudioSegment:
    """Apply volume percentage (as dB), then level_db offset, then ramp in/out.

    Args:
        clip: Input audio segment.
        volume_percentage: Volume as a percentage (100 = unity); ``None`` skips
            volume adjustment.
        ramp_in_ms: Fade-in duration in milliseconds (0 = no fade).
        ramp_out_ms: Fade-out duration in milliseconds (0 = no fade).
        level_db: Additional dB offset applied after volume percentage.

    Returns:
        Processed audio segment.
    """
    if volume_percentage is not None:
        clip = clip + _volume_pct_to_db(volume_percentage)
    if level_db != 0:
        clip = clip + level_db
    if ramp_in_ms > 0:
        clip = clip.fade_in(ramp_in_ms)
    if ramp_out_ms > 0:
        clip = clip.fade_out(ramp_out_ms)
    return clip


def _normalize_effect_key(text: str) -> str:
    """Normalize em-dashes to plain hyphens for effect key matching."""
    return re.sub(r"\s*\u2014\s*", " - ", text)


def _find_effect_entry(sfx_config, text: str):
    """Look up an effect entry by text, with em-dash normalization fallback.

    Tries exact match first; if that fails, normalizes both sides
    (em-dash → hyphen) and scans.  Returns the matched entry or ``None``.
    """
    if not text:
        return None
    entry = sfx_config.effects.get(text)
    if entry is not None:
        return entry
    norm = _normalize_effect_key(text)
    for k, v in sfx_config.effects.items():
        if _normalize_effect_key(k) == norm:
            return v
    return None


def _resolve_audio_params(
    plan: "StemPlan",
    sfx_config,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Resolve volume/ramp/play_duration values from per-effect override or category defaults.

    Args:
        plan: The stem plan whose direction_type determines the category prefix.
        sfx_config: An :class:`~models.SfxConfiguration` instance, or ``None``.

    Returns:
        Tuple of ``(volume_percentage, ramp_in_seconds, ramp_out_seconds, play_duration)``,
        each ``None`` if no value is configured. ``play_duration`` is only resolved
        for MUSIC entries (not AMBIENCE).
    """
    if sfx_config is None:
        return None, None, None, None
    defaults = sfx_config.defaults
    entry = _find_effect_entry(sfx_config, plan.text or "")
    # Determine category prefix for defaults lookup
    prefix_map = {
        "MUSIC": "music",
        "AMBIENCE": "ambience",
        "SFX": "sfx",
        "BEAT": "sfx",
    }
    prefix = prefix_map.get(plan.direction_type)
    if prefix is None:
        # Dialogue and other non-effect types — only per-effect volume applies
        vol = entry.volume_percentage if entry and entry.volume_percentage is not None else None
        return vol, None, None, None
    vol = (
        entry.volume_percentage
        if entry and entry.volume_percentage is not None
        else defaults.get(f"{prefix}_volume_percentage")
    )
    ri = (
        entry.ramp_in_seconds
        if entry and entry.ramp_in_seconds is not None
        else defaults.get(f"{prefix}_ramp_in_seconds")
    )
    ro = (
        entry.ramp_out_seconds
        if entry and entry.ramp_out_seconds is not None
        else defaults.get(f"{prefix}_ramp_out_seconds")
    )
    # play_duration applies to MUSIC only (looped ambience has no meaningful truncation)
    pd = None
    if plan.direction_type == "MUSIC" and entry and entry.play_duration is not None:
        pd = entry.play_duration
    return vol, ri, ro, pd


def collect_stem_plans(
    stems_dir: str, entries_index: dict[int, dict], sfx_config=None
) -> list[StemPlan]:
    """Collect and classify all MP3 stems in a stems directory.

    Uses the entries index to look up each stem's ``direction_type``
    and ``entry_type`` by sequence number. Stems whose seq is not in
    the index are treated as foreground (dialogue) to ensure they are
    always included in the output.

    Args:
        stems_dir: Directory containing episode stem MP3 files.
        entries_index: ``{seq: entry}`` mapping from
            :func:`load_entries_index`.
        sfx_config: Optional :class:`~models.SfxConfiguration`; when provided,
            resolves per-effect or category-default volume/ramp values into
            MUSIC and AMBIENCE plans.

    Returns:
        List of :class:`StemPlan` instances sorted by sequence number.
    """
    stem_files = sorted(glob.glob(os.path.join(stems_dir, "*.mp3")))
    plans = []
    for filepath in stem_files:
        try:
            seq = extract_seq(filepath)
        except ValueError:
            continue  # preamble_*.mp3 and other non-seq files handled separately
        entry = entries_index.get(seq, {})
        entry_type = entry.get("type")

        # Cross-check filename suffix vs parsed entry type to catch stale stems.
        # SFX/direction stems always end with `_sfx`; dialogue stems end with a
        # speaker key (never `_sfx`).
        basename = os.path.splitext(os.path.basename(filepath))[0]
        suffix = basename.rsplit("_", 1)[-1]
        is_sfx_stem = suffix == "sfx"

        # Header entries (section_header, scene_header) never have stems
        if entry_type not in ("dialogue", "direction", None):
            print(f" [W] Stale stem skipped: {os.path.basename(filepath)} "
                  f"(seq {seq} is now a {entry_type} entry)")
            continue
        if is_sfx_stem and entry_type == "dialogue":
            print(f" [W] Stale stem skipped: {os.path.basename(filepath)} "
                  f"(seq {seq} is now a dialogue entry)")
            continue
        if not is_sfx_stem and entry_type == "direction":
            print(f" [W] Stale stem skipped: {os.path.basename(filepath)} "
                  f"(seq {seq} is now a direction entry)")
            continue
        # Check speaker suffix matches parsed speaker for dialogue stems
        if entry_type == "dialogue" and entry.get("speaker"):
            expected_suffix = f"_{entry['speaker']}"
            if not basename.endswith(expected_suffix):
                print(f" [W] Stale stem skipped: {os.path.basename(filepath)} "
                      f"(seq {seq} speaker is now {entry['speaker']})")
                continue

        plan = StemPlan(
            seq=seq,
            filepath=filepath,
            direction_type=entry.get("direction_type"),
            entry_type=entry_type,
            text=entry.get("text"),
        )
        # Preamble intro music (seq < 0) and postamble outro music both play
        # sequentially in the foreground rather than as background overlays.
        if plan.direction_type == "MUSIC" and (
            entry.get("section") in ("preamble", "postamble") or plan.seq < 0
        ):
            plan.foreground_override = True
        vol, ri, ro, pd = _resolve_audio_params(plan, sfx_config)
        plan.volume_percentage = vol
        plan.ramp_in_seconds = ri
        plan.ramp_out_seconds = ro

        plan.play_duration = pd
        # Source-based stems are pre-trimmed by XILP002; don't trim again at mix time
        src_entry = _find_effect_entry(sfx_config, plan.text) if sfx_config else None
        if src_entry is not None:
            if src_entry.source is not None and pd is not None:
                plan.pre_trimmed = True
            if src_entry.loop is False:
                plan.loop = False
        plans.append(plan)

    # Deduplicate: if multiple stems share the same seq (e.g. old and new
    # section names for an SFX entry), keep only the first one and warn.
    deduped = []
    seen_plan_seqs: set[int] = set()
    for plan in plans:
        if plan.seq in seen_plan_seqs:
            print(f" [W] Duplicate stem skipped: {os.path.basename(plan.filepath)} "
                  f"(seq {plan.seq} already loaded)")
            continue
        seen_plan_seqs.add(plan.seq)
        deduped.append(plan)
    plans = deduped

    # Inject synthetic stop markers for ambience-end directives in the index.
    # "AMBIENCE: STOP" and "AMBIENCE: * FADES OUT" have no stem file on disk
    # but must appear in the timeline so build_ambience_layer can use their
    # cue position as the loop end boundary.
    seen_seqs = {p.seq for p in plans}
    for seq, entry in entries_index.items():
        if seq in seen_seqs:
            continue
        text = entry.get("text", "")
        if entry.get("direction_type") == "AMBIENCE" and (
            text == "AMBIENCE: STOP" or text.endswith("FADES OUT")
        ):
            plans.append(StemPlan(
                seq=seq,
                filepath="",  # sentinel: no audio — skip in layer builders
                direction_type="AMBIENCE",
                entry_type=entry.get("type"),
                text=text,
            ))

    return plans


def apply_phone_filter(segment: AudioSegment) -> AudioSegment:
    """Apply a phone-speaker audio filter to an audio segment.

    Cuts frequencies below 300 Hz and above 3000 Hz, then boosts
    volume by 5 dB to simulate a telephone speaker.

    Args:
        segment: Input audio segment to filter.

    Returns:
        Filtered audio segment.
    """
    return segment.high_pass_filter(300).low_pass_filter(3000) + 5


def build_foreground(
    stem_plans: list[StemPlan],
    cast_config: dict,
    apply_effects_fn=None,
    gap_ms: int = 600,
) -> tuple[AudioSegment, dict[int, int]]:
    """Build the foreground audio track and a full-episode timeline.

    Iterates stems in sequence order. Foreground stems (dialogue, SFX,
    BEAT) are concatenated with silence gaps and their positions are
    recorded in the timeline. Background stems (AMBIENCE, MUSIC) are
    recorded in the timeline at the current foreground cursor position
    but do not advance it — they are overlaid at that cue point in a
    separate background pass.

    Args:
        stem_plans: Classified stem list from :func:`collect_stem_plans`.
        cast_config: ``{speaker_key: {"pan": float, "filter": bool}}``
            for per-speaker audio effects.
        apply_effects_fn: Optional callable applied to speakers with
            ``filter=True`` (typically :func:`apply_phone_filter`).
            Pass ``None`` to skip phone filtering.
        gap_ms: Silence inserted between foreground stems in ms.

    Returns:
        Tuple of ``(foreground_audio, timeline)`` where ``timeline``
        maps sequence numbers to millisecond offsets within the
        foreground track.
    """
    foreground = AudioSegment.empty()
    timeline: dict[int, int] = {}
    current_ms = 0

    for plan in sorted(stem_plans, key=lambda p: p.seq):
        # Record cue position for ALL stems (both fg and bg).
        # Background stems don't advance current_ms — they overlay.
        timeline[plan.seq] = current_ms

        if plan.is_background:
            continue

        segment = AudioSegment.from_file(plan.filepath)

        # Apply volume_percentage to SFX/BEAT stems in the foreground.
        if plan.direction_type in ("SFX", "BEAT") and plan.volume_percentage is not None:
            segment = segment + _volume_pct_to_db(plan.volume_percentage)

        # Apply per-speaker effects to dialogue stems.
        basename = os.path.splitext(os.path.basename(plan.filepath))[0]
        speaker = basename.rsplit("_", 1)[-1]
        if speaker in cast_config:
            if cast_config[speaker].get("filter") and apply_effects_fn:
                segment = apply_effects_fn(segment)
            segment = segment.pan(cast_config[speaker].get("pan", 0.0))

        foreground += segment + AudioSegment.silent(duration=gap_ms)
        current_ms += len(segment) + gap_ms

    return foreground, timeline


def _loop_clip(clip: AudioSegment, duration_ms: int) -> AudioSegment:
    """Loop an audio clip to fill exactly ``duration_ms`` milliseconds.

    Args:
        clip: Source audio clip to repeat.
        duration_ms: Target duration in milliseconds.

    Returns:
        Audio segment of exactly ``duration_ms`` length, or a silent
        segment if ``clip`` is empty or ``duration_ms`` is zero.
    """
    if len(clip) == 0 or duration_ms <= 0:
        return AudioSegment.silent(duration=max(0, duration_ms))
    repeats = -(-duration_ms // len(clip))  # ceiling division
    return (clip * repeats)[:duration_ms]


def build_ambience_layer(
    stem_plans: list[StemPlan],
    timeline: dict[int, int],
    total_ms: int,
    level_db: float = AMBIENCE_LEVEL_DB,
) -> AudioSegment:
    """Build the ambience background layer.

    Each AMBIENCE stem is looped from its cue point to the start of
    the next background cue (AMBIENCE or MUSIC) or the end of the
    track, whichever comes first. The ``level_db`` parameter controls
    ducking; use ``0`` for DAW layer export so the producer controls
    levels in-DAW.

    Args:
        stem_plans: Classified stem list from :func:`collect_stem_plans`.
        timeline: Cue-point timestamps from :func:`build_foreground`.
        total_ms: Total foreground track length in milliseconds.
        level_db: Volume adjustment applied to the clip before looping.
            Negative values duck the ambience below dialogue.

    Returns:
        Tuple of ``(layer, labels)`` where *layer* is a full-length
        :class:`~pydub.AudioSegment` with ambience looped at each cue
        point, and *labels* is a list of ``(start_sec, end_sec, text)``
        tuples spanning each looped region.
    """
    layer = AudioSegment.silent(duration=total_ms)
    labels: list[tuple[float, float, str]] = []
    ambience_plans = sorted(
        (p for p in stem_plans if p.direction_type == "AMBIENCE"),
        key=lambda p: p.seq,
    )
    if not ambience_plans:
        return layer, labels

    # All background cue ms values (AMBIENCE + MUSIC) sorted by position.
    bg_cues: list[tuple[int, int]] = sorted(
        (
            (timeline.get(p.seq, 0), p.seq)
            for p in stem_plans
            if p.is_background
        ),
        key=lambda t: t[0],
    )

    for plan in ambience_plans:
        if not plan.filepath:  # AMBIENCE: STOP marker — boundary only, no audio
            continue
        start_ms = timeline.get(plan.seq, 0)
        if start_ms >= total_ms:
            continue

        # End at the next background cue after this one, or track end.
        end_ms = total_ms
        for cue_ms, cue_seq in bg_cues:
            if cue_seq > plan.seq and cue_ms > start_ms:
                end_ms = min(cue_ms, total_ms)
                break

        duration_needed = end_ms - start_ms
        if duration_needed <= 0:
            continue

        try:
            clip = AudioSegment.from_file(plan.filepath)
        except Exception as exc:
            print(f" [W] Skipping corrupt ambience stem: {plan.filepath} ({exc})")
            continue
        ramp_in_ms = int((plan.ramp_in_seconds or 0) * 1000)
        ramp_out_ms = int((plan.ramp_out_seconds or 0) * 1000)
        looped = _loop_clip(clip, duration_needed) if plan.loop else clip[:duration_needed]
        looped = _apply_clip_effects(
            looped, plan.volume_percentage, ramp_in_ms, ramp_out_ms, level_db
        )
        layer = layer.overlay(looped, position=start_ms)
        label_text = plan.text or plan.direction_type or "AMBIENCE"
        labels.append((
            start_ms / 1000.0, end_ms / 1000.0, label_text,
            plan.ramp_in_seconds, plan.ramp_out_seconds,
            None, None, plan.volume_percentage, plan.seq,
        ))

    return layer, labels


def build_music_layer(
    stem_plans: list[StemPlan],
    timeline: dict[int, int],
    total_ms: int,
    level_db: float = MUSIC_LEVEL_DB,
) -> AudioSegment:
    """Build the music/sting background layer.

    Each MUSIC stem is overlaid at its cue point without looping.
    Use ``level_db=0`` for DAW layer export so levels are set in-DAW.

    Args:
        stem_plans: Classified stem list from :func:`collect_stem_plans`.
        timeline: Cue-point timestamps from :func:`build_foreground`.
        total_ms: Total foreground track length in milliseconds.
        level_db: Volume adjustment applied before overlaying.

    Returns:
        Tuple of ``(layer, labels)`` where *layer* is a full-length
        :class:`~pydub.AudioSegment` with music stings overlaid at
        their cue positions, and *labels* is a list of
        ``(start_sec, end_sec, text)`` tuples for each sting.
    """
    layer = AudioSegment.silent(duration=total_ms)
    labels: list[tuple[float, float, str]] = []
    for plan in sorted(stem_plans, key=lambda p: p.seq):
        if plan.direction_type != "MUSIC":
            continue
        start_ms = timeline.get(plan.seq, 0)
        if start_ms >= total_ms:
            continue
        clip = AudioSegment.from_file(plan.filepath)
        if plan.play_duration is not None and not plan.pre_trimmed:
            clip = clip[:max(1, int(len(clip) * plan.play_duration / 100.0))]
        ramp_in_ms = int((plan.ramp_in_seconds or 0) * 1000)
        ramp_out_ms = int((plan.ramp_out_seconds or 0) * 1000)
        clip = _apply_clip_effects(
            clip, plan.volume_percentage, ramp_in_ms, ramp_out_ms, level_db
        )
        layer = layer.overlay(clip, position=start_ms)
        label_text = plan.text or plan.direction_type or "MUSIC"
        labels.append((
            start_ms / 1000.0, (start_ms + len(clip)) / 1000.0, label_text,
            plan.ramp_in_seconds, plan.ramp_out_seconds, plan.play_duration,
            None, plan.volume_percentage, plan.seq,
        ))
    return layer, labels


def build_dialogue_layer(
    stem_plans: list[StemPlan],
    timeline: dict[int, int],
    total_ms: int,
    cast_config: dict,
    apply_effects_fn=None,
) -> tuple:
    """Build an isolated dialogue layer for DAW export.

    Places only dialogue stems (``entry_type == "dialogue"``) at their
    foreground timeline positions in a full-length silent segment.
    Phone filter and pan effects are applied per speaker as configured.

    Args:
        stem_plans: Classified stem list from :func:`collect_stem_plans`.
        timeline: Cue-point timestamps from :func:`build_foreground`.
        total_ms: Total track length in milliseconds.
        cast_config: Per-speaker audio settings.
        apply_effects_fn: Optional phone filter callable.

    Returns:
        Tuple of ``(layer, labels)`` where *layer* is a full-length
        :class:`~pydub.AudioSegment` with dialogue stems at their
        timeline positions, and *labels* is a list of
        ``(start_sec, end_sec, speaker)`` tuples for Audacity label export.
    """
    layer = AudioSegment.silent(duration=total_ms)
    labels: list[tuple[float, float, str]] = []
    for plan in sorted(stem_plans, key=lambda p: p.seq):
        if plan.entry_type != "dialogue":
            continue
        start_ms = timeline.get(plan.seq, 0)
        segment = AudioSegment.from_file(plan.filepath)
        basename = os.path.splitext(os.path.basename(plan.filepath))[0]
        speaker = basename.rsplit("_", 1)[-1]
        if speaker in cast_config:
            if cast_config[speaker].get("filter") and apply_effects_fn:
                segment = apply_effects_fn(segment)
            segment = segment.pan(cast_config[speaker].get("pan", 0.0))
        end_ms = start_ms + len(segment)
        labels.append((start_ms / 1000.0, end_ms / 1000.0, speaker, None, None, None, None, None, plan.seq))
        layer = layer.overlay(segment, position=start_ms)
    return layer, labels



def _mp3_duration_ms(filepath: str) -> int:
    """Return the duration of an MP3 file in milliseconds without decoding audio.

    Uses mutagen for a fast header-only read.  Falls back to pydub if
    mutagen is unavailable.

    Args:
        filepath: Path to the MP3 file.

    Returns:
        Duration in milliseconds.
    """
    if _MutagenMP3 is not None:
        info = _MutagenMP3(filepath).info
        return int(info.length * 1000)
    # Fallback — slower but always available.
    return len(AudioSegment.from_file(filepath))


def build_foreground_timeline_only(
    stem_plans: list[StemPlan],
    gap_ms: int = 600,
) -> tuple[int, dict[int, int]]:
    """Build a foreground timeline without decoding audio.

    Lightweight variant of :func:`build_foreground` that reads MP3
    durations via mutagen header inspection instead of loading full
    audio via pydub.  Enables ``--dry-run --timeline`` without
    expensive audio decoding.

    Args:
        stem_plans: Classified stem list from :func:`collect_stem_plans`.
        gap_ms: Silence gap between foreground stems in ms.

    Returns:
        Tuple of ``(total_ms, timeline)`` where ``timeline`` maps
        sequence numbers to millisecond offsets.
    """
    timeline: dict[int, int] = {}
    current_ms = 0

    for plan in sorted(stem_plans, key=lambda p: p.seq):
        timeline[plan.seq] = current_ms
        if plan.is_background:
            continue
        duration = _mp3_duration_ms(plan.filepath)
        current_ms += duration + gap_ms

    return current_ms, timeline


def compute_dialogue_labels(
    stem_plans: list[StemPlan],
    timeline: dict[int, int],
) -> list[tuple]:
    """Compute dialogue label tuples without loading audio.

    Args:
        stem_plans: Classified stem list.
        timeline: Cue-point timestamps from a foreground build.

    Returns:
        List of 7-element tuples ``(start_s, end_s, speaker, None, None, None, snippet)``
        where *snippet* is the first 5 words of the dialogue text (or ``None`` if no
        text is available).  Positions [3]–[5] are ``None`` (dialogue has no ramp or
        play_duration); position [6] carries the snippet for the HTML tooltip.
    """
    labels = []
    for plan in sorted(stem_plans, key=lambda p: p.seq):
        if plan.entry_type != "dialogue":
            continue
        start_ms = timeline.get(plan.seq, 0)
        duration = _mp3_duration_ms(plan.filepath)
        end_ms = start_ms + duration
        basename = os.path.splitext(os.path.basename(plan.filepath))[0]
        speaker = basename.rsplit("_", 1)[-1]
        words = (plan.text or "").split()
        snippet = " ".join(words[:5]) if words else None
        labels.append((start_ms / 1000.0, end_ms / 1000.0, speaker, None, None, None, snippet, None, plan.seq))
    return labels


def compute_ambience_labels(
    stem_plans: list[StemPlan],
    timeline: dict[int, int],
    total_ms: int,
) -> list[tuple[float, float, str]]:
    """Compute ambience label tuples without loading audio.

    Uses the same boundary logic as :func:`build_ambience_layer`.

    Args:
        stem_plans: Classified stem list.
        timeline: Cue-point timestamps.
        total_ms: Total episode duration in ms.

    Returns:
        List of ``(start_s, end_s, text)`` tuples.
    """
    labels: list[tuple[float, float, str]] = []
    ambience_plans = sorted(
        (p for p in stem_plans if p.direction_type == "AMBIENCE"),
        key=lambda p: p.seq,
    )
    if not ambience_plans:
        return labels

    bg_cues: list[tuple[int, int]] = sorted(
        (
            (timeline.get(p.seq, 0), p.seq)
            for p in stem_plans
            if p.is_background
        ),
        key=lambda t: t[0],
    )

    for plan in ambience_plans:
        if not plan.filepath:  # AMBIENCE: STOP marker — boundary only, no label
            continue
        start_ms = timeline.get(plan.seq, 0)
        if start_ms >= total_ms:
            continue
        end_ms = total_ms
        for cue_ms, cue_seq in bg_cues:
            if cue_seq > plan.seq and cue_ms > start_ms:
                end_ms = min(cue_ms, total_ms)
                break
        if end_ms - start_ms <= 0:
            continue
        label_text = plan.text or plan.direction_type or "AMBIENCE"
        labels.append((
            start_ms / 1000.0, end_ms / 1000.0, label_text,
            plan.ramp_in_seconds, plan.ramp_out_seconds,
            None, None, plan.volume_percentage, plan.seq,
        ))

    return labels


def compute_music_labels(
    stem_plans: list[StemPlan],
    timeline: dict[int, int],
    total_ms: int,
) -> list[tuple[float, float, str]]:
    """Compute music label tuples without loading audio.

    Args:
        stem_plans: Classified stem list.
        timeline: Cue-point timestamps.
        total_ms: Total episode duration in ms.

    Returns:
        List of ``(start_s, end_s, text)`` tuples.
    """
    labels: list[tuple[float, float, str]] = []
    for plan in sorted(stem_plans, key=lambda p: p.seq):
        if plan.direction_type != "MUSIC":
            continue
        start_ms = timeline.get(plan.seq, 0)
        if start_ms >= total_ms:
            continue
        duration = _mp3_duration_ms(plan.filepath)
        if plan.play_duration is not None and not plan.pre_trimmed:
            duration = max(1, int(duration * plan.play_duration / 100.0))
        label_text = plan.text or plan.direction_type or "MUSIC"
        labels.append((
            start_ms / 1000.0, (start_ms + duration) / 1000.0, label_text,
            plan.ramp_in_seconds, plan.ramp_out_seconds, plan.play_duration,
            None, plan.volume_percentage, plan.seq,
        ))
    return labels


def compute_sfx_labels(
    stem_plans: list[StemPlan],
    timeline: dict[int, int],
    total_ms: int,
) -> list[tuple[float, float, str]]:
    """Compute SFX/BEAT label tuples without loading audio.

    Args:
        stem_plans: Classified stem list.
        timeline: Cue-point timestamps.
        total_ms: Total episode duration in ms.

    Returns:
        List of ``(start_s, end_s, text)`` tuples.
    """
    labels: list[tuple[float, float, str]] = []
    for plan in sorted(stem_plans, key=lambda p: p.seq):
        if plan.direction_type not in ("SFX", "BEAT"):
            continue
        start_ms = timeline.get(plan.seq, 0)
        duration = _mp3_duration_ms(plan.filepath)
        label_text = plan.text or plan.direction_type or "SFX"
        labels.append((
            start_ms / 1000.0, (start_ms + duration) / 1000.0, label_text,
            None, None, None, None, plan.volume_percentage, plan.seq,
        ))
    return labels


def build_sfx_layer(
    stem_plans: list[StemPlan],
    timeline: dict[int, int],
    total_ms: int,
) -> AudioSegment:
    """Build an isolated SFX layer for DAW export.

    Places only one-shot SFX and BEAT stems (``direction_type in
    ("SFX", "BEAT")``) at their foreground timeline positions.

    Args:
        stem_plans: Classified stem list from :func:`collect_stem_plans`.
        timeline: Cue-point timestamps from :func:`build_foreground`.
        total_ms: Total track length in milliseconds.

    Returns:
        Tuple of ``(layer, labels)`` where *layer* is a full-length
        :class:`~pydub.AudioSegment` with SFX stems at their timeline
        positions, and *labels* is a list of ``(start_sec, end_sec, text)``
        tuples for each one-shot effect.
    """
    layer = AudioSegment.silent(duration=total_ms)
    labels: list[tuple[float, float, str]] = []
    for plan in sorted(stem_plans, key=lambda p: p.seq):
        if plan.direction_type not in ("SFX", "BEAT"):
            continue
        start_ms = timeline.get(plan.seq, 0)
        segment = AudioSegment.from_file(plan.filepath)
        if plan.volume_percentage is not None:
            segment = segment + _volume_pct_to_db(plan.volume_percentage)
        layer = layer.overlay(segment, position=start_ms)
        label_text = plan.text or plan.direction_type or "SFX"
        labels.append((
            start_ms / 1000.0, (start_ms + len(segment)) / 1000.0, label_text,
            None, None, None, None, plan.volume_percentage, plan.seq,
        ))
    return layer, labels
