# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared SFX library utilities.

Provides common functions for managing a shared SFX asset library and
generating episode-specific stems.  Both ``XILU002_generate_SFX.py`` and
``XILP002_producer.py`` delegate to this module to avoid code
duplication and to ensure that each unique sound effect is generated only
once into the shared ``SFX/`` directory.

Module Attributes:
    SFX_DIR: Default path for the shared SFX asset library.
"""

import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata

from elevenlabs.client import ElevenLabs
from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TXXX, USLT, ID3NoHeaderError, PictureType
from mutagen.wave import WAVE
from pydub import AudioSegment

from xil_pipeline.log_config import get_logger
from xil_pipeline.models import SfxConfiguration, SfxEntry, get_workspace_root
from xil_pipeline.sfx_backends import ElevenLabsSfxBackend, SfxBackend

logger = get_logger(__name__)

SFX_DIR = str(get_workspace_root() / "SFX")


def sfx_dir(slug: str) -> str:
    """Return the per-show SFX directory, falling back to flat SFX/ if the slug subdir is absent."""
    root = get_workspace_root()
    per_show = root / "SFX" / slug
    return str(per_show if per_show.is_dir() else root / "SFX")

_BAR = "=" * 70


@contextlib.contextmanager
def run_banner(script_name: str | None = None):
    """Context manager that prints a start header and end trailer.

    Usage:

```bash
def main():
    with run_banner():
        ...  # all application logic
```

    Args:
        script_name: Override the script name shown in the banner.
                     Defaults to ``os.path.basename(sys.argv[0])``.
    """
    name = script_name or os.path.basename(sys.argv[0])
    start = datetime.datetime.now()

    # Logged (not printed) so the banner reaches BOTH the console — where the
    # text is unchanged, RUN renders bare like INFO — and the structured log
    # file, where it becomes the per-invocation boundary that tools like
    # xil-stem-log and xil_effort key off.  The BEGIN record carries the full
    # invocation so a log block can always be traced back to its command.
    from xil_pipeline.log_config import RUN, get_logger

    _log = get_logger(__name__)
    try:
        from xil_pipeline import __version__ as _ver
    except Exception:  # pragma: no cover - version should always import
        _ver = "?"
    argv = " ".join(sys.argv)

    # Decorative bars stay console-only (unchanged terminal appearance); the
    # BEGIN/END records are file-only (structured boundary, no terminal noise).
    _console = {"file": False}
    _file = {"console": False}

    _log.log(RUN, "", extra=_console)
    _log.log(RUN, _BAR, extra=_console)
    _log.log(RUN, f"  {name}  |  started {start.strftime('%Y-%m-%d %H:%M:%S')}", extra=_console)
    _log.log(RUN, _BAR, extra=_console)
    _log.log(RUN, "", extra=_console)
    _log.log(
        RUN,
        f'BEGIN argv="{argv}" pid={os.getpid()} ver={_ver} cwd={os.getcwd()}',
        extra=_file,
    )
    try:
        yield
    finally:
        end = datetime.datetime.now()
        elapsed = end - start
        _log.log(RUN, f"END elapsed={elapsed.total_seconds():.1f}s", extra=_file)
        _log.log(RUN, "", extra=_console)
        _log.log(RUN, _BAR, extra=_console)
        _log.log(
            RUN,
            f"  {name}  |  finished {end.strftime('%Y-%m-%d %H:%M:%S')}  ({elapsed.total_seconds():.1f}s)",
            extra=_console,
        )
        _log.log(RUN, _BAR, extra=_console)
        _log.log(RUN, "", extra=_console)


_MAX_SLUG_LEN = 180  # filesystem max is 255 bytes; leave room for .mp3 + collision suffix


def slugify_effect_key(text: str) -> str:
    """Convert direction text to a filesystem-safe slug.

    Rules:
        1. Lowercase the entire string.
        2. Replace ``': '`` (colon-space) with ``'_'`` (category separator).
        3. Replace remaining non-alphanumeric characters with ``'-'``.
        4. Collapse multiple consecutive hyphens.
        5. Strip leading/trailing hyphens.
        6. Truncate to ``_MAX_SLUG_LEN`` chars; append an 8-char SHA-256 suffix
           when truncated to avoid collisions between long similar keys.

    Examples:
        >>> slugify_effect_key("BEAT")
        'beat'
        >>> slugify_effect_key("SFX: DOOR OPENS, BELL CHIMES")
        'sfx_door-opens-bell-chimes'
    """
    if not text:
        return ""
    slug = text.lower()
    slug = slug.replace(": ", "_")
    slug = re.sub(r"[^a-z0-9_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    if len(slug) > _MAX_SLUG_LEN:
        h = hashlib.sha256(slug.encode()).hexdigest()[:8]
        slug = slug[:_MAX_SLUG_LEN].rstrip("-") + "_" + h
    return slug


def _sha256_file(path: str) -> str:
    """Return the hex-encoded SHA-256 digest of *path*, read in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── SFX stem manifest helpers ─────────────────────────────────────────────────

def _sfx_manifest_path(stems_dir: str) -> str:
    """Return the SFX stem manifest JSON path inside *stems_dir*."""
    tag = os.path.basename(stems_dir.rstrip(os.sep))
    return os.path.join(stems_dir, f"{tag}_sfx_manifest.json")


def _sfx_manifest_load(path: str) -> dict:
    """Load the SFX manifest from *path*, returning an empty manifest when absent or corrupt."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "entries": []}


def _sfx_manifest_save(path: str, manifest: dict) -> None:
    """Atomically write *manifest* to *path* via a temp-file rename."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp, path)


def _sfx_manifest_content_key(effect_key: str, sfx_type: str,
                               source_path: str | None,
                               prompt: str | None,
                               duration_seconds: float | None) -> tuple:
    """Build a deduplication key from SFX effect parameters for manifest upsert."""
    return (effect_key, sfx_type, source_path or "", prompt or "",
            round(duration_seconds, 3) if duration_seconds is not None else None)


def _sfx_manifest_upsert(manifest: dict, by_key: dict, entry: dict) -> None:
    """Insert or update *entry* in *manifest*, deduplicating by content key."""
    key = _sfx_manifest_content_key(
        entry["effect_key"], entry["sfx_type"],
        entry.get("source_path"), entry.get("prompt"),
        entry.get("duration_seconds"),
    )
    if key in by_key:
        by_key[key].update(entry)
    else:
        manifest["entries"].append(entry)
        by_key[key] = entry


def file_nonempty(path: str) -> bool:
    """Return True if *path* exists and has a non-zero size.

    Uses a single ``os.stat()`` call to avoid a TOCTOU race between
    an existence check and a separate size check.

    Args:
        path: Filesystem path to test.

    Returns:
        ``True`` if the file exists and ``st_size > 0``, ``False`` otherwise.
    """
    try:
        return os.stat(path).st_size > 0
    except OSError:
        return False


def resolve_source_path(source: str) -> str | None:
    """Resolve a declared ``source`` to a real file, or ``None`` if absent.

    Relative paths resolve against the workspace root rather than the current
    directory, so ``source: "SFX/<slug>/file.mp3"`` works regardless of where
    the command was run from.

    Accented filenames are also tried in both Unicode normalizations.  ``Í``
    can be a single codepoint (NFC) or ``I`` plus a combining acute (NFD);
    the two are different byte strings and therefore different paths, but they
    look identical, so a config written in one form silently fails to find an
    asset stored in the other.  Configs are normalized to NFC on load, but the
    filesystem may hold either — filenames created on macOS are typically NFD.

    Args:
        source: The ``source`` field from an :class:`SfxEntry`.

    Returns:
        The real path of the matched file, or ``None`` when nothing matches.
    """
    from pathlib import Path as _Path

    candidates = [source]
    for form in ("NFC", "NFD"):
        variant = unicodedata.normalize(form, source)
        if variant not in candidates:
            candidates.append(variant)

    for candidate in candidates:
        path = _Path(candidate)
        if not path.is_absolute():
            path = get_workspace_root() / path
        real = os.path.realpath(str(path))
        if os.path.isfile(real):
            return real
    return None


def shared_sfx_path(sfx_dir: str, effect_key: str, backend: str = "elevenlabs") -> str:
    """Return the shared library file path for an effect key.

    Model-generated assets are tagged with the backend name so audio from
    different generators can coexist in the same ``SFX/`` library without one
    silently shadowing another.  The default backend (``elevenlabs``) keeps the
    plain, historical filename for backward compatibility; other backends get a
    ``.<backend>`` infix.  Backend-independent assets (``silence``, ``source``
    copies) should always pass the default so they are never regenerated on a
    backend switch.

    Args:
        sfx_dir: Base directory for shared SFX assets.
        effect_key: Direction text key (e.g. ``'BEAT'``).
        backend: Generating backend name.  Only ``'elevenlabs'`` is generatable
            now; other names still resolve so trial-era assets stay reachable.

    Returns:
        Full path like ``SFX/beat.mp3`` (elevenlabs) or
        ``SFX/sfx_door-opens.audioldm2.mp3`` (a removed trial backend).
    """
    slug = slugify_effect_key(effect_key)
    suffix = "" if backend == "elevenlabs" else f".{backend}"
    return os.path.join(sfx_dir, f"{slug}{suffix}.mp3")


def tag_mp3(
    path: str,
    show: str = "Sample Show",
    title: str | None = None,
    artist: str | None = None,
    lyrics: str | None = None,
    comments: str | None = None,
    cover_art_path: str | None = None,
) -> None:
    """Write ID3 metadata tags to an MP3 file.

    Sets Album, Genre, and Year.  Optionally sets Title, Artist, Lyrics,
    and cover art (APIC front-cover frame).

    Args:
        path: Path to the MP3 file.
        show: Album name (default ``"Sample Show"``).
        title: Optional TIT2 title tag (e.g. the effect key or dialogue
            song label).
        artist: Optional TPE1 artist tag (e.g. the speaker's full name).
        lyrics: Optional USLT unsynchronised lyrics tag (full dialogue
            text).
        cover_art_path: Optional path to a PNG or JPEG image to embed as
            the front-cover APIC frame. Silently skipped if the file does
            not exist.
    """
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.add(TALB(encoding=3, text=show))
    tags.add(TCON(encoding=3, text="Podcast"))
    tags.add(TDRC(encoding=3, text=str(datetime.date.today().year)))
    if title:
        tags.add(TIT2(encoding=3, text=title))
    if artist:
        tags.add(TPE1(encoding=3, text=artist))
    if lyrics:
        tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
    if comments:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=comments))
    if cover_art_path and os.path.exists(cover_art_path):
        ext = os.path.splitext(cover_art_path)[1].lower()
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        with open(cover_art_path, "rb") as img:
            tags.add(APIC(encoding=3, mime=mime, type=PictureType.COVER_FRONT, desc="", data=img.read()))
    tags.save(path)


# ── SFX quality grading (stored in a dedicated ID3 frame) ─────────────────────
#
# A grade is recorded in each mp3's ``TXXX:XIL_GRADE`` user-text frame
# ("accurate" | "rejected"; absent = ungraded). Storing it in the file (not the
# filename) lets a verdict travel with the asset, and the pipeline can omit
# rejected pool files from production. Set via the xil-gui Audio Grading tab.

SFX_GRADE_FRAME = "XIL_GRADE"
SFX_GRADE_ACCURATE = "accurate"
SFX_GRADE_REJECTED = "rejected"
_SFX_GRADES = (SFX_GRADE_ACCURATE, SFX_GRADE_REJECTED)


def read_sfx_grade(path: str) -> str:
    """Return ``'accurate'``/``'rejected'`` from *path*'s XIL_GRADE frame, else ``''``.

    Never raises — a missing file, missing ID3 header, or unknown value all
    read as ungraded (``''``).
    """
    try:
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return ""
        frame = tags.get(f"TXXX:{SFX_GRADE_FRAME}")
        if frame and frame.text:
            val = str(frame.text[0]).strip().lower()
            return val if val in _SFX_GRADES else ""
        return ""
    except Exception:
        return ""


def write_sfx_grade(path: str, status: str) -> None:
    """Set (or clear) *path*'s XIL_GRADE frame. ``status`` in {accurate, rejected, ''}.

    All other ID3 frames are preserved; only the grade frame is replaced/removed.
    """
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall(f"TXXX:{SFX_GRADE_FRAME}")
    if status in _SFX_GRADES:
        tags.add(TXXX(encoding=3, desc=SFX_GRADE_FRAME, text=status))
    tags.save(path)


# ─── SFX edit journal ─────────────────────────────────────────────────────────
# Timeline sound edits (volume/ramps/play_duration) are saved into the SFX
# config, but generate_sfx_config() rewrites that file as a fresh skeleton
# whenever it is absent at parse time — so a cleared config would silently
# lose every hand-tuned override.  Each edit is therefore also appended to a
# sidecar journal that survives the config (and `xil remove-episode`), and
# can be replayed onto a regenerated skeleton.

# Fields the timeline edit journal records and replays.  ``source`` is here so an
# asset assignment survives a rebuild from a fresh script .md: create_sfx_config
# writes the skeleton from the script's pipe-hints, then replays this journal on
# top, so a journaled source is reinstated even when the hint is gone from the
# script.  That ordering means the journal BEATS the script hint — replay warns on
# every disagreement rather than swapping an asset silently.
SFX_EDIT_FIELDS = ("volume_percentage", "ramp_in_seconds",
                   "ramp_out_seconds", "play_duration", "source")


def sfx_edits_path(sfx_path: str) -> str:
    """Return the edit-journal path for an SFX config: ``sfx_X.json`` → ``sfx_X_edits.jsonl``."""
    base, _ = os.path.splitext(sfx_path)
    return f"{base}_edits.jsonl"


def append_sfx_edit(sfx_path: str, key: str, fields: dict) -> None:
    """Append one edit record to the journal beside *sfx_path*.

    Args:
        sfx_path: Path of the SFX config the edit was applied to.
        key: Effect key (the direction text) that was edited.
        fields: Editable-field values; ``None`` means "clear this override".
    """
    # Preserve exactly the fields given (explicit None included) so replay
    # reproduces the save's clear-vs-set semantics.
    record = {
        "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "key": key,
        "fields": dict(fields),
    }
    journal = sfx_edits_path(sfx_path)
    os.makedirs(os.path.dirname(journal) or ".", exist_ok=True)
    with open(journal, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_sfx_defaults_edit(sfx_path: str, fields: dict) -> None:
    """Append one category-defaults edit record to the journal beside *sfx_path*.

    Same append mechanics as :func:`append_sfx_edit`, but the record carries
    ``"scope": "defaults"`` and no ``"key"`` — :func:`replay_sfx_edits`
    applies it to ``data["defaults"]`` instead of a single effect entry.

    Args:
        sfx_path: Path of the SFX config the edit was applied to.
        fields: Prefixed defaults keys (e.g. ``music_volume_percentage``) to
            set; ``None`` means "clear this default".
    """
    record = {
        "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "scope": "defaults",
        "fields": dict(fields),
    }
    journal = sfx_edits_path(sfx_path)
    os.makedirs(os.path.dirname(journal) or ".", exist_ok=True)
    with open(journal, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def replay_sfx_edits(sfx_path: str, dry_run: bool = False) -> tuple[int, list[str]]:
    """Reapply journaled edits to the SFX config at *sfx_path*.

    Records are applied in journal order (last write wins), with the same
    semantics as the timeline-editor save route: ``None`` removes the
    override, any other value sets it.  Keys absent from the current
    ``effects`` dict are still created (a renamed direction leaves a
    harmless orphan) but reported so the caller can warn. Records with
    ``"scope": "defaults"`` (from :func:`append_sfx_defaults_edit`) are
    applied to ``data["defaults"]`` instead and never produce orphans;
    records without a ``"scope"`` field (all pre-existing journals) are
    treated as per-cue edits, unchanged.

    Args:
        sfx_path: SFX config to update in place.
        dry_run: When True, compute but do not write.

    Returns:
        ``(records_applied, orphan_keys)`` — ``(0, [])`` when no journal exists.
    """
    journal = sfx_edits_path(sfx_path)
    if not os.path.exists(journal):
        return 0, []

    with open(sfx_path, encoding="utf-8") as f:
        data = json.load(f)
    effects = data.setdefault("effects", {})

    applied = 0
    orphans: list[str] = []
    with open(journal, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("scope") == "defaults":
                    defaults_fields = record.get("fields", {})
                    defaults_dict = data.setdefault("defaults", {})
                    for dfield, dval in defaults_fields.items():
                        if dval is None:
                            defaults_dict.pop(dfield, None)
                        else:
                            defaults_dict[dfield] = dval
                    applied += 1
                    continue
                key = record["key"]
                fields = record["fields"]
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.warning(f"  Skipping malformed journal line {lineno} in {journal}")
                continue
            if key not in effects and key not in orphans:
                orphans.append(key)
            effect = effects.setdefault(key, {})
            for field in SFX_EDIT_FIELDS:
                if field not in fields:
                    continue
                if fields[field] is None:
                    effect.pop(field, None)
                else:
                    # A journaled source overriding a different one is the case
                    # where the journal beats a freshly-edited script hint. Never
                    # silent: the operator has to be able to see which asset won.
                    if (field == "source" and effect.get(field) is not None
                            and effect[field] != fields[field]):
                        logger.warning(
                            "  Journal overrides script hint for %r: %s -> %s "
                            "(re-run xil sfx-hydrate --force to make the script win)",
                            key, effect[field], fields[field],
                        )
                    effect[field] = fields[field]
            applied += 1

    if applied and not dry_run:
        with open(sfx_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return applied, orphans


def tag_wav(
    path: str,
    show: str = "Sample Show",
    title: str | None = None,
    artist: str | None = None,
) -> None:
    """Write ID3 metadata tags to a WAV file.

    Sets Album, Genre, and Year.  Optionally sets Title and Artist.

    Args:
        path: Path to the WAV file.
        show: Album name (default ``"Sample Show"``).
        title: Optional TIT2 title tag (e.g. the layer name).
        artist: Optional TPE1 artist tag.
    """
    wav = WAVE(path)
    if wav.tags is None:
        wav.add_tags()
    wav.tags.add(TALB(encoding=3, text=show))
    wav.tags.add(TCON(encoding=3, text="Podcast"))
    wav.tags.add(TDRC(encoding=3, text=str(datetime.date.today().year)))
    if title:
        wav.tags.add(TIT2(encoding=3, text=title))
    if artist:
        wav.tags.add(TPE1(encoding=3, text=artist))
    wav.save()


def ensure_shared_sfx(
    effect_key: str,
    effect: SfxEntry,
    sfx_dir: str,
    defaults: dict,
    client: ElevenLabs | None = None,
    show: str = "Sample Show",
    backend: SfxBackend | None = None,
) -> str:
    """Ensure the shared SFX asset exists, generating if needed.

    For ``type='silence'`` effects, generates silent audio locally via
    pydub.  For ``type='sfx'`` effects, generates audio via the supplied
    *backend* (ElevenLabs API or local AudioLDM 2).  In both cases, ID3
    metadata tags (Album, Genre, Year, Title) are written to the resulting MP3.

    Model-generated ``type='sfx'`` assets are stored under a backend-tagged
    filename (e.g. ``sfx_door-opens.audioldm2.mp3``) so audio from different
    generators can coexist; ``silence`` and ``source`` copies keep the plain
    name and are never regenerated on a backend switch.

    Args:
        effect_key: Direction text key.
        effect: The ``SfxEntry`` model instance.
        sfx_dir: Shared SFX library directory.
        defaults: Config-level defaults (e.g. ``prompt_influence``).
        client: ElevenLabs client instance.  Used only when *backend* is
            ``None`` (a default :class:`ElevenLabsSfxBackend` is built from it).
        show: Show name for the Album ID3 tag.
        backend: SFX generation backend.  Defaults to an ElevenLabs backend
            wrapping *client* for backward compatibility.

    Returns:
        The path to the shared asset file.

    Raises:
        ValueError: If the effect requires model generation but no usable
            backend/client is available.
    """
    if backend is None:
        backend = ElevenLabsSfxBackend(client)

    # Model-generated SFX assets are backend-tagged; silence and source copies
    # are backend-independent and keep the plain, historical filename.
    is_model_sfx = effect.type == "sfx" and effect.source is None
    path = shared_sfx_path(
        sfx_dir, effect_key, backend.name if is_model_sfx else "elevenlabs"
    )

    # Only skip generation when no explicit source is declared. If a source is
    # specified it must always win — a stale pool file (e.g. from another show
    # that used the same effect key) would otherwise silently shadow the source.
    if effect.source is None and file_nonempty(path):
        return path

    os.makedirs(sfx_dir, exist_ok=True)

    if effect.source is not None:
        src_real = resolve_source_path(effect.source)
        if src_real is not None:
            # Skip copy when source already IS the pool file (source path lives
            # in SFX/ and the slugified key maps to the same filename).
            if not (os.path.exists(path) and os.path.samefile(src_real, path)):
                shutil.copy2(src_real, path)
        else:
            # Source file declared but missing — fall back to model generation if
            # a prompt is available, otherwise raise an actionable error.
            if effect.prompt is None:
                raise FileNotFoundError(
                    f"Source file not found: '{effect.source}' "
                    f"(key: '{effect_key}'). "
                    "Add the file or add a 'prompt' field to generate it."
                )
            print(
                f"   [warn] source '{effect.source}' not found — "
                f"generating via {backend.name} for '{effect_key}'"
            )
            prompt_influence = effect.prompt_influence
            if prompt_influence is None:
                prompt_influence = defaults.get("prompt_influence", 0.3)
            backend.generate_to(path, effect.prompt, effect.duration_seconds, prompt_influence)
            tag_mp3(path, show=show, title=effect_key,
                    comments=getattr(backend, "asset_comment", None))
            return path
    elif effect.type == "silence":
        duration_ms = int(effect.duration_seconds * 1000)
        silence = AudioSegment.silent(duration=duration_ms)
        silence.export(path, format="mp3")
    else:
        prompt_influence = effect.prompt_influence
        if prompt_influence is None:
            prompt_influence = defaults.get("prompt_influence", 0.3)
        backend.generate_to(path, effect.prompt, effect.duration_seconds, prompt_influence)

    # A backend may attach a provenance/licence note (MMAudio's weights are
    # CC BY-NC 4.0), so the constraint travels with the file rather than living
    # only in the filename infix.
    tag_mp3(path, show=show, title=effect_key,
            comments=getattr(backend, "asset_comment", None) if backend else None)

    return path


def place_episode_stem(shared_path: str, stem_path: str) -> bool:
    """Copy a shared SFX asset to an episode stem location.

    Args:
        shared_path: Path to the shared asset in ``SFX/``.
        stem_path: Destination path in ``stems/<TAG>/``.

    Returns:
        ``True`` if the file was copied, ``False`` if the stem already
        existed on disk.
    """
    if file_nonempty(stem_path):
        return False
    os.makedirs(os.path.dirname(stem_path), exist_ok=True)
    shutil.copy2(shared_path, stem_path)
    return True


def load_sfx_entries(
    script_json_path: str,
    sfx_json_path: str,
    max_duration: float | None = None,
    direction_types: set[str] | None = None,
    local_only: bool = False,
    sfx_dir: str | None = None,
) -> list[dict]:
    """Load direction entries matched against an SFX configuration.

    Reads the parsed script and SFX config, returning only direction
    entries whose ``text`` field has a matching key in the SFX effects
    mapping.

    Args:
        script_json_path: Path to the parsed script JSON.
        sfx_json_path: Path to the SFX configuration JSON.
        max_duration: If set, exclude effects with ``duration_seconds``
            exceeding this value.
        direction_types: If set, only include entries whose
            ``direction_type`` is in this set (e.g. ``{"SFX", "BEAT"}``).
            ``None`` includes all categories.
        local_only: If ``True``, skip effects that would require an API
            call — i.e. ``type == "sfx"``, no ``source`` file, and not
            already present in the shared ``SFX/`` directory.  Silence
            entries and source-backed entries are always included.

    Returns:
        A list of SFX entry dicts with ``seq``, ``text``, ``direction_type``,
        ``stem_name``, ``sfx_type``, ``section``, and ``scene``.
    """
    if sfx_dir is None:
        sfx_dir = SFX_DIR
    with open(script_json_path, encoding="utf-8") as f:
        script_data = json.load(f)
    with open(sfx_json_path, encoding="utf-8") as f:
        sfx_data = json.load(f)

    sfx_cfg = SfxConfiguration(**sfx_data)

    sfx_entries: list[dict] = []
    for entry in script_data["entries"]:
        if entry["type"] != "direction":
            continue
        if direction_types is not None and entry.get("direction_type") not in direction_types:
            continue
        effect = sfx_cfg.effects.get(entry["text"])
        if effect is None:
            continue
        if effect.duration_seconds == 0.0:
            continue  # stop markers (FADES OUT / AMBIENCE: STOP) — no stem needed
        if max_duration is not None and effect.duration_seconds > max_duration:
            continue
        if local_only and effect.type == "sfx" and effect.source is None:
            if not file_nonempty(shared_sfx_path(sfx_dir, entry["text"])):
                logger.debug("--local-only: skipping %r (not in SFX/)", entry["text"])
                continue

        seq = entry["seq"]
        if seq < 0:
            stem_name = f"n{abs(seq):03d}_{entry['section']}_sfx"
        else:
            stem_name = f"{seq:03d}_{entry['section']}"
            if entry.get("scene"):
                stem_name += f"-{entry['scene']}"
            stem_name += "_sfx"

        sfx_entries.append({
            "seq": entry["seq"],
            "text": entry["text"],
            "direction_type": entry.get("direction_type"),
            "stem_name": stem_name,
            "sfx_type": effect.type,
            "section": entry["section"],
            "scene": entry.get("scene"),
        })

    return sfx_entries


def generate_sfx(
    sfx_entries: list[dict],
    sfx_config: dict,
    stems_dir: str,
    sfx_dir: str | None = None,
    client: ElevenLabs | None = None,
    start_from: int = 1,
    backend: SfxBackend | None = None,
) -> None:
    """Generate SFX stems via a two-phase shared-library workflow.

    **Phase 1** — For each unique effect key, ensure the shared asset
    exists in *sfx_dir* (generate via the backend or silence if missing).

    **Phase 2** — For each script entry, copy the shared asset to the
    episode stems directory with the sequence-numbered filename.

    Args:
        sfx_entries: SFX entry dicts from :func:`load_sfx_entries`.
        sfx_config: Raw SFX config dict.
        stems_dir: Episode stems output directory.
        sfx_dir: Shared SFX library directory.
        client: ElevenLabs client (used when *backend* is ``None``).
        start_from: Only process entries with ``seq >= start_from``.
        backend: SFX generation backend.  Defaults to an ElevenLabs backend
            wrapping *client*.
    """
    if sfx_dir is None:
        sfx_dir = SFX_DIR
    if backend is None:
        backend = ElevenLabsSfxBackend(client)
    os.makedirs(stems_dir, exist_ok=True)
    sfx_cfg = SfxConfiguration(**sfx_config)
    defaults = sfx_cfg.defaults
    run_started_at = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

    mf_path = _sfx_manifest_path(stems_dir)
    manifest = _sfx_manifest_load(mf_path)
    by_key: dict = {}
    for _me in manifest["entries"]:
        _k = _sfx_manifest_content_key(
            _me["effect_key"], _me["sfx_type"],
            _me.get("source_path"), _me.get("prompt"), _me.get("duration_seconds"),
        )
        by_key[_k] = _me

    entries_to_process = [e for e in sfx_entries if e["seq"] >= start_from]
    logger.info("--- SFX: Processing %d entries ---", len(entries_to_process))

    # Phase 1: ensure shared assets for unique effect keys
    unique_keys = dict.fromkeys(e["text"] for e in entries_to_process)
    shared_paths: dict[str, str] = {}
    effects: dict[str, object] = {}
    for key in unique_keys:
        effect = sfx_cfg.effects[key]
        path = ensure_shared_sfx(key, effect, sfx_dir, defaults, client,
                                show=sfx_cfg.show, backend=backend)
        shared_paths[key] = path
        effects[key] = effect
        logger.info("   Shared: %s", path)

    # Phase 2: place episode stems
    copied_count = 0
    skipped_count = 0
    omitted_count = 0
    for entry in entries_to_process:
        stem_file = os.path.join(stems_dir, f"{entry['stem_name']}.mp3")
        shared_path = shared_paths[entry["text"]]
        effect = effects[entry["text"]]

        # Omit pool files graded "rejected" in the Audio Grading tab: do not place
        # the stem, and remove any stale copy from a prior run so the effect is
        # truly absent rather than silently kept.
        if read_sfx_grade(shared_path) == SFX_GRADE_REJECTED:
            if os.path.exists(stem_file):
                os.remove(stem_file)
            logger.info("   [grade] omitted (rejected): %s", os.path.basename(shared_path))
            omitted_count += 1
            continue

        placed = place_episode_stem(shared_path, stem_file)
        if placed:
            logger.info("   Placed: %s", stem_file)
            logger.info("   SHA256: %s", _sha256_file(stem_file))
            copied_count += 1
        else:
            logger.info("   Exists: %s — skipping", stem_file)
            skipped_count += 1
        try:
            sha256_hex = _sha256_file(stem_file)
            _sfx_manifest_upsert(manifest, by_key, {
                "effect_key": entry["text"],
                "sfx_type": getattr(effect, "type", "copy"),
                "source_path": getattr(effect, "source", None),
                "prompt": getattr(effect, "prompt", None),
                "duration_seconds": getattr(effect, "duration_seconds", None),
                "sha256": sha256_hex,
                "seq_at_placement": entry["seq"],
                "stem_filename": os.path.basename(stem_file),
                "placed_at": datetime.datetime.now().isoformat(timespec="seconds"),
            })
        except Exception:
            pass

    logger.info(
        "--- SFX Complete: %d shared assets, %d placed, %d skipped, %d omitted (rejected) ---",
        len(unique_keys), copied_count, skipped_count, omitted_count,
    )
    try:
        _sfx_manifest_save(mf_path, manifest)
        logger.info("   SFX Manifest: %s (%d entries)", os.path.basename(mf_path), len(manifest["entries"]))
        snap_path = mf_path.replace(".json", f"_{run_started_at}.json")
        _sfx_manifest_save(snap_path, manifest)
        logger.info("   SFX Snapshot: %s", os.path.basename(snap_path))
    except Exception as exc:
        logger.warning("Could not write SFX manifest: %s", exc)


def dry_run_sfx(
    sfx_entries: list[dict],
    sfx_config: dict,
    stems_dir: str,
    sfx_dir: str | None = None,
    backend_name: str = "elevenlabs",
) -> None:
    """Preview SFX generation showing status and credit estimates.

    Each entry is classified as one of:
    - **EXISTS** — episode stem already in ``stems/<TAG>/``
    - **CACHED** — shared asset in ``SFX/``, will be copied (no generation)
    - **NEW** — needs generation to ``SFX/``, then copy

    Args:
        sfx_entries: SFX entry dicts from :func:`load_sfx_entries`.
        sfx_config: Raw SFX config dict.
        stems_dir: Episode stems directory.
        sfx_dir: Shared SFX library directory.
        backend_name: SFX backend (``'elevenlabs'``).  Model
            assets are matched against the backend-tagged filename, and local
            backends report generation as free instead of an API credit estimate.
    """
    if sfx_dir is None:
        sfx_dir = SFX_DIR
    sfx_cfg = SfxConfiguration(**sfx_config)
    is_local = backend_name != "elevenlabs"

    logger.info("\n%s", "=" * 70)
    logger.info("SFX DRY RUN — %d entries  (backend: %s)", len(sfx_entries), backend_name)
    logger.info("  stems dir: %s", stems_dir)
    logger.info("  shared dir: %s", sfx_dir)
    logger.info("%s\n", "=" * 70)

    # Per-category accumulators: keys are direction_type buckets + "silence"
    buckets: dict[str, dict] = {
        "MUSIC":    {"new": 0, "dur": 0.0},
        "AMBIENCE": {"new": 0, "dur": 0.0},
        "SFX":      {"new": 0, "dur": 0.0},
        "silence":  {"new": 0, "dur": 0.0},
    }
    new_count = 0
    cached_count = 0
    exists_count = 0
    missing_count = 0
    missing_sources: list[str] = []

    for entry in sfx_entries:
        effect = sfx_cfg.effects.get(entry["text"])
        if effect is None:
            continue

        stem_file = os.path.join(stems_dir, f"{entry['stem_name']}.mp3")
        is_source = effect.source is not None
        # Model-generated assets use the backend-tagged filename; silence/source
        # copies are backend-independent (plain name).
        is_model_sfx = effect.type == "sfx" and not is_source
        shared_backend = backend_name if is_model_sfx else "elevenlabs"
        shared_file = effect.source if is_source else shared_sfx_path(sfx_dir, entry["text"], shared_backend)
        # Test existence against a properly resolved path — declared sources are
        # workspace-root-relative, not CWD-relative, and an accented filename may
        # be stored in a different Unicode normalisation than the config uses.
        # ``shared_file`` stays as declared, for readable log output.
        resolved_file = resolve_source_path(effect.source) if is_source else shared_file

        if os.path.exists(stem_file):
            status = "EXISTS"
            exists_count += 1
        elif is_source and resolved_file is None:
            status = "MISSING"
            missing_count += 1
            missing_sources.append(f"  '{entry['text']}' → {shared_file}")
        elif resolved_file is not None and os.path.exists(resolved_file):
            status = "CACHED"
            cached_count += 1
        else:
            status = "   NEW"
            new_count += 1

        seq_label = f"n{abs(entry['seq']):03d}" if entry["seq"] < 0 else f"{entry['seq']:03d}"
        if effect.type == "silence":
            logger.info(
                " [%s] %s | silence | %5.1fs | %s",
                status, seq_label, effect.duration_seconds, entry["text"],
            )
            if status == "   NEW":
                buckets["silence"]["new"] += 1
                buckets["silence"]["dur"] += effect.duration_seconds
        elif is_source:
            logger.info(
                " [%s] %s | copy    |         | ~    0 credits | %s",
                status, seq_label, entry["text"],
            )
            logger.info("            source: %s", shared_file)
        else:
            credits = int(effect.duration_seconds * 40)
            bucket_key = entry.get("direction_type") or "SFX"
            if bucket_key not in buckets:
                bucket_key = "SFX"
            if status == "   NEW":
                buckets[bucket_key]["new"] += 1
                buckets[bucket_key]["dur"] += effect.duration_seconds
            if is_local:
                logger.info(
                    " [%s] %s | sfx     | %5.1fs | local (free)  | %s",
                    status, seq_label, effect.duration_seconds, entry["text"],
                )
            else:
                logger.info(
                    " [%s] %s | sfx     | %5.1fs | ~%5d credits | %s",
                    status, seq_label, effect.duration_seconds, credits, entry["text"],
                )
            logger.info("            prompt: %s", effect.prompt)

        logger.info("            stem: %s.mp3", entry["stem_name"])
        if not is_source:
            logger.info("            shared: %s", os.path.basename(shared_file))
        logger.info("")

    total_new_dur = sum(b["dur"] for b in buckets.values())
    total_credits = int(total_new_dur * 40)
    logger.info("%s", "=" * 70)
    logger.info(
        "SUMMARY: %d total — %d new, %d cached, %d on disk, %d MISSING  (backend: %s)",
        len(sfx_entries), new_count, cached_count, exists_count, missing_count, backend_name,
    )
    for cat in ("MUSIC", "AMBIENCE", "SFX"):
        b = buckets[cat]
        if b["new"] or any(
            (entry.get("direction_type") or "SFX") == cat
            for entry in sfx_entries
        ):
            if is_local:
                logger.info("  %-9s: %3d new, %6.1fs  (local, free)", cat, b["new"], b["dur"])
            else:
                cred = int(b["dur"] * 40)
                logger.info("  %-9s: %3d new, %6.1fs, ~%6d credits", cat, b["new"], b["dur"], cred)
    if buckets["silence"]["new"]:
        logger.info("  %-9s: %3d new  (free)", "silence", buckets["silence"]["new"])
    if is_local:
        logger.info(
            "  %-9s: %3d,  %.1fs  (local generation — no API credits)",
            "TOTAL NEW", new_count, total_new_dur,
        )
    else:
        logger.info(
            "  %-9s: %3d,  %.1fs, ~%d credits  (silence & cached are free)",
            "TOTAL NEW", new_count, total_new_dur, total_credits,
        )
    if missing_sources:
        logger.error("%d source file(s) declared but not found:", len(missing_sources))
        for msg in missing_sources:
            logger.error(msg)
    logger.info("%s\n", "=" * 70)
