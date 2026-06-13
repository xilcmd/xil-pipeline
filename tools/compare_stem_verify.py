#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compare two stem_verify JSON reports by Whisper transcript similarity.

Matches stems across two reports using bipartite greedy matching on transcript
text. Filenames and seq numbering need not align — matching is done on what
Whisper actually heard.

Typical use: compare ElevenLabs Studio export stems against pipeline-generated
stems for the same episode. Reports matched pairs, orphans (unmatched entries),
and flags pairs where the transcripts diverge below a similarity threshold.

Usage::

    python3 tools/compare_stem_verify.py \\
        ElevenLabs_exports/.../stem_verify_report.json \\
        parsed/the413/stem_verify_S01E01.json \\
        --label-a "EL-Studio" --label-b "Pipeline" \\
        --output /tmp/compare.json
"""

import argparse
import json
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


# ---------------------------------------------------------------------------
# Text normalization + similarity
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_entries(path: Path, sfx_filter: bool) -> list[dict]:
    """Return file entries that have a non-empty transcript."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = []
    for e in data["files"]:
        if sfx_filter and e.get("speaker") == "sfx":
            continue
        t = e.get("transcript")
        if t and t.get("text") and t["text"].strip():
            entries.append(e)
    return entries


# ---------------------------------------------------------------------------
# Bipartite greedy matching
# ---------------------------------------------------------------------------

def _match(a_entries: list[dict], b_entries: list[dict], min_match: float) -> tuple[list[dict], list[int], list[int]]:
    """Greedy bipartite match by transcript similarity.

    Returns (pairs, a_orphan_indices, b_orphan_indices).
    Each pair is a dict with keys: similarity, a, b.
    """
    # Build all (i, j, sim) triples
    triples = []
    for i, a in enumerate(a_entries):
        for j, b in enumerate(b_entries):
            s = round(_sim(a["transcript"]["text"], b["transcript"]["text"]), 4)
            if s >= min_match:
                triples.append((i, j, s))

    # Sort descending by similarity
    triples.sort(key=lambda x: -x[2])

    matched_a: set[int] = set()
    matched_b: set[int] = set()
    pairs: list[dict] = []

    for i, j, s in triples:
        if i in matched_a or j in matched_b:
            continue
        matched_a.add(i)
        matched_b.add(j)
        pairs.append({"similarity": s, "a_idx": i, "b_idx": j})

    a_orphans = [i for i in range(len(a_entries)) if i not in matched_a]
    b_orphans = [j for j in range(len(b_entries)) if j not in matched_b]

    # Sort pairs by a_idx (preserves reading order)
    pairs.sort(key=lambda p: p["a_idx"])
    return pairs, a_orphans, b_orphans


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------

def _entry_record(e: dict) -> dict:
    return {
        "seq": e.get("seq"),
        "filename": e.get("filename"),
        "speaker": e.get("speaker"),
        "scene": e.get("scene"),
        "duration_seconds": e.get("duration_seconds"),
        "bitrate_kbps": e.get("bitrate_kbps"),
        "transcript": e["transcript"]["text"],
    }


def _build_output(
    pairs: list[dict],
    a_orphans: list[int],
    b_orphans: list[int],
    a_entries: list[dict],
    b_entries: list[dict],
    label_a: str,
    label_b: str,
    threshold: float,
    path_a: Path,
    path_b: Path,
) -> dict:
    pair_records = []
    flagged_count = 0
    sim_total = 0.0

    for rank, p in enumerate(pairs, 1):
        a = a_entries[p["a_idx"]]
        b = b_entries[p["b_idx"]]
        s = p["similarity"]
        flagged = s < threshold
        if flagged:
            flagged_count += 1
        sim_total += s
        pair_records.append({
            "rank": rank,
            "similarity": s,
            "flagged": flagged,
            "a": _entry_record(a),
            "b": _entry_record(b),
        })

    avg_sim = round(sim_total / len(pairs), 4) if pairs else None

    return {
        "generated": datetime.now().replace(microsecond=0).isoformat(),
        "label_a": label_a,
        "label_b": label_b,
        "path_a": str(path_a.resolve()),
        "path_b": str(path_b.resolve()),
        "threshold": threshold,
        "summary": {
            "a_total": len(a_entries),
            "b_total": len(b_entries),
            "matched": len(pairs),
            "a_orphans": len(a_orphans),
            "b_orphans": len(b_orphans),
            "flagged": flagged_count,
            "avg_similarity": avg_sim,
        },
        "pairs": pair_records,
        "a_orphans": [_entry_record(a_entries[i]) for i in a_orphans],
        "b_orphans": [_entry_record(b_entries[j]) for j in b_orphans],
    }


# ---------------------------------------------------------------------------
# Terminal display
# ---------------------------------------------------------------------------

def _print_report(result: dict, verbose: bool = False) -> None:
    s = result["summary"]
    la = result["label_a"]
    lb = result["label_b"]

    print(f"\n  {la:<20}: {s['a_total']} stems with transcripts")
    print(f"  {lb:<20}: {s['b_total']} stems with transcripts")
    print()
    print(f"  Matched pairs        : {s['matched']}")
    print(f"  {la} orphans    : {s['a_orphans']}")
    print(f"  {lb} orphans    : {s['b_orphans']}")
    print(f"  Flagged (< {result['threshold']:.2f})   : {s['flagged']}")
    print(f"  Avg similarity       : {s['avg_similarity']:.4f}" if s["avg_similarity"] else "  Avg similarity       : n/a")

    if verbose:
        print(f"\n--- All matched pairs ({la} → {lb}) ---")
        print(f"  {'sim':>6}  {'A-seq':>5}  {'A-filename':<28}  {'B-seq':>5}  {'B-filename':<34}  transcript (truncated)")
        print(f"  {'-'*6}  {'-'*5}  {'-'*28}  {'-'*5}  {'-'*34}  {'-'*40}")
        for p in result["pairs"]:
            a = p["a"]
            b = p["b"]
            flag = " *" if p["flagged"] else "  "
            fname = (a.get("filename") or "")[:28]
            bfname = (b.get("filename") or "")[:34]
            preview = a["transcript"][:60].replace("\n", " ")
            print(f"{flag}{p['similarity']:>6.4f}  {a['seq']:>5}  {fname:<28}  {b['seq']:>5}  {bfname:<34}  {preview}")

    flagged = [p for p in result["pairs"] if p["flagged"]]
    if flagged:
        print(f"\n--- Flagged pairs (similarity < {result['threshold']}) ---")
        for p in flagged:
            a = p["a"]
            b = p["b"]
            spk = b.get("speaker") or "?"
            print(f"\n[sim={p['similarity']:.4f}]  {la} seq={a['seq']:>3}  {lb} seq={b['seq']:>3}  {spk}")
            print(f"  {la}: {a['transcript'][:100]}")
            print(f"  {lb}: {b['transcript'][:100]}")

    a_orphans = result.get("a_orphans", [])
    if a_orphans:
        print(f"\n--- {la} orphans (no match found in {lb}) ---")
        for e in a_orphans:
            print(f"  seq={e['seq']:>3}  {e['filename']}  {e['duration_seconds']:.1f}s")
            print(f"    {e['transcript'][:100]}")

    b_orphans = result.get("b_orphans", [])
    if b_orphans:
        print(f"\n--- {lb} orphans (no match found in {la}) ---")
        for e in b_orphans:
            spk = e.get("speaker") or "?"
            print(f"  seq={e['seq']:>3}  {spk}  {e['filename']}  {e['duration_seconds']:.1f}s")
            print(f"    {e['transcript'][:100]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compare_stem_verify",
        description="Compare two stem_verify JSON reports by Whisper transcript similarity.",
    )
    parser.add_argument("report_a", metavar="REPORT_A", help="First stem_verify JSON (e.g. ElevenLabs export)")
    parser.add_argument("report_b", metavar="REPORT_B", help="Second stem_verify JSON (e.g. pipeline stems)")
    parser.add_argument("--label-a", default="A", metavar="LABEL", help="Display label for report A")
    parser.add_argument("--label-b", default="B", metavar="LABEL", help="Display label for report B")
    parser.add_argument("--threshold", type=float, default=0.75, metavar="FLOAT",
                        help="Flag pairs with similarity below this (default: 0.75)")
    parser.add_argument("--min-match", type=float, default=0.40, metavar="FLOAT",
                        help="Minimum similarity to treat as matched vs orphan (default: 0.40)")
    parser.add_argument("--output", "-o", default=None, metavar="FILE",
                        help="Write full JSON report to file")
    parser.add_argument("--no-sfx-filter", action="store_true",
                        help="Include sfx-speaker entries (excluded by default)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print all matched pairs as a mapping table (A-filename → B-seq/speaker/scene)")
    return parser


def main() -> None:
    args = get_parser().parse_args()

    path_a = Path(args.report_a)
    path_b = Path(args.report_b)

    if not path_a.exists():
        print(f"Error: report_a not found: {path_a}", file=sys.stderr)
        sys.exit(1)
    if not path_b.exists():
        print(f"Error: report_b not found: {path_b}", file=sys.stderr)
        sys.exit(1)

    sfx_filter = not args.no_sfx_filter
    a_entries = _load_entries(path_a, sfx_filter=False)   # EL has no speaker field
    b_entries = _load_entries(path_b, sfx_filter=sfx_filter)

    print(f"Loaded {len(a_entries)} A-entries from {path_a.name}")
    print(f"Loaded {len(b_entries)} B-entries from {path_b.name}")
    print("Matching... ", end="", flush=True)

    pairs, a_orphans, b_orphans = _match(a_entries, b_entries, args.min_match)
    print("done.")

    result = _build_output(
        pairs, a_orphans, b_orphans,
        a_entries, b_entries,
        args.label_a, args.label_b,
        args.threshold,
        path_a, path_b,
    )

    _print_report(result, verbose=args.verbose)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"\nWritten: {out}")


if __name__ == "__main__":
    main()
