# NAS Impact Assessment — `XIL_PROJECTROOT` on a Network Mount

Written 2026-07-11, when `XIL_PROJECTROOT` was being moved from local disk
(`/mnt/c/Users/shaba/xil-projects`) to a NAS mount (`/mnt/cloudsy/...`).
Every workspace read becomes a network round-trip; every stat becomes an SMB
metadata call. This doc ranks where that hurts, what has already been
mitigated in code, and what to watch during and after migration.

The code root (`XIL_CODEROOT`, the repo itself) stays local — only content
workspace I/O is affected.

---

## Ranked pain points

### 0. Timeline artifacts baked absolute `XIL_PROJECTROOT` paths — 403 after the root moved

**Status: fixed (root-agnostic relative paths, 2026-07-11)**

This was the first thing that actually broke on the NAS. The generated
timeline HTML embedded **absolute** workspace paths in `LAYER_AUDIO` and
`CLIPS` (e.g. `/mnt/c/Users/shaba/xil-projects/daw/.../layer_dialogue.wav`).
The GUI serves the timeline in an iframe via `/gradio_api/file=<abs>`, whose
`allowed_paths` is the **current** workspace root. Once `XIL_PROJECTROOT`
moved to `/mnt/cloudsy/...`, every embedded `/mnt/c/...` path fell outside
`allowed_paths` and the browser got **HTTP 403 "File not allowed"** for all
five layer WAVs and every clip — playback with no sound and (under the old
pre-prefetch code) no visible error. Regenerating on the NAS didn't help
either, because `parsed/` had not been synced there yet, so `xil daw` bailed
with "Parsed JSON not found."

*Root cause:* an artifact that hard-codes the absolute root is invalidated by
any root move, and re-generating it requires the full source tree
(`parsed/`, cast configs) present at the new root.

*Fix:* `render_html_timeline` now stores every audio URL **relative to the
timeline file's own directory** (`os.path.relpath`, POSIX separators, keeping
the `?v={mtime}` cache-buster). Layer WAVs sit beside the timeline →
`S07E01_layer_dialogue.wav?v=…`; stems resolve as
`../../../stems/<slug>/<TAG>/<seq>_….mp3?v=…`. The browser resolves these
against the iframe's document URL (`/gradio_api/file=<abs timeline>`), so the
**same artifact works under any root** — local or NAS — with no
regeneration. A plain file copy to the NAS is now sufficient; the prefetch
fetches the relative path directly (no `/gradio_api/file=` concatenation).
Guarded by `test_paths_are_root_agnostic_relative`.

*Migration action:* timeline HTML files copied from the old local tree still
contain the old absolute paths — **re-sync or regenerate them** so they carry
the relative form. (Done for the 26 episodes already present on the NAS on
2026-07-11; `the413/S07E03` was not yet synced.)

### 1. Episodes-tab scan — every browser connect walked the whole workspace

**Status: mitigated (GUI cache, 2026-07)**

`xil-gui`'s Episodes tab calls the `XILU019` episode-status engine, which
`rglob`s + stats every stems/daw file **and** the full `scripts/` tree per
episode. On local disk that is milliseconds; over SMB it is thousands of
serial metadata round-trips — and it fired on *every* browser connect
(`demo.load`).

*Mitigation:* `_refresh_episodes` results are now cached per workspace root
with a 5-minute TTL (`_EPISODES_CACHE` in `xil_gui.py`). New browser tabs get
cached rows instantly; the ⟳ Refresh button forces a rescan.

### 2. Timeline full-mix playback — 5 WAVs (~1–1.7 GB) streamed blind

**Status: mitigated (browser prefetch with progress, 2026-07)**

The timeline transport played five layer WAVs streamed straight off
`/gradio_api/file=<NAS path>`. Over a NAS the browser buffers stalled with
zero feedback — the "hang" that motivated this assessment.

*Mitigation:* the generated timeline HTML now prefetches all layers to Blobs
with an aggregate `Loading audio… N% (X / Y MB)` readout, per-layer MB rows,
and a Cancel button (second click on ▶ also cancels). Loaded blobs are cached
by URL + mtime cache-buster, so replays are instant. Single-clip preview and
the sound-profile workflow got the same treatment (`Loading… N%` in the
player strip). Remember: the timeline HTML is a **build artifact** — the fix
reaches an episode only after `xil daw --episode <TAG> --dry-run
--timeline-html` regenerates it (done for all existing artifacts on
2026-07-11).

### 3. GUI audio tabs — every selection re-read the file from the NAS

**Status: mitigated (local read-through cache, 2026-07)**

The Audio Preview and Audio Grading tabs handed NAS paths directly to
`gr.Audio`, and "play all" concatenation decoded every stem from the mount on
every click (and leaked a temp file per click).

*Mitigation:* `_cached_audio_path()` copies workspace audio to a bounded
local cache (`~/.cache/xil-gui/audio/`, 2 GB budget, oldest-mtime eviction)
with a `Copying <name>… X / Y MB` progress bar; keys are
`sha1(path|size|mtime)` so they self-invalidate. Concatenated "play all"
products are cached the same way (fixing the temp-file leak). Any `OSError`
degrades to direct streaming — playback never breaks because the cache did.

### 4. No I/O timeouts anywhere — a stalled mount is an indefinite hang

**Status: open — mount configuration + startup probe recommended**

Nothing in the pipeline or GUI sets I/O deadlines. A wedged NAS mount (sleep,
network blip, SMB reconnect) turns every stage and every GUI handler into an
indefinite hang, indistinguishable from "slow."

*Recommendations:*

- Mount with timeouts rather than hard-hanging defaults — for CIFS/SMB:
  `soft`, sensible `echo_interval`, and `actimeo` to batch attribute reads;
  for NFS: `soft,timeo=...,retrans=...`.
- Add a coarse liveness probe at GUI/CLI startup (e.g. `os.stat` of the
  workspace root in a thread with a 5 s deadline) and print a clear banner
  ("workspace on NAS is not responding") instead of hanging silently.

### 5. Master export reads everything, every run

**Status: open — acceptable cost, watch it**

`XILP011` (master export) full-loads all five layer WAVs each run, and
daw/assembly stages do per-stem pydub/ffmpeg decodes of full files. That is
1–1.7 GB pulled across the network per export, per attempt. Unlike the GUI
there is progress logging, so it is slow-but-visible rather than a hang.

*Future option:* a read-through staging copy for pipeline stages, reusing the
same key scheme as the GUI audio cache.

### 6. Atomic renames and journal appends over SMB

**Status: open — audit during migration**

The pipeline leans on `os.replace`/rename for atomic writes and appends to
JSONL journals (`sfx_*_edits.jsonl`). On SMB, rename onto a target that
another process holds open (e.g. Gradio streaming a WAV while a re-export
replaces it) can fail with sharing violations, and append atomicity is not
guaranteed across clients.

*Recommendation:* wrap workspace renames in a small retry helper
(3–5 attempts, short backoff) during migration; keep single-writer discipline
for journals.

### 7. `sync_to_NAS.sh` — now a full-tree mirror (was a partial subset)

**Status: fixed 2026-07-11 — rewritten to mirror the whole workspace**

The casing concern originally noted here (`DAW/` vs `daw/`) was already
resolved (the script uses lowercase `daw/`). The real problem, found while
running a sync for the cutover, was coverage: the old script pushed only
`configs/ masters/ scripts/ voice_refs/ .regen*` + root `*.sh`, and synced
`daw/`+`stems/` only under an explicit `--stems` flag. **`parsed/` was never
synced at all** — which is exactly why `xil daw --timeline-html` could not
regenerate a timeline natively on the NAS ("Parsed JSON not found").

*Fix:* rewritten to mirror the **entire** `$XIL_PROJECTROOT` in one
`rsync -av --delete --partial ./ "$NAS"/`, excluding only transient
caches/junk (`.ruff_cache/`, `__pycache__/`, `*.pyc`, `.DS_Store`,
`Thumbs.db`). This now includes `parsed/`, all content dirs, and the loose
root reports/logs. `--dry-run` is preserved.

**Direction caveat (important):** this is a **local → NAS** mirror with
`--delete` — correct only while the *local* disk is the source of truth
(i.e. during the cutover). Once `XIL_PROJECTROOT` points at the NAS and you
begin writing there (grades, sfx edits, new renders), re-running it would
**erase** those NAS-side changes. After cutover, retire the script or reverse
`src`/`dst` for a NAS→local backup.

*Slowness note:* even a `--dry-run` of the full tree is minutes-long — just
building the file list for `daw/`+`stems/` is thousands of SMB stat
round-trips (a live instance of the metadata cost in #1/#4). Run the real
mirror in the background; the first pass moves ~24 GB, later passes are
incremental (only changed files transfer, but the traversal cost remains).

### 8. SFX grade scan — 842 serial ID3 reads

**Status: mitigated (persistent grade cache, 2026-07)**

The Grading tab's `_scan_sfx_grades` opened every SFX MP3 to read its ID3
grade tag on each scan. Over a NAS: ~842 file opens per refresh.

*Mitigation:* grades persist in `<sfx>/.xil_grade_cache.json` keyed by
relative path + size + mtime; a rescan re-reads only new/changed files, and
grading a file patches the cache in place. Corrupt/missing cache degrades to
a clean full rescan.

### 9. Miscellaneous frictions

**Status: open — low individual cost**

- `derive_paths()` performs double `exists()` checks per call; in hot loops
  each becomes an SMB round-trip. Future: memoize layout detection per
  workspace root per process.
- The staleness engine compares mtimes; SMB servers may truncate mtime
  granularity (commonly to 1–2 s). Same-second regenerations can be judged
  "not newer." Low risk given multi-second stage runtimes, but if phantom
  staleness appears post-migration, check timestamp granularity first.
- WSL drvfs already made birthtime/ctime unreliable for produce-time
  detection (stem-manifest mtime is used instead); verify that signal
  survives the NAS move too.

---

## Migration checklist

1. Fix the `DAW/` vs `daw/` casing in `sync_to_NAS.sh` **before** first sync (#7).
2. Mount with soft/timeout options, not hang-forever defaults (#4).
3. Sync the **whole** source tree, not just `daw/` — in particular `parsed/`
   and `configs/`. Without `parsed/`, `xil daw --timeline-html` cannot
   regenerate a timeline on the NAS ("Parsed JSON not found").
4. Re-sync or regenerate every timeline HTML so it carries the new
   **relative** audio paths (#0); a timeline copied before 2026-07-11 still
   has absolute `/mnt/c/...` paths and will 403 on the NAS.
5. First GUI session on the NAS: expect the *first* Episodes scan and SFX
   grade scan to be slow (cold caches); subsequent ones should be fast. If
   they are not, the caches are not persisting — investigate before blaming
   the NAS.
6. Spot-check that a re-exported master replaces cleanly while the GUI is
   open (#6).
7. Confirm playback: pick an episode, press ▶, watch for the
   "Loading audio… N%" strip and per-layer MB counters, then sound. If the
   progress strip completes but no audio plays, see the note below on
   post-`await` autoplay.

## Post-`await` autoplay — verified working

The mix/clip prefetch calls `.play()` *after* `await fetchAudioBlob(...)`.
Browsers gate media playback on user activation; a click on ▶ sets **sticky
activation** for the (same-origin) iframe document, which survives the
`await`, so play proceeds. **Confirmed 2026-07-11** in the browser against the
NAS: S07E01 pressed ▶ → hourglass + "Loading audio…" strip → full-mix
playback started on completion. Recorded here only as the escape hatch if a
future browser/policy change blocks it: start each element muted and unmute
once playing, or fall back to native streaming with a buffered-progress
overlay instead of full prefetch.
