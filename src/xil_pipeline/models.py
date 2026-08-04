# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pydantic data models for the podcast production pipeline.

Defines validated, typed structures for script parsing output,
cast configuration, and production dialogue entries. These models
replace untyped dictionaries with field-level validation and
type annotations that render as rich API documentation via mkdocstrings.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Hardcoded fallback when no project.json or --show is provided.
DEFAULT_SLUG = "sample"


def get_workspace_root() -> Path:
    """Return the active workspace root.

    Resolves in priority order:
    1. ``XIL_PROJECTROOT`` environment variable (absolute path).
    2. Current working directory (existing behaviour).
    """
    env_val = os.environ.get("XIL_PROJECTROOT")
    if env_val:
        return Path(env_val).expanduser().resolve()
    return Path.cwd()


def get_code_root() -> Path | None:
    """Return the **code root** — the directory that holds the optional local-model
    virtualenvs (``venv-chatterbox``, ``venv-whisper``).

    Resolves the ``XIL_CODEROOT`` environment variable (absolute path, tilde-expanded),
    or ``None`` when it is unset.  This is distinct from :func:`get_workspace_root`
    (``XIL_PROJECTROOT``): the code root tracks where the *software* and its heavy
    model venvs live, while the workspace root tracks the show *content*.  They differ
    when the package is installed once and pointed at a separate content directory.
    """
    env_val = os.environ.get("XIL_CODEROOT")
    return Path(env_val).expanduser().resolve() if env_val else None


def resolve_venv_python(venv_name: str, explicit: str | None = None) -> str | None:
    """Resolve the ``python3`` interpreter for an optional local-model venv.

    Used to locate ``venv-chatterbox`` / ``venv-whisper``, which
    carry heavy ML dependencies and are never installed into the main package.

    Resolution order:

    1. *explicit* — a caller-supplied interpreter path (e.g. the per-command
       ``--chatterbox-python`` / ``--whisper-python`` flag).
       Wins when provided.
    2. ``XIL_CODEROOT`` — when set, ``$XIL_CODEROOT/<venv_name>/bin/python3`` is used
       **exclusively**: it overrides auto-detection entirely and there is **no
       fallback** (returns ``None`` if the interpreter is absent there).
    3. Auto-detect — ``<workspace>/<venv_name>/bin/python3``, then the repo root next
       to the running install.

    Args:
        venv_name: Directory name of the venv (e.g. ``"venv-chatterbox"``).
        explicit: An interpreter path that takes precedence over all detection.

    Returns:
        The interpreter path string, or ``None`` if none was found.
    """
    if explicit:
        return explicit
    code_root = get_code_root()
    if code_root is not None:
        cand = code_root / venv_name / "bin" / "python3"
        return str(cand) if cand.exists() else None
    for cand in (
        get_workspace_root() / venv_name / "bin" / "python3",
        Path(__file__).resolve().parent.parent.parent / venv_name / "bin" / "python3",
        Path(sys.executable).resolve().parent.parent.parent / venv_name / "bin" / "python3",
    ):
        if cand.exists():
            return str(cand)
    return None


def get_active_show() -> str | None:
    """Return the slug from ``.active_show``, or ``None`` if not set."""
    f = get_workspace_root() / ".active_show"
    return f.read_text(encoding="utf-8").strip() if f.exists() else None


def set_active_show(slug: str) -> None:
    """Write *slug* to ``.active_show`` in the workspace root."""
    f = get_workspace_root() / ".active_show"
    f.write_text(slug, encoding="utf-8")

# Per-type production defaults (gap_ms between dialogue stems, voice stability hint).
TYPE_DEFAULTS: dict[str, dict] = {
    "podcast":   {"gap_ms": 600, "stability": None},
    "audiobook": {"gap_ms": 400, "stability": 0.75},
    "drama":     {"gap_ms": 800, "stability": None},
    "special":   {"gap_ms": 600, "stability": None},
}


def show_slug(show_name: str) -> str:
    """Convert a show title to a filesystem-safe slug.

    Lowercases the string and strips all non-alphanumeric characters.

    Args:
        show_name: Human-readable show title (e.g., ``"nightowls"``).

    Returns:
        Compact slug like ``"nightowls"`` or ``"mypodcast"``.
    """
    return re.sub(r"[^a-z0-9]", "", show_name.lower())


def _derive_paths_new(slug: str, tag: str) -> dict[str, str]:
    """Normalized workspace layout paths (0.1.8+)."""
    root = str(get_workspace_root())
    return {
        "cast": os.path.join(root, "configs", slug, f"cast_{tag}.json"),
        "sfx": os.path.join(root, "configs", slug, f"sfx_{tag}.json"),
        "parsed": os.path.join(root, "parsed", slug, f"parsed_{tag}.json"),
        "parsed_csv": os.path.join(root, "parsed", slug, f"parsed_{tag}.csv"),
        "annotated_csv": os.path.join(root, "parsed", slug, f"annotated_{tag}.csv"),
        "master": os.path.join(root, "masters", slug, f"{tag}_master.mp3"),
        "cues": os.path.join(root, "cues", slug, f"cues_{tag}.md"),
        "cues_manifest": os.path.join(root, "cues", slug, f"cues_manifest_{tag}.json"),
        "orig_parsed": os.path.join(root, "parsed", slug, f"orig_parsed_{tag}.json"),
        "revised_script": os.path.join(root, "scripts", slug, f"revised_{slug}_{tag}.md"),
        "stems": os.path.join(root, "stems", slug, tag),
        "daw": os.path.join(root, "daw", slug, tag),
        "posts": os.path.join(root, "posts", slug, f"{tag}_posts.md"),
    }


def derive_paths_legacy(slug: str, tag: str) -> dict[str, str]:
    """Legacy workspace layout paths (pre-0.1.8) — used by the migration tool.

    Args:
        slug: Show slug (e.g., ``"the413"``).
        tag: Episode tag (e.g., ``"S01E01"``).

    Returns:
        Dictionary mapping logical names to legacy absolute file paths.
    """
    root = str(get_workspace_root())
    return {
        "cast": os.path.join(root, f"cast_{slug}_{tag}.json"),
        "sfx": os.path.join(root, f"sfx_{slug}_{tag}.json"),
        "parsed": os.path.join(root, "parsed", f"parsed_{slug}_{tag}.json"),
        "parsed_csv": os.path.join(root, "parsed", f"parsed_{slug}_{tag}.csv"),
        "annotated_csv": os.path.join(root, "parsed", f"parsed_{slug}_{tag}_annotated.csv"),
        "master": os.path.join(root, f"{slug}_{tag}_master.mp3"),
        "cues": os.path.join(root, "cues", f"cues_{slug}_{tag}.md"),
        "cues_manifest": os.path.join(root, "cues", f"cues_manifest_{tag}.json"),
        "orig_parsed": os.path.join(root, "parsed", f"orig_parsed_{slug}_{tag}.json"),
        "revised_script": os.path.join(root, "scripts", f"revised_{slug}_{tag}.md"),
        "stems": os.path.join(root, "stems", slug, tag),
        "daw": os.path.join(root, "daw", tag),
    }


def derive_paths(slug: str, tag: str) -> dict[str, str]:
    """Derive all standard pipeline file paths from a show slug and episode tag.

    Auto-detects workspace layout: returns legacy paths when the cast config
    exists at the legacy root location (pre-0.1.8 workspaces), and normalized
    paths otherwise (new workspaces or post-migration).  Run ``xil migrate-workspace``
    to move an existing workspace to the normalized layout.

    Args:
        slug: Show slug (e.g., ``"nightowls"``).
        tag: Episode tag (e.g., ``"S01E01"``).

    Returns:
        Dictionary mapping logical names to relative file paths.
    """
    new = _derive_paths_new(slug, tag)
    legacy = derive_paths_legacy(slug, tag)
    use_legacy = os.path.exists(legacy["cast"]) and not os.path.exists(new["cast"])
    return legacy if use_legacy else new


def _read_project(project_path: str = "project.json") -> dict:
    """Return the parsed contents of *project_path*, or ``{}`` if absent."""
    if not os.path.isabs(project_path):
        root = get_workspace_root()
        if project_path == "project.json":
            active_file = root / ".active_show"
            if active_file.exists():
                slug = active_file.read_text(encoding="utf-8").strip()
                candidate = root / "configs" / slug / "project.json"
                if candidate.exists():
                    with open(candidate, encoding="utf-8") as f:
                        return json.load(f)
        project_path = str(root / project_path)
    if os.path.exists(project_path):
        with open(project_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Project configuration model
# ---------------------------------------------------------------------------


class _DocModel(BaseModel):
    """Base for pipeline models: field docs live in per-attribute docstrings.

    Sets ``use_attribute_docstrings`` (note the *plural* Pydantic config key) so the
    string literal beneath each field becomes its JSON-schema ``description``. This
    keeps documentation in one place instead of duplicating it across
    ``Field(description=...)`` and a class ``Attributes:`` block.
    """

    model_config = ConfigDict(use_attribute_docstrings=True)


class ProjectConfig(_DocModel):
    """Typed view of ``project.json``.

    All fields are optional with sensible defaults so that a minimal
    ``{"show": "My Show"}`` project.json validates without change.
    """

    show: str = Field(default="Sample Show")
    """Human-readable show title."""

    type: Literal["podcast", "audiobook", "drama", "special"] = Field(default="podcast")
    """Content type — ``"podcast"`` (default), ``"audiobook"``, ``"drama"``, or
    ``"special"``. Drives section maps, gap defaults, and ``xil-init`` sample templates."""

    season: int | None = Field(default=None)
    """Season number, or ``None``."""

    season_title: str | None = Field(default=None)
    """Season arc title (e.g. ``"The Holiday Shift"``)."""

    tag_format: str | None = Field(default=None)
    """Custom episode tag format string (e.g. ``"V{volume:02d}C{chapter:02d}"`` for
    audiobooks). ``None`` uses the standard ``S01E01`` / ``E01`` derivation."""


def load_project_config(project_path: str = "project.json") -> ProjectConfig:
    """Load and validate ``project.json``, returning a :class:`ProjectConfig`.

    Args:
        project_path: Path to the project config file.

    Returns:
        :class:`ProjectConfig` with all fields populated (defaults where absent).
    """
    data = _read_project(project_path)
    return ProjectConfig(**data)


def resolve_project_type(project_path: str = "project.json") -> str:
    """Return the content type from ``project.json``, defaulting to ``"podcast"``.

    Args:
        project_path: Path to the project config file.

    Returns:
        One of ``"podcast"``, ``"audiobook"``, ``"drama"``, or ``"special"``.
    """
    data = _read_project(project_path)
    return data.get("type", "podcast")


def resolve_slug(show_arg: str | None = None, project_path: str = "project.json") -> str:
    """Resolve the show slug from CLI arg, project.json, or the default.

    Resolution order:
    1. Explicit *show_arg* (passed through :func:`show_slug`).
    2. ``project.json`` ``"show"`` field (if the file exists).
    3. :data:`DEFAULT_SLUG` (``"sample"``).

    Args:
        show_arg: Value of ``--show`` CLI flag, or ``None``.
        project_path: Path to the project config file.

    Returns:
        Filesystem-safe show slug.
    """
    if show_arg:
        return show_slug(show_arg)
    data = _read_project(project_path)
    if "show" in data:
        return show_slug(data["show"])
    return DEFAULT_SLUG


def resolve_season_title(
    season_title_arg: str | None = None,
    project_path: str = "project.json",
) -> str | None:
    """Resolve the season/arc title from an explicit value or project.json.

    Resolution order:
    1. Explicit *season_title_arg* (e.g. extracted from the script header ``Arc:`` token).
    2. ``project.json`` ``"season_title"`` field (if the file exists and the key is present).
    3. ``None`` — no season title is available.

    Args:
        season_title_arg: Season title already known (e.g. from the script header), or ``None``.
        project_path: Path to the project config file.

    Returns:
        Season title string, or ``None`` when not available from any source.
    """
    if season_title_arg is not None:
        return season_title_arg
    data = _read_project(project_path)
    return data.get("season_title") or None


def resolve_season(
    season_arg: int | None = None,
    project_path: str = "project.json",
) -> int | None:
    """Resolve the season number from an explicit value or project.json.

    Resolution order:
    1. Explicit *season_arg* (e.g. parsed from the script header ``Season N:`` token).
    2. ``project.json`` ``"season"`` field (if the file exists and the key is present).
    3. ``None`` — no season number is available.

    Args:
        season_arg: Season number already known (e.g. from the script header), or ``None``.
        project_path: Path to the project config file.

    Returns:
        Season number as an integer, or ``None`` when not available from any source.
    """
    if season_arg is not None:
        return season_arg
    data = _read_project(project_path)
    val = data.get("season")
    return int(val) if val is not None else None


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


class ScriptEntry(_DocModel):
    """A single parsed entry from a production script.

    Each entry represents one line or block from the markdown script,
    classified into one of four types: dialogue, direction,
    section_header, or scene_header.
    """

    seq: int = Field(...)
    """Sequence number, 1-based and unique within a script."""

    type: Literal["dialogue", "direction", "section_header", "scene_header"] = Field(...)
    """Entry classification determining how the line is processed."""

    section: str | None = Field(default=None)
    """Current section slug (e.g., ``"cold-open"``, ``"act1"``)."""

    scene: str | None = Field(default=None)
    """Current scene slug (e.g., ``"scene-1"``) or ``None``."""

    speaker: str | None = Field(default=None)
    """Normalized speaker key for dialogue entries (e.g., ``"adam"``)."""

    direction: str | None = Field(default=None)
    """Parenthetical acting direction for dialogue lines."""

    text: str = Field(...)
    """The spoken text, header text, or stage direction content."""

    direction_type: Literal["SFX", "MUSIC", "AMBIENCE", "BEAT", "VINTAGE FILTER"] | None = Field(default=None)
    """Subtype for direction entries indicating sound category."""

    sfx_source: str | None = Field(default=None)
    """Scriptwriter SFX source hint (e.g. ``'SFX/filename.mp3'``), stripped from the
    ``'| filename'`` annotation in the script."""

    sfx_overrides: dict[str, float] | None = Field(default=None)
    """Per-cue :class:`SfxEntry` overrides from the script's attribute hints, keyed by
    config field name — ``'| play_volume_pct=20%'`` yields
    ``{"volume_percentage": 20.0}``. Applied on top of the generated SFX config entry;
    the script wins over any value already in ``sfx_<TAG>.json``."""


class ScriptStats(_DocModel):
    """Aggregate statistics for a parsed production script."""

    total_entries: int = Field(..., ge=0)
    """Total number of parsed entries."""

    dialogue_lines: int = Field(..., ge=0)
    """Count of dialogue-type entries."""

    direction_lines: int = Field(..., ge=0)
    """Count of direction-type entries."""

    characters_for_tts: int = Field(..., ge=0)
    """Total character count across all dialogue text."""

    speakers: list[str] = Field(...)
    """Sorted list of unique speaker keys found in the script."""

    sections: list[str] = Field(...)
    """Sorted list of unique section slugs found in the script."""


class ParsedScript(_DocModel):
    """Complete output of the script parsing stage.

    Produced by ``parse_script()`` in XILP001, consumed by
    ``load_production()`` in XILP002.
    """

    show: str = Field(...)
    """Show title (e.g., ``"nightowls"``)."""

    season: int | None = Field(default=None)
    """Season number, or ``None`` if not declared in the script header."""

    episode: int = Field(...)
    """Episode number."""

    title: str = Field(...)
    """Episode title."""

    season_title: str | None = Field(default=None)
    """Season arc title extracted from ``Arc: "…"`` in the script header (e.g.
    ``"The Holiday Shift"``). ``None`` when the header contains no arc declaration."""

    source_file: str = Field(...)
    """Basename of the source markdown file."""

    entries: list[ScriptEntry] = Field(...)
    """Ordered list of parsed script entries."""

    stats: ScriptStats = Field(...)
    """Aggregate statistics for the parsed script."""

    @property
    def tag(self) -> str:
        """Compact season/episode tag, e.g. ``S01E01`` or ``E01``."""
        return episode_tag(self.season, self.episode)


# ---------------------------------------------------------------------------
# Cast configuration models
# ---------------------------------------------------------------------------


class CastMember(_DocModel):
    """Configuration for a single cast member's voice and audio settings.

    Maps a character to their ElevenLabs voice and stereo positioning.
    """

    full_name: str = Field(...)
    """Character's display name (e.g., ``"Adam Santos"``)."""

    voice_id: str = Field(...)
    """ElevenLabs voice identifier; ``"TBD"`` if unassigned, or ``""`` for
    non-ElevenLabs backends."""

    pan: float = Field(..., ge=-1.0, le=1.0)
    """Stereo pan position from -1.0 (full left) to 1.0 (full right)."""

    filter: str | bool | None = Field(...)
    """Audio filter chain. ``False``/``None`` = none; ``True``/``"phone"`` = phone
    filter; ``"vintage"`` = vintage filter; ``"vintage,phone"`` = both filters applied
    in listed order."""

    role: str = Field(...)
    """Character role description (e.g., ``"Host/Narrator"``)."""

    stability: float | None = Field(default=None, ge=0.0, le=1.0)
    """Voice stability (0=expressive, 1=monotone); None uses voice default."""

    similarity_boost: float | None = Field(default=None, ge=0.0, le=1.0)
    """Adherence to original voice (0=loose, 1=strict); None uses voice default."""

    style: float | None = Field(default=None, ge=0.0, le=1.0)
    """Style exaggeration of the original speaker; None uses voice default."""

    use_speaker_boost: bool | None = Field(default=None)
    """Boost similarity to original speaker (higher latency); None uses voice default."""

    language_code: str | None = Field(default=None)
    """ISO 639-1 language code for text normalisation (e.g. 'en', 'de'); None = auto."""

    speed: float | None = Field(default=None, ge=0.7, le=1.5)
    """TTS speaking rate (0.7=slow … 1.0=default … 1.5=fast); None uses voice default."""


class PreambleSegment(_DocModel):
    """One text slice of a multi-part preamble or postamble."""

    text: str = Field(...)
    """Spoken text (may use {season_title}, {episode}, {title} placeholders)."""

    shared_key: str | None = Field(default=None)
    """Retained for backward compatibility with existing cast JSONs. No longer used at
    generation time — all segments are joined and sent as a single TTS call to produce
    seamless prosody across the whole block."""


class Preamble(_DocModel):
    """Broadcast introduction prepended to every episode."""

    text: str | None = Field(default=None)
    """Single-string intro text (legacy; may use {season_title}, {episode}, {title}
    placeholders). Mutually exclusive with ``segments``."""

    segments: list[PreambleSegment] | None = Field(default=None)
    """Ordered list of cacheable text segments (preferred over ``text`` for new
    episodes). Stock segments carry a ``shared_key`` so they are generated once and
    reused; the variable episode-identifier segment has ``shared_key=None``."""

    speaker: str = Field(...)
    """Cast key for the reader (e.g. ``"tina"``)."""

    speed: float | None = Field(default=None, ge=0.7, le=1.2)
    """TTS speaking rate passed to ElevenLabs VoiceSettings (0.7–1.2, default 1.0).
    Values below 1.0 slow the reader down; None uses the voice default."""


class CastConfiguration(_DocModel):
    """Complete cast configuration for a production episode.

    Loaded from the cast config JSON and used by ``load_production()``
    to map speaker keys to voice and audio settings.
    """

    show: str = Field(...)
    """Show title (e.g., ``"nightowls"``)."""

    season: int | None = Field(default=None)
    """Season number, or ``None`` if not set in the cast file."""

    episode: int | None = Field(default=None)
    """Episode number."""

    tag_override: str | None = Field(default=None)
    """Raw tag for non-episodic content (e.g. ``V01C03``, ``D01``) — overrides
    season/episode derivation."""

    title: str | None = Field(default=None)
    """Episode title (optional, not used during production)."""

    season_title: str | None = Field(default=None)
    """Season subtitle/arc title (e.g., ``"The Letters"``)."""

    artist: str = Field(default="XIL Pipeline")
    """Artist/creator credit for audio metadata."""

    preamble: Preamble | None = Field(default=None)
    """Broadcast intro configuration, or ``None`` if not configured."""

    postamble: Preamble | None = Field(default=None)
    """Broadcast outro configuration, or ``None`` if not configured."""

    cast: dict[str, CastMember] = Field(...)
    """Mapping of speaker keys to their voice configurations."""

    @property
    def tag(self) -> str:
        """Compact tag: raw override (e.g. ``V01C03``) or derived ``S01E01`` / ``E01``."""
        if self.tag_override:
            return self.tag_override
        if self.episode is None:
            raise ValueError("CastConfiguration requires either tag_override or episode")
        return episode_tag(self.season, self.episode)


# ---------------------------------------------------------------------------
# Production pipeline models (Stage 2/3)
# ---------------------------------------------------------------------------


class VoiceConfig(_DocModel):
    """Simplified voice configuration used during voice generation.

    Built from ``CastMember`` by ``load_production()``, carrying only
    the fields needed for TTS generation and audio assembly.
    """

    id: str = Field(...)
    """ElevenLabs voice identifier."""

    pan: float = Field(..., ge=-1.0, le=1.0)
    """Stereo pan position from -1.0 (full left) to 1.0 (full right)."""

    filter: str | bool | None = Field(...)
    """Audio filter chain (see ``CastMember.filter``)."""


class DialogueEntry(_DocModel):
    """A single dialogue line prepared for voice generation.

    Produced by ``load_production()`` from parsed script entries,
    enriched with the stem filename for audio output.
    """

    speaker: str = Field(...)
    """Normalized speaker key (e.g., ``"adam"``)."""

    text: str = Field(...)
    """Spoken dialogue text to synthesize."""

    stem_name: str = Field(...)
    """Output filename stem (e.g., ``"003_cold-open_adam"``)."""

    seq: int = Field(...)
    """Sequence number from the parsed script."""

    section: str | None = Field(default=None)
    """Script section slug (e.g. ``'preamble'``, ``'act1'``)."""

    direction: str | None = Field(default=None)
    """Acting direction for the line, if any."""


# ---------------------------------------------------------------------------
# SFX configuration models
# ---------------------------------------------------------------------------


class SfxEntry(_DocModel):
    """A single sound effect mapping from script direction to API parameters.

    Maps a direction entry's text (e.g., ``"SFX: PHONE BUZZING"``) to the
    ElevenLabs Sound Effects API parameters needed to generate it, or marks
    it as silence (for BEAT entries).

    Note: per-effect volume is always ``volume_percentage``, not the prefixed
    form (``ambience_volume_percentage`` etc.) — those belong in ``defaults`` only.
    """

    prompt: str | None = Field(default=None)
    """Natural-language description for the ElevenLabs SFX API. ``None`` for
    ``type="silence"`` or ``source``-based entries (no generation)."""

    type: Literal["sfx", "silence"] = Field(default="sfx")
    """Whether this is an API-generated sound effect (``"sfx"``) or local silence
    (``"silence"``, e.g. a BEAT stop marker)."""

    duration_seconds: float = Field(default=5.0, ge=0.0)
    """Audio length in seconds — meaning depends on the entry kind. For
    API-generated cues (no ``source``), the requested generation length (must be
    > 0 and ≤ 30). For ``source=`` cues, clips the file to this many seconds **at
    mix time** unless ``play_duration`` is set; ``0`` plays the source full-length.
    For ``type="silence"``, the silence duration (``0.0`` = stop marker)."""

    prompt_influence: float | None = Field(default=None, ge=0.0, le=1.0)
    """How closely the ElevenLabs output follows the prompt (0.0–1.0); ``None``
    uses the config-level default."""

    loop: bool = Field(default=False)
    """Whether to generate loopable audio (useful for ambience beds)."""

    source: str | None = Field(default=None)
    """Path to a pre-existing audio file. When set, bypasses API generation and the
    file is used directly; length is governed by ``play_duration`` /
    ``duration_seconds`` (see those fields)."""

    volume_percentage: float | None = Field(default=None, ge=0.0, le=200.0)
    """Per-effect playback volume as a percentage (100 = unity gain, 0–200); ``None``
    uses the category default. Always the un-prefixed form (the prefixed variants
    belong in ``defaults`` only)."""

    ramp_in_seconds: float | None = Field(default=None, ge=0.0, le=30.0)
    """Fade-in duration in seconds (0–30); ``None`` uses the category default."""

    ramp_out_seconds: float | None = Field(default=None, ge=0.0, le=30.0)
    """Fade-out duration in seconds (0–30); ``None`` uses the category default."""

    play_duration: float | None = Field(default=None, ge=0.0, le=100.0)
    """Percentage of the clip to play (0–100; 100 = full). Applies to ``source=``
    one-shots (SFX/MUSIC/BEAT) and **takes precedence over ``duration_seconds``**
    when set. ``None`` plays the full clip, subject to any ``duration_seconds``
    clipping."""

    @model_validator(mode="before")
    @classmethod
    def _reject_prefixed_volume(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        bad = [k for k in data if k in ("ambience_volume_percentage",
                                         "music_volume_percentage",
                                         "sfx_volume_percentage",
                                         "vintage_filter_volume_percentage")]
        if bad:
            raise ValueError(
                f"Unknown field(s) in SfxEntry: {bad}. "
                "Use 'volume_percentage' for per-effect volume; "
                "the prefixed forms (ambience_volume_percentage, etc.) "
                "belong in the 'defaults' block only."
            )
        return data

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


class SfxConfiguration(_DocModel):
    """Sound effects configuration for a production episode.

    Analogous to :class:`CastConfiguration` for voices. Maps parsed
    direction entry text to ElevenLabs Sound Effects API parameters.
    """

    show: str = Field(...)
    """Show title (e.g., ``"nightowls"``)."""

    season: int | None = Field(default=None)
    """Season number, or ``None`` if not declared."""

    episode: int | None = Field(default=None)
    """Episode number."""

    tag_override: str | None = Field(default=None)
    """Raw tag for non-episodic content (e.g. ``V01C03``, ``D01``) — overrides
    season/episode derivation."""

    defaults: dict = Field(default_factory=dict)
    """Shared default settings (e.g., ``prompt_influence``)."""

    effects: dict[str, SfxEntry] = Field(...)
    """Mapping of direction text to SFX entry configurations."""

    vintage_scenes: list[str] = Field(default_factory=list)
    """Scene labels whose dialogue receives the vintage audio filter
    (e.g. ``["scene-3", "scene-4"]``). Empty list = no vintage treatment."""

    @property
    def tag(self) -> str:
        """Compact tag: raw override (e.g. ``V01C03``) or derived ``S01E01`` / ``E01``."""
        if self.tag_override:
            return self.tag_override
        if self.episode is None:
            raise ValueError("SfxConfiguration requires either tag_override or episode")
        return episode_tag(self.season, self.episode)
