#!/usr/bin/env python3
"""migrate_logs_v1.py — rename pre-v2 xil logs to the versioned convention.

Lives in tools/; run from anywhere: python3 tools/migrate_logs_v1.py [-n]

Before v2, ``logs/xil_YYYY-MM-DD.log`` held a bare copy of the terminal
transcript (no timestamp, level or stage per line).  v2 writes a structured
``logs/xil_v2_YYYY-MM-DD.log`` instead, so the old files are renamed to
``xil_v1_YYYY-MM-DD.log`` and the format is unambiguous from the filename —
no sniffing, and ``xil-stem-log`` / ``xil_effort`` can parse both.

Only files matching ``xil_YYYY-MM-DD.log`` exactly are touched.  Anything else
in ``logs/`` (already-versioned files, unrelated ``*.log`` files) is left alone.
Safe to re-run: already-renamed files are reported as skipped.

Files currently held open by another process are **skipped**.  Renaming a log
that a running ``xil`` command or ``xil-gui`` still has open leaves the writer
appending to the old inode, and on a network mount (9p/NFS/SMB) it can leave
the new name unresolvable until that process exits.  Today's log is normally
the only one at risk; stop the writer (or wait for it to finish) and re-run.

Log directory: ``$XIL_PROJECTROOT/logs``, falling back to ``./logs``.
"""
import argparse
import os
import re
import sys
from pathlib import Path

# Exactly the pre-v2 name: xil_<date>.log with nothing between "xil_" and the date.
V1_NAME = re.compile(r"^xil_(\d{4}-\d{2}-\d{2})\.log$")

ap = argparse.ArgumentParser(
    description="Rename pre-v2 xil_<date>.log files to xil_v1_<date>.log.",
)
ap.add_argument("-n", "--dry-run", action="store_true",
                help="List what would be renamed without changing anything")
ap.add_argument("--logs-dir", default=None,
                help="Log directory (default: $XIL_PROJECTROOT/logs, else ./logs)")
args = ap.parse_args()

if args.logs_dir:
    logs_dir = Path(args.logs_dir)
else:
    root = os.environ.get("XIL_PROJECTROOT")
    logs_dir = (Path(root) if root else Path.cwd()) / "logs"

if not logs_dir.is_dir():
    sys.exit(f"Log directory not found: {logs_dir}")

def _open_by(path: Path) -> str | None:
    """Return "pid <n> (<cmd>)" if some process holds *path* open, else None.

    Best-effort and Linux-only: scans /proc/*/fd.  Processes owned by other
    users are invisible, so a None result is "no evidence", not a guarantee.
    """
    target = str(path.resolve() if path.exists() else path)
    for proc in Path("/proc").glob("[0-9]*"):
        fd_dir = proc / "fd"
        try:
            handles = list(fd_dir.iterdir())
        except (PermissionError, FileNotFoundError, OSError):
            continue
        for fd in handles:
            try:
                if os.readlink(fd) == target:
                    try:
                        cmd = (proc / "comm").read_text().strip()
                    except OSError:
                        cmd = "?"
                    return f"pid {proc.name} ({cmd})"
            except OSError:
                continue
    return None


renamed, skipped, collisions, in_use = [], [], [], []

for path in sorted(logs_dir.iterdir()):
    if not path.is_file():
        continue
    m = V1_NAME.match(path.name)
    if not m:
        skipped.append(path.name)
        continue
    target = path.with_name(f"xil_v1_{m.group(1)}.log")
    if target.exists():
        # Never clobber: an existing v1 file means this was already migrated.
        collisions.append(f"{path.name} -> {target.name} (target exists)")
        continue
    holder = _open_by(path)
    if holder:
        # Renaming an open log strands the writer on the old inode, and on a
        # network mount the new name can become unresolvable until it exits.
        in_use.append(f"{path.name} (held open by {holder})")
        continue
    if args.dry_run:
        renamed.append(f"{path.name} -> {target.name}")
    else:
        path.rename(target)
        renamed.append(f"{path.name} -> {target.name}")

print(f"Log directory: {logs_dir}")
if args.dry_run:
    print("(--dry-run: nothing was changed)\n")

print(f"Renamed:    {len(renamed)}")
for r in renamed:
    print(f"    {r}")
print(f"Left alone: {len(skipped)}")
for s in skipped[:10]:
    print(f"    {s}")
if len(skipped) > 10:
    print(f"    … and {len(skipped) - 10} more")
if in_use:
    print(f"In use:     {len(in_use)}  (not renamed — stop the writer and re-run)")
    for u in in_use:
        print(f"    {u}")
if collisions:
    print(f"Collisions: {len(collisions)}  (nothing overwritten)")
    for c in collisions:
        print(f"    {c}")

sys.exit(1 if collisions else 0)
