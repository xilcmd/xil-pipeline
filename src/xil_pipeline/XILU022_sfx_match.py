# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XILU022 — Reconcile drifted SFX cues against the existing asset library.

Answers one question: **a cue points at a file that is not on disk — does that
sound already exist under a different name?**

``xil produce`` refuses to start when any cue declares a ``source`` that does
not resolve, and ``xil sfx-impact`` reports those cues as tier ``MISSING``.
Often they are not missing sounds at all, only misnamed references.  Two forces
pull a reference away from its file:

* the scriptwriter writes *loose*, near-duplicate direction text across
  iterations — ``xil discover-sfx --export-kit`` emits the ``[TITLE | file.mp3]``
  cheatsheet precisely so a human can consolidate them, but when the scriptwriter
  cannot find a line it invents a ``"NEW STEM NEEDED: <slug>.mp3"`` placeholder; and
* the creative edits direction text after generation, before the pipeline sees it.

Nothing else in the pipeline goes back and asks whether the sound already exists.
This does.

**How the match is made.**  Every asset written by :func:`~xil_pipeline.sfx_common.tag_mp3`
carries its *originating cue text* in the ID3 ``TIT2`` frame, which makes the
library self-describing — far better evidence than a filename.  Each missing cue
is scored against every pool asset's title (falling back to its generation prompt,
then its filename) on:

* **coverage** — ``|cue ∩ candidate| / |cue|``, how much of what the cue asks for
  the candidate contains.  This leads, because pool titles are much more verbose
  than cue text and a symmetric measure punishes the right answers for it.
* **jaccard** — breaks ties, and penalises a candidate that is mostly about
  something else.

Two guards keep a plausible-looking wrong answer from being applied:

* a **category gate** — an ``AMBIENCE:`` cue never matches a ``MUSIC`` asset; and
* a **margin rule** — a top score with no daylight over the runner-up is a review
  case, not an automatic one.  Several cues in practice have three candidates at
  identical coverage, and only a human can pick between them.

**Repair.**  ``--apply`` copies the matched asset into the show's own pool under
the *cue's* slug, re-tags the copy's title to the cue text, and journals the new
``source``.  Naming the copy after the cue is what makes the repair permanent:
afterwards the exact-slug rule holds, so a re-run reports ``EXACT``, and the next
``xil discover-sfx --export-kit`` lists the asset under the cue text so the
scriptwriter finds it unaided instead of inventing another placeholder.

Usage::

    xil sfx-match --show deadair                        # report only
    xil sfx-match --show deadair --emit-hints           # paste-ready script block
    xil sfx-match --show deadair --apply --dry-run      # inspect every write
    xil sfx-match --show deadair --apply                # EXACT + STRONG
    xil sfx-match --show deadair --apply --accept-review
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from mutagen.id3 import COMM, ID3, TIT2, ID3NoHeaderError

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root, resolve_slug
from xil_pipeline.sfx_common import (
    append_sfx_edit,
    run_banner,
    sfx_dir,
    shared_sfx_path,
    slugify_effect_key,
)
from xil_pipeline.XILU005_discover_SFX import fetch_local_records
from xil_pipeline.XILU021_sfx_impact import classify_placement, discover_configs

logger = get_logger(__name__)

SCRIPT_NAME = "XILU022_sfx_match"

TIERS = ("EXACT", "STRONG", "REVIEW", "NONE")

# Tokens that carry no discriminating signal — category prefixes that the
# category gate already handles, the placeholder vocabulary the scriptwriter
# emits, and connectives.
#
# Deliberately NOT stopped: "up", "out", "down", "off", "back".  They read like
# filler but do the real work in cue text — "THEME, UP BRIEFLY, THEN OUT" and
# "MUG SET DOWN" lose their meaning without them.
STOPWORDS = frozenset({
    "sfx", "ambience", "ambient", "music", "beat", "new", "stem", "needed",
    "mp3", "wav", "elevenlabs",
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "it",
    "its", "of", "on", "or", "s", "the", "then", "to", "with",
})

# Coverage a candidate must reach before it is worth a human's attention at all.
REVIEW_FLOOR = 0.5
# Thresholds a candidate must clear to be applied without review.
STRONG_COVERAGE = 0.8
STRONG_JACCARD = 0.4
# Composite-score daylight the leader needs over the runner-up.  Without this a
# three-way tie at perfect coverage would apply an arbitrary one of the three.
STRONG_MARGIN = 0.15
# Weight on jaccard in the composite ranking score.  Small: coverage decides the
# ordering, jaccard only separates candidates coverage cannot.
JACCARD_WEIGHT = 0.25

CSV_COLUMNS = [
    "show", "episode", "cue", "current_source", "tier", "rank",
    "candidate", "candidate_title", "candidate_scope", "coverage", "jaccard",
    "score", "duration_s", "also_in",
]

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


# ── text handling ─────────────────────────────────────────────────────────────

def _singular(token: str) -> str:
    """Fold a simple plural onto its singular so CHAIRS matches CHAIR.

    Applied identically to both sides of every comparison, so the crude cases it
    gets wrong (``glass`` is protected by the ``ss`` guard, ``keys`` becomes
    ``key``) cost nothing — the only effect is that two spellings of one word
    stop being treated as different words.
    """
    if len(token) >= 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> set[str]:
    """Return the lowercase, stopword-stripped, singularised token set of *text*."""
    return {_singular(t) for t in _TOKEN_SPLIT.split(text.lower())
            if t and t not in STOPWORDS}


def score_pair(cue_tokens: set[str], cand_tokens: set[str]) -> tuple[float, float]:
    """Return ``(coverage, jaccard)`` for a cue against a candidate.

    Coverage is asymmetric on purpose: a verbose library title that happens to
    contain everything the cue asks for is a good match, even though the extra
    words drag its jaccard down.
    """
    if not cue_tokens or not cand_tokens:
        return 0.0, 0.0
    shared = len(cue_tokens & cand_tokens)
    return shared / len(cue_tokens), shared / len(cue_tokens | cand_tokens)


def composite(coverage: float, jaccard: float) -> float:
    """Blend coverage and jaccard into the single value candidates rank by."""
    return coverage + JACCARD_WEIGHT * jaccard


def placement_from_filename(filename: str) -> str:
    """Classify an untitled asset by its filename prefix.

    Mirrors the bucketing ``xil discover-sfx --export-kit`` uses when it sorts
    the pipe-hint cheatsheet, covering both the slug form (``ambience_…``) and
    the ElevenLabs short-code form (``AMBGras-…``).
    """
    f = filename.lower()
    if f.startswith(("ambience_", "ambience-", "amb-", "amb_")):
        return "AMBI(bg)"
    if f.startswith("amb") and len(f) > 3 and f[3].isalpha():
        return "AMBI(bg)"
    if f.startswith(("music_", "music-", "mus-", "mus_")):
        return "MUSIC(bg)"
    if f.startswith("mus") and len(f) > 3 and f[3].isalpha():
        return "MUSIC(bg)"
    if f.startswith("beat"):
        return "BEAT(fg)"
    return "SFX(fg)"


# ── pool ──────────────────────────────────────────────────────────────────────

@dataclass
class PoolAsset:
    """One deduplicated asset in the shared SFX library."""

    filename: str
    path: str
    scope: str                      # show slug, or "" for the flat SFX/ root
    title: str
    prompt: str
    duration_s: float | None
    size_bytes: int
    placement: str
    tokens: set[str] = field(default_factory=set)
    also_in: list[str] = field(default_factory=list)

    @property
    def match_text(self) -> str:
        """The text this asset is matched on: title, else prompt, else filename."""
        return self.title or self.prompt or self.filename


def _scope_rank(scope: str, own_show: str | None) -> int:
    """Order scopes by preference: the cue's own show, then root, then siblings."""
    if own_show and scope == own_show:
        return 0
    return 1 if scope == "" else 2


def _is_canonical_name(asset: "PoolAsset") -> bool:
    """True when the filename is the slug form the pipeline itself would write.

    Library filenames and titles have drifted apart: an asset titled
    ``SFX: CHAIR SCRAPING — ELENA STANDING`` can sit in a file called
    ``SFX-_CHAIRS_—_SOFT_SCRAPING,_SITTING_DOWN.mp3``.  When several copies of one
    asset are collapsed, the copy whose name still agrees with its title is the
    one to surface and to copy from.
    """
    return bool(asset.title) and asset.filename == slugify_effect_key(asset.title) + ".mp3"


def _asset_from_record(rec: dict) -> "PoolAsset":
    """Build a scored-ready :class:`PoolAsset` from a discover-sfx record."""
    title = (rec.get("title") or "").strip()
    asset = PoolAsset(
        filename=rec["filename"],
        path=rec["path"],
        scope=rec.get("show") or "",
        title=title,
        prompt=(rec.get("prompt") or "").strip(),
        duration_s=rec.get("duration_seconds"),
        size_bytes=rec.get("size_bytes") or 0,
        placement=(classify_placement(title) if title
                   else placement_from_filename(rec["filename"])),
    )
    asset.tokens = tokenize(asset.match_text)
    return asset


def load_assets(workspace: Path) -> list[PoolAsset]:
    """Read every ``.mp3`` under ``SFX/`` once, tags and all.

    Split from :func:`build_pool` because reading ID3 and MP3 headers for a
    workspace-sized library takes minutes over a network mount, while the
    per-show dedupe that follows is instant — a workspace sweep must not pay the
    scan cost once per show.
    """
    return [_asset_from_record(rec)
            for rec in fetch_local_records(str(workspace / "SFX"))]


def build_pool(assets: list[PoolAsset], own_show: str | None = None) -> list[PoolAsset]:
    """Deduplicate *assets* into one entry per distinct sound.

    The library is heavily duplicated — the same asset routinely exists at
    ``SFX/x.mp3``, ``SFX/the413/x.mp3`` and ``SFX/deadair/x.mp3``, which would
    otherwise fill every candidate list with three copies of one sound.  Assets
    are grouped by ``(match text, duration)``; the winner is the copy in the most
    preferred scope, preferring one whose filename still matches its title.  The
    scopes of the copies it stands in for are recorded as ``also_in``.
    """
    groups: dict[tuple[str, float | None], list[PoolAsset]] = {}
    for asset in assets:
        groups.setdefault((" ".join(sorted(asset.tokens)), asset.duration_s), []).append(asset)

    pool: list[PoolAsset] = []
    for members in groups.values():
        members.sort(key=lambda a: (_scope_rank(a.scope, own_show),
                                    not _is_canonical_name(a),
                                    len(a.filename), a.filename))
        # Copy rather than mutate: the same PoolAsset objects are shared across
        # every show's pool, and also_in is computed per show.
        pool.append(replace(members[0],
                            also_in=sorted({m.scope or "SFX/" for m in members[1:]})))
    return pool


def build_exact_index(assets: list[PoolAsset],
                      own_show: str | None = None) -> dict[str, PoolAsset]:
    """Map filename → preferred copy, over ALL assets rather than the deduped pool.

    Deliberately not derived from :func:`build_pool`: dedupe collapses copies by
    content, and the copy it discards may be the very one carrying the slug name
    a cue resolves to.  ``SFX: PHONE SCREEN TAP`` has an exact
    ``sfx_phone-screen-tap.mp3`` on disk, but it shares a title with
    ``SFX-_PHONE_SCREEN_TAP,_SEND_TONE.mp3`` and lost the group — searching the
    deduped pool reported REVIEW for a cue the library already answers exactly.
    """
    index: dict[str, PoolAsset] = {}
    for asset in assets:
        current = index.get(asset.filename)
        if current is None or (_scope_rank(asset.scope, own_show)
                               < _scope_rank(current.scope, own_show)):
            index[asset.filename] = asset
    return index


# ── matching ──────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    """A scored pool asset proposed for a cue."""

    asset: PoolAsset
    coverage: float
    jaccard: float

    @property
    def score(self) -> float:
        """The composite value this candidate was ranked by."""
        return composite(self.coverage, self.jaccard)


@dataclass
class CueMatch:
    """One unresolvable cue and the candidates found for it."""

    show: str
    episode: str
    cue: str
    current_source: str
    config_path: str
    tier: str = "NONE"
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def best(self) -> Candidate | None:
        """The top-ranked candidate, or ``None`` when nothing cleared the floor."""
        return self.candidates[0] if self.candidates else None

    def rows(self) -> list[dict]:
        """Return CSV rows — one per candidate, or one bare row when there are none."""
        base = {
            "show": self.show, "episode": self.episode, "cue": self.cue,
            "current_source": self.current_source, "tier": self.tier,
        }
        if not self.candidates:
            return [{**base, "rank": "", "candidate": "", "candidate_title": "",
                     "candidate_scope": "", "coverage": "", "jaccard": "",
                     "score": "", "duration_s": "", "also_in": ""}]
        return [
            {**base,
             "rank": i,
             "candidate": c.asset.filename,
             "candidate_title": c.asset.match_text,
             "candidate_scope": c.asset.scope or "SFX/",
             "coverage": round(c.coverage, 2),
             "jaccard": round(c.jaccard, 2),
             "score": round(c.score, 3),
             "duration_s": "" if c.asset.duration_s is None else c.asset.duration_s,
             "also_in": ", ".join(c.asset.also_in)}
            for i, c in enumerate(self.candidates, 1)
        ]


def match_cue(
    cue: str,
    pool: list[PoolAsset],
    exact_index: dict[str, PoolAsset] | None = None,
    top: int = 3,
    min_coverage: float = REVIEW_FLOOR,
) -> tuple[str, list[Candidate]]:
    """Score *cue* against *pool* and return ``(tier, ranked candidates)``.

    An exact slug hit short-circuits: the library already holds this cue's asset
    under the name the pipeline would itself generate, so no scoring is needed.
    *exact_index* covers copies dedupe removed from *pool*; without it the lookup
    falls back to the pool alone.
    """
    exact_name = slugify_effect_key(cue) + ".mp3"
    hit = (exact_index or {}).get(exact_name)
    if hit is None:
        hit = next((a for a in pool if a.filename == exact_name), None)
    if hit is not None:
        return "EXACT", [Candidate(hit, 1.0, 1.0)]

    cue_tokens = tokenize(cue)
    wanted = classify_placement(cue)
    scored = [
        Candidate(asset, cov, jac)
        for asset in pool
        if asset.placement == wanted
        for cov, jac in (score_pair(cue_tokens, asset.tokens),)
        if cov >= min_coverage
    ]
    scored.sort(key=lambda c: (-c.score, c.asset.filename))
    scored = scored[:top]

    if not scored:
        return "NONE", []

    leader = scored[0]
    margin = leader.score - (scored[1].score if len(scored) > 1 else 0.0)
    if (leader.coverage >= STRONG_COVERAGE
            and leader.jaccard >= STRONG_JACCARD
            and margin >= STRONG_MARGIN):
        return "STRONG", scored
    return "REVIEW", scored


def _source_missing(source: str | None, workspace: Path) -> bool:
    """True when *source* is set but does not resolve to a file on disk."""
    if not source:
        return False
    path = Path(source)
    if not path.is_absolute():
        path = workspace / source
    return not path.is_file()


def analyze(
    workspace: Path,
    show: str | None = None,
    episode: str | None = None,
    top: int = 3,
    min_coverage: float = REVIEW_FLOOR,
) -> tuple[list[CueMatch], int]:
    """Find every cue with an unresolvable source and match it against the pool.

    Returns:
        ``(matches, configs_scanned)``.
    """
    configs = discover_configs(workspace, show=show, episode=episode)
    pools: dict[str, list[PoolAsset]] = {}
    indexes: dict[str, dict[str, PoolAsset]] = {}
    assets: list[PoolAsset] | None = None
    matches: list[CueMatch] = []

    for slug, tag, path in configs:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("  Skipping %s — %s", path, exc)
            continue

        for cue, effect in (data.get("effects") or {}).items():
            source = (effect or {}).get("source")
            if not _source_missing(source, workspace):
                continue
            if assets is None:
                # Deferred so a workspace where every source resolves never pays
                # for a full library scan.
                logger.info("  Indexing the SFX library…")
                assets = load_assets(workspace)
                logger.info("  Indexed %d asset(s)", len(assets))
            if slug not in pools:
                pools[slug] = build_pool(assets, own_show=slug)
                indexes[slug] = build_exact_index(assets, own_show=slug)
            tier, candidates = match_cue(cue, pools[slug], indexes[slug],
                                         top=top, min_coverage=min_coverage)
            matches.append(CueMatch(
                show=slug, episode=tag, cue=cue, current_source=source,
                config_path=str(path), tier=tier, candidates=candidates,
            ))

    return matches, len(configs)


# ── repair ────────────────────────────────────────────────────────────────────

def _retag_copy(dest: str, cue: str, provenance: str) -> None:
    """Point the copy's title at *cue* and record where it came from.

    Only ``TIT2`` and ``COMM`` are touched, so the album, artist, generation
    prompt (``USLT``) and grade frame all survive the copy untouched.  Retitling
    is the step that makes the repair self-sustaining: the next
    ``xil discover-sfx --export-kit`` lists this file under the cue text, so the
    scriptwriter can find it without a human re-consolidating the hint.
    """
    try:
        tags = ID3(dest)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("TIT2")
    tags.add(TIT2(encoding=3, text=cue))
    tags.delall("COMM")
    tags.add(COMM(encoding=3, lang="eng", desc="", text=provenance))
    tags.save(dest)


def _write_config_source(config_path: str, cue: str, source: str) -> None:
    """Set the cue's ``source`` in the config on disk.

    Writing the config as well as the journal follows the Timeline modal's save
    path (``POST /xil/update-sfx``): config first, journal second.  The journal
    alone would not help — ``xil produce`` reads the config, so an unblocked
    render would still need ``xil sfx-restore`` run by hand.
    """
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("effects", {}).setdefault(cue, {})["source"] = source
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def apply_match(
    match: CueMatch,
    workspace: Path,
    dry_run: bool = False,
) -> str | None:
    """Copy the best candidate into the show's pool, then write and journal it.

    The destination is named for the *cue*, not the source asset, so a re-run
    resolves it via the exact-slug rule and the assignment stops drifting.

    Returns:
        The workspace-relative source path written, or ``None`` when there was
        nothing to apply.
    """
    best = match.best
    if best is None:
        return None

    dest = shared_sfx_path(sfx_dir(match.show), match.cue)
    rel_dest = os.path.relpath(dest, workspace)
    rel_src = os.path.relpath(best.asset.path, workspace)

    if os.path.abspath(dest) == os.path.abspath(best.asset.path):
        # The library already holds it under the cue's own name; only the
        # config's stale reference needs correcting.
        action = "already in place"
    elif os.path.exists(dest):
        action = "destination exists, reusing"
    else:
        action = f"copy {rel_src}"
        if not dry_run:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(best.asset.path, dest)
            _retag_copy(dest, match.cue,
                        f"copied from {rel_src} by xil sfx-match")

    logger.info("    %s%s  →  %s  (%s)",
                "[dry-run] " if dry_run else "", match.cue[:44], rel_dest, action)

    if not dry_run:
        rel_dest = rel_dest.replace(os.sep, "/")
        _write_config_source(match.config_path, match.cue, rel_dest)
        append_sfx_edit(match.config_path, match.cue, {"source": rel_dest})
    return rel_dest


# ── output ────────────────────────────────────────────────────────────────────

def write_csv(matches: list[CueMatch], stream) -> None:
    """Write the per-candidate table to an open text stream."""
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for match in matches:
        writer.writerows(match.rows())


def render_hints(matches: list[CueMatch], workspace: Path) -> str:
    """Render a paste-ready pipe-hint block for the script ``.md``.

    Uses the ``[CUE | file.mp3]`` form ``xil discover-sfx --export-kit`` writes,
    so the block drops straight into a script the same way a cheatsheet line
    does.  Runner-up candidates are emitted as commented alternates — most cues
    have several plausible matches and the choice is the reviewer's.
    """
    today = dt.date.today().isoformat()
    out = [
        "# SFX Pipe-Hint Repairs",
        f"# Generated {today} by xil sfx-match",
        "# Replace the matching direction line in the script with the [CUE | file] line.",
        "# Alternates are listed beneath each match — swap in whichever is right.",
        "",
    ]
    for tier in ("EXACT", "STRONG", "REVIEW"):
        rows = [m for m in matches if m.tier == tier]
        if not rows:
            continue
        out += [f"## {tier} ({len(rows)})", ""]
        for match in rows:
            dest = os.path.basename(shared_sfx_path(sfx_dir(match.show), match.cue))
            out.append(f"[{match.cue} | {dest}]")
            for cand in match.candidates:
                rel = os.path.relpath(cand.asset.path, workspace).replace(os.sep, "/")
                out.append(f"#   cov {cand.coverage:.2f}  “{cand.asset.match_text}”")
                out.append(f"#     {rel}")
            out.append("")

    none_rows = [m for m in matches if m.tier == "NONE"]
    if none_rows:
        out += [f"## NONE — no existing asset, needs generation ({len(none_rows)})", ""]
        out += [f"# {m.show}/{m.episode}  {m.cue}" for m in none_rows]
        out.append("")
    return "\n".join(out)


def log_summary(matches: list[CueMatch], configs_scanned: int) -> None:
    """Log the per-show tier tally and the candidates a human needs to judge."""
    by_show: dict[str, dict[str, int]] = {}
    for match in matches:
        tiers = by_show.setdefault(match.show, dict.fromkeys(TIERS, 0))
        tiers[match.tier] += 1

    logger.info("")
    logger.info(f"  Scanned {configs_scanned} SFX config(s), "
                f"{len(matches)} cue(s) with an unresolvable source")
    if not matches:
        logger.info("")
        logger.info("  Every declared source resolves — nothing to match.")
        return

    logger.info("")
    header = f"  {'show':<24} {'EXACT':>7} {'STRONG':>7} {'REVIEW':>7} {'NONE':>7}"
    logger.info(header)
    logger.info("  " + "-" * (len(header) - 2))
    totals = dict.fromkeys(TIERS, 0)
    for slug, tiers in sorted(by_show.items()):
        logger.info(f"  {slug:<24} {tiers['EXACT']:>7} {tiers['STRONG']:>7} "
                    f"{tiers['REVIEW']:>7} {tiers['NONE']:>7}")
        for tier in TIERS:
            totals[tier] += tiers[tier]
    logger.info("  " + "-" * (len(header) - 2))
    logger.info(f"  {'TOTAL':<24} {totals['EXACT']:>7} {totals['STRONG']:>7} "
                f"{totals['REVIEW']:>7} {totals['NONE']:>7}")

    review = [m for m in matches if m.tier == "REVIEW"]
    if review:
        logger.info("")
        logger.info(f"  {len(review)} cue(s) need a human decision:")
        for match in review[:15]:
            best = match.best
            # The title, not the filename: an asset titled "SFX: CHAIR SCRAPING
            # — ELENA STANDING" lives in a file called
            # "SFX-_CHAIRS_—_SOFT_SCRAPING,_SITTING_DOWN.mp3", and showing only
            # the filename makes a correct match look like a bad one.
            logger.info(f"    {match.cue[:42]:<42}  cov {best.coverage:.2f}  "
                        f"“{best.asset.match_text[:52]}”")
        if len(review) > 15:
            logger.info(f"    … and {len(review) - 15} more — see the CSV")

    nothing = [m for m in matches if m.tier == "NONE"]
    if nothing:
        logger.info("")
        logger.info(f"  {len(nothing)} cue(s) have no existing asset and need generation.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-sfx-match."""
    parser = argparse.ArgumentParser(
        prog="xil-sfx-match",
        description=(
            "Find existing SFX library assets for cues whose declared source is "
            "not on disk — the cues that make 'xil produce' refuse to start. "
            "Reports by default; --apply copies the match into the show's pool "
            "and journals the new source."
        ),
    )
    parser.add_argument("--show", default=None,
                        help="Restrict to one show slug (default: every show in the workspace)")
    parser.add_argument("--episode", "--tag", dest="episode", default=None,
                        help="Restrict to one episode tag (e.g. S01E01)")
    parser.add_argument("--top", type=int, default=3,
                        help="Candidates to report per cue (default: 3)")
    parser.add_argument("--min-coverage", type=float, default=REVIEW_FLOOR,
                        help=f"Coverage a candidate must reach to be reported "
                             f"(default: {REVIEW_FLOOR})")
    parser.add_argument("--output", default=None,
                        help="CSV output path, or '-' for stdout "
                             "(default: reports/sfx_match_<date>.csv)")
    parser.add_argument("--emit-hints", nargs="?", const="", default=None,
                        dest="emit_hints", metavar="PATH",
                        help="Also write a paste-ready pipe-hint block "
                             "(default path: reports/sfx_match_hints_<date>.md)")
    parser.add_argument("--apply", action="store_true",
                        help="Copy each accepted match into the show's SFX pool under "
                             "the cue's own slug, retitle the copy, and journal the new "
                             "source. Acts on EXACT and STRONG only unless "
                             "--accept-review is given.")
    parser.add_argument("--accept-review", action="store_true",
                        help="Widen --apply to REVIEW matches. Read the CSV first — a "
                             "REVIEW tier means the top candidate had no clear margin "
                             "over its runners-up.")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --apply, report every copy and journal record without writing")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress the console summary (CSV only)")
    return parser


def _run(args) -> None:
    """Do the work. Split out so ``main`` can choose whether to wrap it."""
    workspace = get_workspace_root()
    show = args.show
    if show is None and args.episode:
        show = resolve_slug(None)

    to_stdout = args.output == "-"
    matches, configs_scanned = analyze(
        workspace, show=show, episode=args.episode,
        top=args.top, min_coverage=args.min_coverage,
    )

    if not configs_scanned:
        scope = f"show={show}" if show else "workspace"
        msg = f"No SFX configs found ({scope}) under {workspace / 'configs'}"
        print(msg, file=sys.stderr) if to_stdout else logger.error(msg)
        sys.exit(1)

    date = dt.date.today().isoformat()
    if to_stdout:
        write_csv(matches, sys.stdout)
    else:
        out_path = Path(args.output) if args.output else (
            workspace / "reports" / f"sfx_match_{date}.csv")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            write_csv(matches, f)
        logger.info(f"  Wrote {out_path} ({len(matches)} cue(s))")

    if args.emit_hints is not None:
        hints_path = Path(args.emit_hints) if args.emit_hints else (
            workspace / "reports" / f"sfx_match_hints_{date}.md")
        hints_path.parent.mkdir(parents=True, exist_ok=True)
        hints_path.write_text(render_hints(matches, workspace), encoding="utf-8")
        msg = f"Wrote {hints_path}"
        print(msg, file=sys.stderr) if to_stdout else logger.info(f"  {msg}")

    if args.apply:
        accepted = ("EXACT", "STRONG", "REVIEW") if args.accept_review else ("EXACT", "STRONG")
        todo = [m for m in matches if m.tier in accepted and m.best]
        logger.info("")
        logger.info("  %sApplying %d match(es) [%s]:",
                    "[dry-run] " if args.dry_run else "", len(todo), ", ".join(accepted))
        for match in todo:
            apply_match(match, workspace, dry_run=args.dry_run)
        if args.dry_run and todo:
            logger.info("  Re-run without --dry-run to apply.")

    if not args.quiet and not to_stdout:
        log_summary(matches, configs_scanned)


def main() -> None:
    """CLI entry point for SFX library reconciliation."""
    configure_logging()
    args = get_parser().parse_args()

    # ``--output -`` makes stdout the CSV stream, so the run banner (which the
    # console handler writes to stdout) would corrupt it.
    if args.output == "-":
        _run(args)
    else:
        with run_banner(SCRIPT_NAME):
            _run(args)


if __name__ == "__main__":
    main()
