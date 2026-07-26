#!/usr/bin/env python3
"""xil_effort.py — estimate effort from git history + xil pipeline logs.
Lives in tools/; run from anywhere: python3 tools/xil_effort.py [--log PATH ...]
Git repo: auto-detected from this script's location.
Data root: $XIL_PROJECTROOT — logs read from $XIL_PROJECTROOT/logs/*.log,
outputs written to $XIL_PROJECTROOT/reports/. Falls back to the repo root
for both if $XIL_PROJECTROOT is unset.
"""
import subprocess, re, os, glob, argparse, datetime as dt
from pathlib import Path
from collections import defaultdict

def find_repo():
    here = Path(__file__).resolve().parent
    try:
        top = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True).stdout.strip()
        return Path(top)
    except subprocess.CalledProcessError:
        raise SystemExit(f"No git repo found at or above {here}")

REPO = find_repo()
DATA = Path(os.environ.get("XIL_PROJECTROOT") or REPO)
REPORTS = DATA / "reports"
REPORTS.mkdir(exist_ok=True)

ap = argparse.ArgumentParser()
ap.add_argument("--log", nargs="*", default=[str(DATA / "logs" / "*.log")],
                help="xil activity log file(s) or globs "
                     "(default: $XIL_PROJECTROOT/logs/*.log)")
ap.add_argument("--run-marker", default="VOICE REFS",
                help="section header printed once per xil invocation "
                     "(default: VOICE REFS)")
args = ap.parse_args()

def git(*a):
    return subprocess.run(["git", "-C", str(REPO)] + list(a),
        capture_output=True, text=True, check=True).stdout.splitlines()

# --- gather: git ---
commits = git("log", "--date=short", "--pretty=%ad")
merges  = git("log", "--merges", "--date=short", "--pretty=%ad")

day_commits = defaultdict(int)
day_merges  = defaultdict(int)
day_ins     = defaultdict(int)
day_del     = defaultdict(int)
day_pipe    = defaultdict(int)
cmd_counts  = defaultdict(int)
for d in commits: day_commits[d] += 1
for d in merges:  day_merges[d]  += 1

# --- gather: pipeline logs ---
# Two on-disk formats are understood:
#
#   v2 (structured)   xil_v2_YYYY-MM-DD.log — every line is
#                     "<iso-ts>|<LEVEL>|<stage>|<message>".  Stage names are
#                     tallied directly and runs are counted from the
#                     "|RUN|<stage>|BEGIN ..." banner written by run_banner().
#   v1 (stdout dump)  xil_YYYY-MM-DD.log / xil_v1_YYYY-MM-DD.log — captured
#                     stdout with no per-line metadata.  The date comes from
#                     the filename, runs are counted by occurrences of the
#                     per-invocation banner (default "VOICE REFS", override
#                     with --run-marker), and other ALL-CAPS section headers
#                     are tallied as a section-mix breakdown.
#
# HTTP trace lines are ignored in both.
FNAME_DATE = re.compile(r"xil_(?:v\d+_)?(\d{4}-\d{2}-\d{2})\.log$")
HEADER     = re.compile(r"^([A-Z][A-Z0-9_/-]+(?: [A-Z0-9_/-]+)+)")
LEVEL      = re.compile(r"^[A-Z]+$")

matched_files = 0
for pattern in args.log:
    for path in sorted(glob.glob(pattern)):
        matched_files += 1
        fname_day = None
        m = FNAME_DATE.search(path)
        if m:
            fname_day = m.group(1)
        piped, runs = 0, 0
        with open(path, errors="replace") as fh:
            for line in fh:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2 and len(parts[0]) >= 10:
                    try:
                        day = dt.date.fromisoformat(parts[0][:10]).isoformat()
                    except ValueError:
                        pass
                    else:
                        # v2 puts the level second and the stage third; the
                        # older anticipated shape was "timestamp|command|args".
                        if len(parts) >= 3 and LEVEL.match(parts[1]):
                            # v2: count one unit of activity per invocation, so
                            # the daily figure stays a RUN count comparable with
                            # v1 days rather than becoming a line count.
                            stage = parts[2]
                            if parts[1] == "RUN" and parts[-1].startswith("BEGIN"):
                                day_pipe[day] += 1
                        else:
                            stage = parts[1]
                            day_pipe[day] += 1
                        cmd_counts[stage] += 1
                        piped += 1
                        continue
                h = HEADER.match(line)
                if h:
                    name = h.group(1)
                    if name.startswith("HTTP"):
                        continue                    # API trace noise, not a section
                    if name == args.run_marker:
                        runs += 1
                    else:
                        cmd_counts[name] += 1
        if piped == 0 and fname_day:                # v1 stdout-dump style log
            day_pipe[fname_day] += max(runs, 1)

if not day_pipe:
    msg = "no log files matched" if matched_files == 0 else \
          f"{matched_files} log file(s) matched but no activity was parsed"
    print(f"WARNING: pipeline logs contributed nothing ({msg}): {args.log}\n")

if not day_pipe:
    msg = "no log files matched" if matched_files == 0 else \
          f"{matched_files} log file(s) matched but no activity was parsed"
    print(f"WARNING: pipeline logs contributed nothing ({msg}): {args.log}\n")

# churn: date lines followed by shortstat lines
cur = None
for line in git("log", "--date=short", "--pretty=%ad", "--shortstat"):
    line = line.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", line):
        cur = line
    elif cur and "changed" in line:
        ins = re.search(r"(\d+) insertion", line)
        dele = re.search(r"(\d+) deletion", line)
        if ins:  day_ins[cur] += int(ins.group(1))
        if dele: day_del[cur] += int(dele.group(1))

def iso_week(datestr):
    y, w, _ = dt.date.fromisoformat(datestr).isocalendar()
    return f"{y}-W{w:02d}"

# --- weekly rollup ---
weeks = defaultdict(lambda: {"commits": 0, "merges": 0, "days": set(),
                             "ins": 0, "del": 0, "pipe": 0})
for d, n in day_commits.items():
    wk = weeks[iso_week(d)]
    wk["commits"] += n
    wk["days"].add(d)
    wk["ins"] += day_ins.get(d, 0)
    wk["del"] += day_del.get(d, 0)
for d, n in day_merges.items():
    weeks[iso_week(d)]["merges"] += n
for d, n in day_pipe.items():
    wk = weeks[iso_week(d)]
    wk["pipe"] += n
    wk["days"].add(d)   # pipeline-only days count as active

def tier(active_days, churn):
    base = 2 if active_days >= 4 else 1 if active_days >= 2 else 0
    if churn >= 1000: base = min(base + 1, 2)   # big churn bumps a tier
    return ["Low", "Medium", "High"][base]

# --- table + weekly CSV ---
import csv
csv_out = open(REPORTS / "effort_weekly.csv", "w", newline="")
cw = csv.writer(csv_out)
cw.writerow(["week", "commits", "merges", "pipe_runs", "active_days",
             "lines_added", "lines_deleted", "effort"])
print("| Week | Commits | Merges | Pipe Runs | Active Days | +Lines | −Lines | Effort |")
print("|------|---------|--------|-----------|-------------|--------|--------|--------|")
for wk in sorted(weeks):
    w = weeks[wk]
    n = len(w["days"])
    t = tier(n, w['ins'] + w['del'])
    print(f"| {wk} | {w['commits']} | {w['merges']} | {w['pipe']} | {n} "
          f"| {w['ins']} | {w['del']} | {t} |")
    cw.writerow([wk, w['commits'], w['merges'], w['pipe'], n,
                 w['ins'], w['del'], t])

tot_c = sum(w["commits"] for w in weeks.values())
tot_m = sum(w["merges"] for w in weeks.values())
tot_p = sum(w["pipe"] for w in weeks.values())
tot_d = sum(len(w["days"]) for w in weeks.values())
tot_i = sum(w["ins"] for w in weeks.values())
tot_x = sum(w["del"] for w in weeks.values())
print(f"| **Total** | **{tot_c}** | **{tot_m}** | **{tot_p}** | **{tot_d}** "
      f"| **{tot_i}** | **{tot_x}** | |")
if cmd_counts:
    print("\nPipeline section mix: " +
          ", ".join(f"{k} ×{v}" for k, v in sorted(cmd_counts.items())))

# --- calendar heatmap (GitHub-style HTML) ---
all_days = set(day_commits) | set(day_pipe)
first = min(dt.date.fromisoformat(d) for d in all_days)
last  = max(dt.date.fromisoformat(d) for d in all_days)
start = first - dt.timedelta(days=first.weekday())  # align to Monday
peak  = max(day_commits.values()) if day_commits else 1

def color(n):
    if n == 0: return "#ebedf0"
    steps = ["#9be9a8", "#40c463", "#30a14e", "#216e39"]
    return steps[min(int(n / peak * 4), 3)]

cells = defaultdict(dict)  # cells[weekday][week_index]
d, wi = start, 0
while d <= last:
    n = day_commits.get(d.isoformat(), 0)
    cells[d.weekday()][wi] = (d, n)
    if d.weekday() == 6: wi += 1
    d += dt.timedelta(days=1)

def cell_style(day, n):
    s = f"width:12px;height:12px;background:{color(n)};border-radius:2px"
    if day_pipe.get(day.isoformat(), 0):
        s += ";outline:2px solid #f66a0a;outline-offset:-2px"  # pipeline marker
    return s

rows = []
for wd, label in enumerate(["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]):
    tds = "".join(
        f'<td title="{c[0]} — {c[1]} commits, '
        f'{day_pipe.get(c[0].isoformat(), 0)} pipe runs, '
        f'+{day_ins.get(c[0].isoformat(), 0)}/−{day_del.get(c[0].isoformat(), 0)} lines" '
        f'style="{cell_style(c[0], c[1])}"></td>'
        if (c := cells[wd].get(i)) else "<td></td>"
        for i in range(wi + 1))
    rows.append(f'<tr><td style="font:10px sans-serif;padding-right:4px">{label}</td>{tds}</tr>')

html = (f"<html><body style='font-family:sans-serif'><h3>xil-pipeline activity "
        f"({first} → {last})</h3><table style='border-spacing:2px'>{''.join(rows)}"
        f"</table><p>{tot_c} commits, {tot_m} merges, {tot_p} pipeline runs, "
        f"{tot_d} active days, +{tot_i}/−{tot_x} lines. "
        f"Green = commits (peak {peak}/day); orange outline = pipeline activity.</p>"
        f"</body></html>")
csv_out.close()
open(REPORTS / "effort_calendar.html", "w").write(html)
print(f"\nWrote {REPORTS}/effort_weekly.csv and {REPORTS}/effort_calendar.html")
