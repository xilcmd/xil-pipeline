# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XILU021 — SFX source-clipping impact analysis.

Answers one question across a whole workspace: **which source-backed cues are
being cut short, and by how much?**

``duration_seconds`` means two different things depending on the cue. For an
API-generated effect it is the requested generation length. For a ``source=``
cue it *clips the file at mix time* — and the parser writes a default of ``5.0``
into every skeleton entry, so a 65-second music bed dropped into a hinted cue
plays for five seconds unless someone notices. That surprise is what this tool
inventories.

The arithmetic mirrors :mod:`xil_pipeline.mix_common` exactly (see
``collect_stem_plans``, which converts ``duration_seconds`` into a
``play_duration`` percentage for source cues) so the report cannot drift from
what the mixer actually does:

* ``loop: true`` — a bed that tiles to fill its span; never clipped.
* explicit ``play_duration`` — deliberate trim, takes precedence.
* ``duration_seconds > 0`` — clips to that many seconds.
* ``duration_seconds == 0`` — plays the source full-length.

Each impacted cue is graded into a tier and paired with the concrete config
change that would un-clip it. **Nothing is ever written to a config** — this is
a decision sheet, not a migration.

The recommended fix is ``play_duration: 100``, not ``duration_seconds: 0``.
Both play the whole file, but only ``play_duration`` is journaled in
``SFX_EDIT_FIELDS``, so only it survives a skeleton rebuild — a
``duration_seconds`` edit reverts to the parser's ``5.0`` default and silently
re-clips a cue the creative had already approved.

Usage::

    xil sfx-impact                                  # every show in the workspace
    xil sfx-impact --show thewoonsocketwonders      # one show
    xil sfx-impact --episode S01E01 --show the413   # one episode
    xil sfx-impact --output - --quiet               # CSV to stdout for piping
    xil sfx-impact --html reports/impact.html       # standalone review page
"""

import argparse
import csv
import datetime as dt
import html
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root, resolve_slug
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

SCRIPT_NAME = "XILU021_sfx_impact"

# Path → duration in milliseconds.  Injected so tests can measure without
# touching the filesystem; production passes mix_common._mp3_duration_ms.
DurationProbe = Callable[[str], float]

# Tier thresholds in seconds of lost audio.  A cue losing under a tenth of a
# second is clipping only in the arithmetic sense; under three seconds is a
# judgement call; anything more is a cue the creative should hear.
NOCHANGE_S = 0.1
MINOR_S = 3.0

TIERS = ("1-nochange", "2-minor", "3-review", "EXCLUDED", "MISSING")

CSV_COLUMNS = [
    "show", "episode", "cue", "source_file", "duration_seconds", "play_duration",
    "loop", "natural_s", "plays_now_s", "delta_s", "lost_pct", "tier",
    "placement", "remediation", "note",
]


@dataclass
class CueImpact:
    """One source-backed cue, measured against its file on disk."""

    show: str
    episode: str
    cue: str
    source_file: str
    duration_seconds: float | None = None
    play_duration: float | None = None
    loop: bool = False
    natural_s: float | None = None
    plays_now_s: float | None = None
    delta_s: float | None = None
    lost_pct: float | None = None
    tier: str = "EXCLUDED"
    placement: str = "SFX(fg)"
    remediation: str = ""
    note: str = ""

    def row(self) -> dict:
        """Return the CSV row form, with floats rounded for readability."""
        d = asdict(self)
        for key in ("natural_s", "plays_now_s", "delta_s", "lost_pct"):
            if d[key] is not None:
                d[key] = round(d[key], 1)
        return {k: ("" if d[k] is None else d[k]) for k in CSV_COLUMNS}


@dataclass
class ImpactReport:
    """Aggregate result of a sweep."""

    impacts: list[CueImpact] = field(default_factory=list)
    configs_scanned: int = 0

    @property
    def actionable(self) -> list[CueImpact]:
        """Cues that are actually losing audio (tier 2 or 3)."""
        return [i for i in self.impacts if i.tier in ("2-minor", "3-review")]

    def tally(self) -> dict[str, int]:
        """Return a tier → count mapping covering every known tier."""
        counts = dict.fromkeys(TIERS, 0)
        for impact in self.impacts:
            counts[impact.tier] = counts.get(impact.tier, 0) + 1
        return counts

    def by_show(self) -> dict[str, dict[str, int]]:
        """Return show → tier → count, for the console summary."""
        out: dict[str, dict[str, int]] = {}
        for impact in self.impacts:
            tiers = out.setdefault(impact.show, dict.fromkeys(TIERS, 0))
            tiers[impact.tier] = tiers.get(impact.tier, 0) + 1
        return out


def classify_placement(cue: str) -> str:
    """Classify a cue key by where it sits in the mix.

    Background cues (music beds, ambience) tolerate clipping very differently
    from foreground one-shots, so the tier alone is not enough to triage.

    Args:
        cue: The SFX config key (e.g. ``"OUTRO MUSIC"``, ``"SFX: DOOR"``).

    Returns:
        One of ``"MUSIC(bg)"``, ``"AMBI(bg)"``, ``"BEAT(fg)"``, ``"SFX(fg)"``.
    """
    key = cue.upper().strip()
    if key.startswith(("MUSIC", "INTRO MUSIC", "OUTRO MUSIC")) or " MUSIC" in key.split(":")[0]:
        return "MUSIC(bg)"
    if key.startswith("AMBIEN"):
        return "AMBI(bg)"
    if key.startswith("BEAT"):
        return "BEAT(fg)"
    return "SFX(fg)"


def _tier_for(delta_s: float) -> str:
    """Grade a cue by how many seconds of audio it loses."""
    if delta_s < NOCHANGE_S:
        return "1-nochange"
    if delta_s < MINOR_S:
        return "2-minor"
    return "3-review"


def _remediation_for(impact: CueImpact) -> str:
    """Return the config change that would let the cue play in full.

    Expressed as the edit a human would make, not applied by this tool.

    Deliberately recommends ``play_duration: 100`` rather than the more obvious
    ``duration_seconds: 0``.  Both play the whole file — ``play_duration`` wins
    over ``duration_seconds`` in the mixer — but only ``play_duration`` is in
    :data:`~xil_pipeline.sfx_common.SFX_EDIT_FIELDS`, so only it is replayed by
    the edit journal when a config is rebuilt from a fresh skeleton.  A
    ``duration_seconds`` edit silently reverts to the parser's ``5.0`` default
    on the next regeneration, re-clipping audio someone had already signed off.
    """
    if impact.tier in ("1-nochange", "EXCLUDED", "MISSING"):
        return ""
    return "play_duration: 100"


def measure_cue(
    show: str,
    episode: str,
    cue: str,
    effect: dict,
    duration_fn: DurationProbe,
    workspace: Path,
) -> CueImpact | None:
    """Measure one SFX config entry against its source file.

    Mirrors the precedence in :func:`xil_pipeline.mix_common.collect_stem_plans`:
    a looped bed is never clipped, an explicit ``play_duration`` wins over
    ``duration_seconds``, and ``duration_seconds == 0`` means full-length.

    Args:
        show: Show slug.
        episode: Episode tag.
        cue: SFX config key.
        effect: The config entry dict.
        duration_fn: Callable taking a path and returning duration in ms.
        workspace: Workspace root, for resolving relative ``source`` paths.

    Returns:
        A :class:`CueImpact`, or ``None`` when the entry has no ``source``
        (generated and silence cues are not in scope).
    """
    source = effect.get("source")
    if not source:
        return None

    impact = CueImpact(
        show=show,
        episode=episode,
        cue=cue,
        source_file=os.path.basename(source),
        duration_seconds=effect.get("duration_seconds"),
        play_duration=effect.get("play_duration"),
        loop=bool(effect.get("loop", False)),
        placement=classify_placement(cue),
    )

    path = Path(source)
    if not path.is_absolute():
        path = workspace / source
    try:
        natural = duration_fn(str(path)) / 1000.0
    except Exception as exc:                       # unreadable or absent
        impact.tier = "MISSING"
        impact.note = f"source file unreadable ({type(exc).__name__})"
        return impact
    if natural <= 0:
        impact.tier = "MISSING"
        impact.note = "source file reports zero duration"
        return impact

    impact.natural_s = natural

    if impact.loop:
        impact.plays_now_s = natural
        impact.note = "looped bed (fills cue span; not clipped)"
    elif impact.play_duration is not None:
        impact.plays_now_s = natural * float(impact.play_duration) / 100.0
        impact.note = "explicit play_duration kept (takes precedence)"
    elif impact.duration_seconds is None or float(impact.duration_seconds) <= 0:
        impact.plays_now_s = natural
        impact.note = "plays full length (duration_seconds 0 or absent)"
    else:
        impact.plays_now_s = min(float(impact.duration_seconds), natural)
        impact.tier = _tier_for(natural - impact.plays_now_s)
        impact.note = ""

    impact.delta_s = max(0.0, natural - impact.plays_now_s)
    impact.lost_pct = impact.delta_s / natural * 100.0
    impact.remediation = _remediation_for(impact)
    return impact


def discover_configs(
    workspace: Path,
    show: str | None = None,
    episode: str | None = None,
) -> list[tuple[str, str, Path]]:
    """Find SFX configs to analyse.

    Args:
        workspace: Workspace root.
        show: Restrict to one show slug; ``None`` sweeps every show.
        episode: Restrict to one episode tag; ``None`` takes every episode.

    Returns:
        Sorted ``(show, episode, path)`` tuples.
    """
    configs_dir = workspace / "configs"
    if not configs_dir.is_dir():
        return []

    pattern = f"sfx_{episode}.json" if episode else "sfx_*.json"
    found: list[tuple[str, str, Path]] = []
    for show_dir in sorted(configs_dir.iterdir()):
        if not show_dir.is_dir() or (show and show_dir.name != show):
            continue
        for path in sorted(show_dir.glob(pattern)):
            # Skip sidecars such as sfx_<tag>_edits.jsonl-adjacent artefacts.
            tag = path.stem[len("sfx_"):]
            if not tag:
                continue
            found.append((show_dir.name, tag, path))
    return found


def analyze(
    workspace: Path,
    show: str | None = None,
    episode: str | None = None,
    duration_fn: DurationProbe | None = None,
) -> ImpactReport:
    """Sweep the workspace and measure every source-backed cue.

    Args:
        workspace: Workspace root.
        show: Restrict to one show slug.
        episode: Restrict to one episode tag.
        duration_fn: Duration probe (path → ms); defaults to the same
            mutagen-backed helper the mixer uses.

    Returns:
        A populated :class:`ImpactReport`.
    """
    if duration_fn is None:
        from xil_pipeline.mix_common import _mp3_duration_ms
        duration_fn = _mp3_duration_ms

    report = ImpactReport()
    for slug, tag, path in discover_configs(workspace, show, episode):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"  Skipping unreadable config {path}: {exc}")
            continue
        report.configs_scanned += 1
        for cue, effect in (data.get("effects") or {}).items():
            if not isinstance(effect, dict):
                continue
            impact = measure_cue(slug, tag, cue, effect, duration_fn, workspace)
            if impact is not None:
                report.impacts.append(impact)
    return report


def write_csv(report: ImpactReport, stream) -> None:
    """Write the per-cue table to an open text stream."""
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for impact in report.impacts:
        writer.writerow(impact.row())


def log_summary(report: ImpactReport) -> None:
    """Log the per-show tier tally and the headline findings."""
    tally = report.tally()
    logger.info("")
    logger.info(f"  Scanned {report.configs_scanned} SFX config(s), "
                f"{len(report.impacts)} source-backed cue(s)")
    logger.info("")
    header = f"  {'show':<24} {'3-review':>9} {'2-minor':>8} {'1-nochange':>11} {'EXCLUDED':>9} {'MISSING':>8}"
    logger.info(header)
    logger.info("  " + "-" * (len(header) - 2))
    for slug, tiers in sorted(report.by_show().items()):
        logger.info(f"  {slug:<24} {tiers['3-review']:>9} {tiers['2-minor']:>8} "
                    f"{tiers['1-nochange']:>11} {tiers['EXCLUDED']:>9} {tiers['MISSING']:>8}")
    logger.info("  " + "-" * (len(header) - 2))
    logger.info(f"  {'TOTAL':<24} {tally['3-review']:>9} {tally['2-minor']:>8} "
                f"{tally['1-nochange']:>11} {tally['EXCLUDED']:>9} {tally['MISSING']:>8}")

    actionable = report.actionable
    if not actionable:
        logger.info("")
        logger.info("  No cues are losing audio — nothing to review.")
        return

    lost = sum(i.delta_s or 0.0 for i in actionable)
    logger.info("")
    logger.info(f"  {len(actionable)} cue(s) lose audio, {lost:.0f}s total")
    logger.info("")
    logger.info("  Worst offenders:")
    worst = sorted(actionable, key=lambda i: i.delta_s or 0.0, reverse=True)[:10]
    for impact in worst:
        logger.info(f"    {impact.delta_s:>6.1f}s lost  {impact.show}/{impact.episode}  "
                    f"{impact.cue[:44]}  ({impact.plays_now_s:.1f}s of {impact.natural_s:.1f}s)")


_HTML_CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
       padding: 2rem 1.25rem; line-height: 1.5; background: #fff; color: #1a1a1a; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
.sub { color: #666; margin: 0 0 1.5rem; font-size: .9rem; }
.cards { display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: 1.75rem; }
.card { border: 1px solid #e0e0e0; border-radius: 8px; padding: .75rem 1rem; min-width: 8rem; }
.card .n { font-size: 1.5rem; font-weight: 600; }
.card .l { font-size: .75rem; color: #666; text-transform: uppercase; letter-spacing: .04em; }
.wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .85rem; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #ececec;
         white-space: nowrap; }
th { position: sticky; top: 0; background: #fafafa; font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.cue { white-space: normal; min-width: 16rem; }
.t3 { color: #b3261e; font-weight: 600; }
.t2 { color: #9a6700; font-weight: 600; }
.t1, .tE { color: #888; }
.tM { color: #b3261e; }
code { background: #f3f3f3; padding: .1rem .3rem; border-radius: 3px; font-size: .9em; }
@media (prefers-color-scheme: dark) {
  body { background: #14161a; color: #e6e6e6; }
  .card, th, td { border-color: #2c2f36; }
  th { background: #1b1e24; }
  .sub, .card .l, .t1, .tE { color: #9aa0a6; }
  code { background: #23262c; }
  .t3, .tM { color: #ff8a80; }
  .t2 { color: #ffd180; }
}
"""

_TIER_CLASS = {"3-review": "t3", "2-minor": "t2", "1-nochange": "t1",
               "EXCLUDED": "tE", "MISSING": "tM"}


def render_html(report: ImpactReport, scope: str) -> str:
    """Render a standalone, self-contained review page.

    No external assets — it can be mailed to a creative or opened from a file
    share as-is.

    Args:
        report: The completed analysis.
        scope: Human-readable description of what was scanned.

    Returns:
        A complete HTML document.
    """
    tally = report.tally()
    actionable = report.actionable
    lost = sum(i.delta_s or 0.0 for i in actionable)
    generated = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

    cards = [
        ("3-review", tally["3-review"]),
        ("2-minor", tally["2-minor"]),
        ("1-nochange", tally["1-nochange"]),
        ("excluded", tally["EXCLUDED"]),
        ("missing", tally["MISSING"]),
        ("seconds lost", f"{lost:.0f}"),
    ]
    card_html = "".join(
        f'<div class="card"><div class="n">{html.escape(str(n))}</div>'
        f'<div class="l">{html.escape(label)}</div></div>'
        for label, n in cards
    )

    # Worst first — this page exists to drive per-cue decisions.
    order = {"3-review": 0, "2-minor": 1, "MISSING": 2, "1-nochange": 3, "EXCLUDED": 4}
    rows_sorted = sorted(
        report.impacts,
        key=lambda i: (order.get(i.tier, 9), -(i.delta_s or 0.0), i.show, i.episode),
    )

    def cell(value, numeric=False):
        if value is None or value == "":
            return '<td class="num"></td>' if numeric else "<td></td>"
        text = f"{value:.1f}" if numeric and isinstance(value, float) else str(value)
        return f'<td class="{"num" if numeric else ""}">{html.escape(text)}</td>'

    body_rows = []
    for i in rows_sorted:
        body_rows.append(
            "<tr>"
            + cell(i.show) + cell(i.episode)
            + f'<td class="cue">{html.escape(i.cue)}</td>'
            + cell(i.source_file)
            + cell(i.placement)
            + cell(i.natural_s, numeric=True)
            + cell(i.plays_now_s, numeric=True)
            + cell(i.delta_s, numeric=True)
            + cell(i.lost_pct, numeric=True)
            + f'<td class="{_TIER_CLASS.get(i.tier, "")}">{html.escape(i.tier)}</td>'
            + (f"<td><code>{html.escape(i.remediation)}</code></td>" if i.remediation else "<td></td>")
            + f"<td>{html.escape(i.note)}</td>"
            + "</tr>"
        )

    headers = ("show", "episode", "cue", "source file", "placement", "natural s",
               "plays now s", "lost s", "lost %", "tier", "remediation", "note")
    head_html = "".join(f"<th>{html.escape(h)}</th>" for h in headers)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SFX clipping impact — {html.escape(scope)}</title>
<style>{_HTML_CSS}</style></head>
<body>
<h1>SFX source-clipping impact</h1>
<p class="sub">{html.escape(scope)} · {report.configs_scanned} config(s) ·
{len(report.impacts)} source-backed cue(s) · generated {html.escape(generated)}</p>
<div class="cards">{card_html}</div>
<p class="sub"><strong>How to read this:</strong> for a <code>source=</code> cue,
<code>duration_seconds</code> clips the file at mix time — the parser writes a default of
<code>5.0</code> into every skeleton entry. <em>Excluded</em> cues are looped beds or cues with a
deliberate <code>play_duration</code>, which are never clipped by <code>duration_seconds</code>.
Nothing here has been changed; the remediation column is the edit that would restore full length.</p>
<p class="sub"><strong>Why <code>play_duration: 100</code> and not <code>duration_seconds: 0</code>?</strong>
Both play the whole file, but only <code>play_duration</code> is replayed by the timeline edit
journal. A <code>duration_seconds</code> edit is silently reset to <code>5.0</code> the next time the
config is rebuilt from a skeleton — re-clipping a cue that was already approved.</p>
<div class="wrap"><table><thead><tr>{head_html}</tr></thead>
<tbody>{"".join(body_rows)}</tbody></table></div>
</body></html>
"""


def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-sfx-impact."""
    parser = argparse.ArgumentParser(
        prog="xil-sfx-impact",
        description=(
            "Report which source-backed SFX cues are clipped by duration_seconds, "
            "how much audio each loses, and the config change that would restore it. "
            "Read-only — no config is ever modified."
        ),
    )
    parser.add_argument("--show", default=None,
                        help="Restrict to one show slug (default: every show in the workspace)")
    parser.add_argument("--episode", "--tag", dest="episode", default=None,
                        help="Restrict to one episode tag (e.g. S01E01)")
    parser.add_argument("--output", default=None,
                        help="CSV output path, or '-' for stdout "
                             "(default: reports/sfx_impact_<date>.csv)")
    parser.add_argument("--html", nargs="?", const="", default=None,
                        help="Also write a standalone HTML review page "
                             "(default path: reports/sfx_impact_<date>.html)")
    parser.add_argument("--tier", default=None, choices=("2-minor", "3-review", "actionable"),
                        help="Only report cues at this tier ('actionable' = tiers 2 and 3)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress the console summary (CSV only)")
    return parser


def _filter_tier(report: ImpactReport, tier: str | None) -> ImpactReport:
    """Return a report narrowed to *tier*, or the original when unset."""
    if not tier:
        return report
    wanted = ("2-minor", "3-review") if tier == "actionable" else (tier,)
    return ImpactReport(
        impacts=[i for i in report.impacts if i.tier in wanted],
        configs_scanned=report.configs_scanned,
    )


def _run(args) -> None:
    """Do the work. Split out so ``main`` can choose whether to wrap it."""
    workspace = get_workspace_root()
    # An explicit --episode with no --show still means "this show" the way
    # every other episode-scoped command does.
    show = args.show
    if show is None and args.episode:
        show = resolve_slug(None)

    to_stdout = args.output == "-"

    report = analyze(workspace, show=show, episode=args.episode)
    if not report.configs_scanned:
        scope = f"show={show}" if show else "workspace"
        msg = f"No SFX configs found ({scope}) under {workspace / 'configs'}"
        # stdout belongs to the CSV when streaming — diagnostics go to stderr.
        if to_stdout:
            print(msg, file=sys.stderr)
        else:
            logger.error(msg)
        sys.exit(1)

    report = _filter_tier(report, args.tier)

    if to_stdout:
        write_csv(report, sys.stdout)
    else:
        date = dt.date.today().isoformat()
        out_path = Path(args.output) if args.output else (
            workspace / "reports" / f"sfx_impact_{date}.csv")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            write_csv(report, f)
        logger.info(f"  Wrote {out_path} ({len(report.impacts)} row(s))")

    if args.html is not None:
        date = dt.date.today().isoformat()
        html_path = Path(args.html) if args.html else (
            workspace / "reports" / f"sfx_impact_{date}.html")
        html_path.parent.mkdir(parents=True, exist_ok=True)
        scope = show or "all shows"
        if args.episode:
            scope = f"{scope} · {args.episode}"
        html_path.write_text(render_html(report, scope), encoding="utf-8")
        if to_stdout:
            print(f"Wrote {html_path}", file=sys.stderr)
        else:
            logger.info(f"  Wrote {html_path}")

    if not args.quiet and not to_stdout:
        log_summary(report)


def main() -> None:
    """CLI entry point for the SFX clipping impact report."""
    configure_logging()
    args = get_parser().parse_args()

    # ``--output -`` makes stdout the CSV stream, so the run banner (which the
    # console handler writes to stdout) would corrupt it.  Skip the banner in
    # that mode and keep stdout clean for piping.
    if args.output == "-":
        _run(args)
    else:
        with run_banner(SCRIPT_NAME):
            _run(args)


if __name__ == "__main__":
    main()
