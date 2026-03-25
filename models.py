"""Pydantic data models for the podcast production pipeline.

Defines validated, typed structures for script parsing output,
cast configuration, and production dialogue entries. These models
replace untyped dictionaries with field-level validation and
type annotations that render as rich API documentation via mkdocstrings.
"""

import json
import os
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Hardcoded fallback when no project.json or --show is provided.
DEFAULT_SLUG = "the413"


def show_slug(show_name: str) -> str:
    """Convert a show title to a filesystem-safe slug.

    Lowercases the string and strips all non-alphanumeric characters.

    Args:
        show_name: Human-readable show title (e.g., ``"THE 413"``).

    Returns:
        Compact slug like ``"the413"`` or ``"nightowls"``.
    """
    return re.sub(r"[^a-z0-9]", "", show_name.lower())


def derive_paths(slug: str, tag: str) -> dict[str, str]:
    """Derive all standard pipeline file paths from a show slug and episode tag.

    Args:
        slug: Show slug (e.g., ``"the413"``).
        tag: Episode tag (e.g., ``"S01E01"``).

    Returns:
        Dictionary mapping logical names to relative file paths.
    """
    return {
        "cast": f"cast_{slug}_{tag}.json",
        "sfx": f"sfx_{slug}_{tag}.json",
        "parsed": f"parsed/parsed_{slug}_{tag}.json",
        "parsed_csv": f"parsed/parsed_{slug}_{tag}.csv",
        "annotated_csv": f"parsed/parsed_{slug}_{tag}_annotated.csv",
        "master": f"{slug}_{tag}_master.mp3",
        "cues": f"cues/cues_{slug}_{tag}.md",
        "cues_manifest": f"cues/cues_manifest_{tag}.json",
        "orig_parsed": f"parsed/orig_parsed_{slug}_{tag}.json",
        "revised_script": f"scripts/revised_{slug}_{tag}.md",
    }


def resolve_slug(show_arg: str | None = None, project_path: str = "project.json") -> str:
    """Resolve the show slug from CLI arg, project.json, or the default.

    Resolution order:
    1. Explicit *show_arg* (passed through :func:`show_slug`).
    2. ``project.json`` ``"show"`` field (if the file exists).
    3. :data:`DEFAULT_SLUG` (``"the413"``).

    Args:
        show_arg: Value of ``--show`` CLI flag, or ``None``.
        project_path: Path to the project config file.

    Returns:
        Filesystem-safe show slug.
    """
    if show_arg:
        return show_slug(show_arg)
    if os.path.exists(project_path):
        with open(project_path, encoding="utf-8") as f:
            data = json.load(f)
        if "show" in data:
            return show_slug(data["show"])
    return DEFAULT_SLUG


def episode_tag(season: int | None, episode: int) -> str:
    """Format season/episode as a compact tag like ``S01E01`` or ``E01``.

    Args:
        season: Season number, or ``None`` if not declared.
        episode: Episode number.

    Returns:
        ``"S01E01"`` when season is set, ``"E01"`` otherwise.
    """
    if season is not None:
        return f"S{season:02d}E{episode:02d}"
    return f"E{episode:02d}"


# ---------------------------------------------------------------------------
# Script parsing models (Stage 1 output)
# ---------------------------------------------------------------------------


class ScriptEntry(BaseModel):
    """A single parsed entry from a production script.

    Each entry represents one line or block from the markdown script,
    classified into one of four types: dialogue, direction,
    section_header, or scene_header.

    Attributes:
        seq: Sequence number, 1-based and unique within a script.
        type: Entry classification determining how the line is processed.
        section: Current section slug (e.g., ``"cold-open"``, ``"act1"``).
        scene: Current scene slug (e.g., ``"scene-1"``) or ``None``.
        speaker: Normalized speaker key for dialogue entries (e.g., ``"adam"``).
        direction: Parenthetical acting direction for dialogue lines.
        text: The spoken text, header text, or stage direction content.
        direction_type: Subtype for direction entries indicating sound category.
    """

    seq: int = Field(..., description="Sequence number (negative for preamble entries)")
    type: Literal["dialogue", "direction", "section_header", "scene_header"] = Field(
        ..., description="Entry classification"
    )
    section: str | None = Field(default=None, description="Current section slug")
    scene: str | None = Field(default=None, description="Current scene slug")
    speaker: str | None = Field(default=None, description="Normalized speaker key")
    direction: str | None = Field(default=None, description="Acting direction")
    text: str = Field(..., description="Entry content text")
    direction_type: Literal["SFX", "MUSIC", "AMBIENCE", "BEAT"] | None = Field(
        default=None, description="Sound category for direction entries"
    )


class ScriptStats(BaseModel):
    """Aggregate statistics for a parsed production script.

    Attributes:
        total_entries: Total number of parsed entries.
        dialogue_lines: Count of dialogue-type entries.
        direction_lines: Count of direction-type entries.
        characters_for_tts: Total character count across all dialogue text.
        speakers: Sorted list of unique speaker keys found in the script.
        sections: Sorted list of unique section slugs found in the script.
    """

    total_entries: int = Field(..., ge=0, description="Total parsed entries")
    dialogue_lines: int = Field(..., ge=0, description="Dialogue entry count")
    direction_lines: int = Field(..., ge=0, description="Direction entry count")
    characters_for_tts: int = Field(..., ge=0, description="TTS character budget")
    speakers: list[str] = Field(..., description="Unique speaker keys")
    sections: list[str] = Field(..., description="Unique section slugs")


class ParsedScript(BaseModel):
    """Complete output of the script parsing stage.

    Produced by ``parse_script()`` in XILP001, consumed by
    ``load_production()`` in XILP002.

    Attributes:
        show: Show title (e.g., ``"THE 413"``).
        season: Season number, or ``None`` if not declared in the script header.
        episode: Episode number.
        title: Episode title.
        source_file: Basename of the source markdown file.
        entries: Ordered list of parsed script entries.
        stats: Aggregate statistics for the parsed script.
    """

    show: str = Field(..., description="Show title")
    season: int | None = Field(default=None, description="Season number")
    episode: int = Field(..., description="Episode number")
    title: str = Field(..., description="Episode title")
    source_file: str = Field(..., description="Source markdown filename")
    entries: list[ScriptEntry] = Field(..., description="Parsed script entries")
    stats: ScriptStats = Field(..., description="Aggregate statistics")

    @property
    def tag(self) -> str:
        """Compact season/episode tag, e.g. ``S01E01`` or ``E01``."""
        return episode_tag(self.season, self.episode)


# ---------------------------------------------------------------------------
# Cast configuration models
# ---------------------------------------------------------------------------


class CastMember(BaseModel):
    """Configuration for a single cast member's voice and audio settings.

    Maps a character to their ElevenLabs voice and stereo positioning.

    Attributes:
        full_name: Character's display name (e.g., ``"Adam Santos"``).
        voice_id: ElevenLabs voice identifier, or ``"TBD"`` if unassigned.
        pan: Stereo pan position from -1.0 (full left) to 1.0 (full right).
        filter: Whether to apply phone-speaker audio filter.
        role: Character role description (e.g., ``"Host/Narrator"``).
    """

    full_name: str = Field(..., description="Character display name")
    voice_id: str = Field(..., min_length=1, description="ElevenLabs voice ID")
    pan: float = Field(..., ge=-1.0, le=1.0, description="Stereo pan position")
    filter: bool = Field(..., description="Apply phone-speaker filter")
    role: str = Field(..., description="Character role description")
    stability: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Voice stability (0=expressive, 1=monotone); None uses voice default",
    )
    similarity_boost: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Adherence to original voice (0=loose, 1=strict); None uses voice default",
    )
    style: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Style exaggeration of the original speaker; None uses voice default",
    )
    use_speaker_boost: bool | None = Field(
        default=None,
        description="Boost similarity to original speaker (higher latency); None uses voice default",
    )
    language_code: str | None = Field(
        default=None,
        description="ISO 639-1 language code for text normalisation (e.g. 'en', 'de'); None = auto",
    )


class PreambleSegment(BaseModel):
    """One cacheable slice of a multi-part preamble.

    Attributes:
        text: Spoken text (may use {season_title}, {episode}, {title} placeholders).
        shared_key: If set, the segment is cached as ``SFX/{shared_key}.mp3`` and
            reused across episodes.  If ``None`` the segment is episode-specific
            and regenerated each run.
    """

    text: str = Field(..., description="Segment text (may use {season_title}, {episode}, {title})")
    shared_key: str | None = Field(
        default=None,
        description="SFX cache key (e.g. 'preamble-the413-tina-intro'); None = episode-specific",
    )


class Preamble(BaseModel):
    """Broadcast introduction prepended to every episode.

    Attributes:
        text: Single-string intro text (legacy).  Mutually exclusive with ``segments``.
        segments: Ordered list of cacheable text segments.  Stock segments carry a
            ``shared_key`` so they are generated once and reused; the variable
            episode-identifier segment has ``shared_key=None``.
        speaker: Cast key for the reader (e.g. "tina").
        speed: TTS speaking rate passed to ElevenLabs VoiceSettings (0.7–1.2,
            default 1.0). Values below 1.0 slow the reader down.
    """

    text: str | None = Field(
        default=None,
        description="Intro text (may use {season_title}, {episode}, {title}); legacy single-string form",
    )
    segments: list[PreambleSegment] | None = Field(
        default=None,
        description="Ordered cacheable segments; preferred over 'text' for new episodes",
    )
    speaker: str = Field(..., description="Cast member key for TTS generation")
    speed: float | None = Field(
        default=None, ge=0.7, le=1.2,
        description="TTS speaking rate (0.7–1.2); None uses the voice default"
    )

    @model_validator(mode="after")
    def _require_text_or_segments(self) -> "Preamble":
        if self.text is None and not self.segments:
            raise ValueError("Preamble requires either 'text' or 'segments'")
        return self


class CastConfiguration(BaseModel):
    """Complete cast configuration for a production episode.

    Loaded from the cast config JSON and used by ``load_production()``
    to map speaker keys to voice and audio settings.

    Attributes:
        show: Show title (e.g., ``"THE 413"``).
        season: Season number, or ``None`` if not set in the cast file.
        episode: Episode number.
        title: Episode title (optional, not used during production).
        season_title: Season subtitle/arc title (e.g., ``"The Letters"``).
        preamble: Broadcast intro configuration, or ``None`` if not configured.
        cast: Mapping of speaker keys to their voice configurations.
    """

    show: str = Field(..., description="Show title")
    season: int | None = Field(default=None, description="Season number")
    episode: int = Field(..., description="Episode number")
    title: str | None = Field(default=None, description="Episode title")
    season_title: str | None = Field(default=None, description="Season subtitle/arc title")
    artist: str = Field(
        default="Tina Brissette for Berkshire Talking Chronicles",
        description="Artist/creator credit for audio metadata",
    )
    preamble: Preamble | None = Field(default=None, description="Broadcast intro config")
    postamble: Preamble | None = Field(default=None, description="Broadcast outro config")
    cast: dict[str, CastMember] = Field(..., description="Speaker-to-config mapping")

    @property
    def tag(self) -> str:
        """Compact season/episode tag, e.g. ``S01E01`` or ``E01``."""
        return episode_tag(self.season, self.episode)


# ---------------------------------------------------------------------------
# Production pipeline models (Stage 2/3)
# ---------------------------------------------------------------------------


class VoiceConfig(BaseModel):
    """Simplified voice configuration used during voice generation.

    Built from ``CastMember`` by ``load_production()``, carrying only
    the fields needed for TTS generation and audio assembly.

    Attributes:
        id: ElevenLabs voice identifier.
        pan: Stereo pan position from -1.0 (full left) to 1.0 (full right).
        filter: Whether to apply phone-speaker audio filter.
    """

    id: str = Field(..., description="ElevenLabs voice ID")
    pan: float = Field(..., ge=-1.0, le=1.0, description="Stereo pan position")
    filter: bool = Field(..., description="Apply phone-speaker filter")


class DialogueEntry(BaseModel):
    """A single dialogue line prepared for voice generation.

    Produced by ``load_production()`` from parsed script entries,
    enriched with the stem filename for audio output.

    Attributes:
        speaker: Normalized speaker key (e.g., ``"adam"``).
        text: Spoken dialogue text to synthesize.
        stem_name: Output filename stem (e.g., ``"003_cold-open_adam"``).
        seq: Sequence number from the parsed script.
        direction: Acting direction for the line, if any.
    """

    speaker: str = Field(..., description="Speaker key")
    text: str = Field(..., description="Dialogue text for TTS")
    stem_name: str = Field(..., description="Output audio stem name")
    seq: int = Field(..., description="Sequence number; negative values reserved for preamble entries")
    direction: str | None = Field(default=None, description="Acting direction")


# ---------------------------------------------------------------------------
# SFX configuration models
# ---------------------------------------------------------------------------


class SfxEntry(BaseModel):
    """A single sound effect mapping from script direction to API parameters.

    Maps a direction entry's text (e.g., ``"SFX: PHONE BUZZING"``) to the
    ElevenLabs Sound Effects API parameters needed to generate it, or marks
    it as silence (for BEAT entries).

    Attributes:
        prompt: Natural-language description for the ElevenLabs SFX API.
            ``None`` for silence entries.
        type: Whether this is an API-generated sound effect or local silence.
        duration_seconds: Length of the generated audio (0.5–30.0s).
        prompt_influence: How closely the output follows the prompt (0.0–1.0).
            ``None`` to use the config-level default.
        loop: Whether the effect should be loopable (useful for ambience).
    """

    prompt: str | None = Field(default=None, description="ElevenLabs SFX prompt")
    type: Literal["sfx", "silence"] = Field(
        default="sfx", description="Effect type: API-generated or local silence"
    )
    duration_seconds: float = Field(
        default=5.0, ge=0.0, description="Audio duration in seconds (0.0 for stop markers)"
    )
    prompt_influence: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Prompt adherence (0.0–1.0), None for config default",
    )
    loop: bool = Field(default=False, description="Generate loopable audio")
    source: str | None = Field(
        default=None,
        description="Path to a pre-existing audio file (bypasses API generation)",
    )
    volume_percentage: float | None = Field(
        default=None, ge=0.0, le=200.0,
        description="Playback volume as percentage (100=unity); None uses category default",
    )
    ramp_in_seconds: float | None = Field(
        default=None, ge=0.0, le=30.0,
        description="Fade-in duration in seconds; None uses category default",
    )
    ramp_out_seconds: float | None = Field(
        default=None, ge=0.0, le=30.0,
        description="Fade-out duration in seconds; None uses category default",
    )
    play_duration: float | None = Field(
        default=None, ge=0.0, le=100.0,
        description="Percentage of clip duration to play (100=full); None plays full clip",
    )

    @model_validator(mode="after")
    def _check_api_duration_cap(self) -> "SfxEntry":
        """Enforce the 30 s ElevenLabs API cap and zero-duration guard."""
        if self.type == "sfx" and self.source is None:
            if self.duration_seconds == 0.0:
                raise ValueError(
                    "duration_seconds must be > 0 for API-generated effects; "
                    "use type='silence' for stop markers"
                )
            if self.duration_seconds > 30.0:
                raise ValueError(
                    f"duration_seconds must be ≤ 30.0 for API-generated effects "
                    f"(got {self.duration_seconds}); set source= for pre-existing files"
                )
        return self


class SfxConfiguration(BaseModel):
    """Sound effects configuration for a production episode.

    Analogous to :class:`CastConfiguration` for voices. Maps parsed
    direction entry text to ElevenLabs Sound Effects API parameters.

    Attributes:
        show: Show title (e.g., ``"THE 413"``).
        season: Season number, or ``None`` if not declared.
        episode: Episode number.
        defaults: Shared default settings (e.g., ``prompt_influence``).
        effects: Mapping of direction text to SFX entry configurations.
    """

    show: str = Field(..., description="Show title")
    season: int | None = Field(default=None, description="Season number")
    episode: int = Field(..., description="Episode number")
    defaults: dict = Field(default_factory=dict, description="Shared SFX defaults")
    effects: dict[str, SfxEntry] = Field(
        ..., description="Direction text to SFX mapping"
    )

    @property
    def tag(self) -> str:
        """Compact season/episode tag, e.g. ``S01E01`` or ``E01``."""
        return episode_tag(self.season, self.episode)
