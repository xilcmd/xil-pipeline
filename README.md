# xil-pipeline

[![Documentation](https://readthedocs.org/projects/xil-pipeline/badge/?version=latest)](https://xil-pipeline.readthedocs.io/en/latest/)

Show-agnostic audio production pipeline that turns a markdown script into a podcast-ready MP3 via the ElevenLabs API.

A `project.json` file sets the show name; every script derives file paths from it via a shared slug. The pipeline parses scripts, generates TTS voices and SFX, assembles a rough master, exports isolated WAV layers for DAW mixing, and produces a final master MP3. Supporting utilities handle voice discovery, SFX generation, stem migration on script revisions, stale cleanup, and Studio import/export. All API-calling scripts support `--dry-run` to preview costs before spending quota.

## Installation

```bash
pip install xil-pipeline            # core dependencies
pip install xil-pipeline[all]       # all optional backends (Google GenAI, gTTS, Ollama)
pip install xil-pipeline[dev]       # development and testing
```

## Quick Start

See [`samples/Tech_Deep_Dive_S01E04.md`](samples/Tech_Deep_Dive_S01E04.md) for an example of the markdown script format the pipeline expects. It demonstrates dialogue, acting directions, SFX/ambience/music cues, beats, sections, and scenes. [`samples/S01E04_techdeepdive_2026-04-02.mp3`](samples/S01E04_techdeepdive_2026-04-02.mp3) is the rendered output — a two-host tech podcast segment generated entirely from that script.

```bash
# Scaffold a new project workspace (creates a copy of the sample script)
xil-init my-show --show "My Podcast"
cd my-show

# Scan the sample script (pre-flight check)
# Always run scan before parse when onboarding a new episode — it will catch
# unrecognized speakers before they silently disappear from the parsed output.
xil-scan scripts/sample_S01E01.md --speakers configs/my-show/speakers.json

# Parse into structured JSON
xil-parse scripts/sample_S01E01.md --episode S01E01 --speakers configs/my-show/speakers.json

# Preview TTS character cost (no API calls)
xil-produce --episode S01E01 --dry-run

# Generate voice and SFX stems (requires ELEVENLABS_API_KEY — see Environment below)
xil-produce --episode S01E01

# Export DAW layers for mixing in Audacity
xil-daw --episode S01E01

# Produce final master MP3
xil-master --episode S01E01
```

## Pipeline Stages

| Command | Script | Description |
|---------|--------|-------------|
| `xil-init` | xil_init | Scaffold a new project workspace |
| `xil-scan` | XILP000 | Pre-flight script scanner |
| `xil-parse` | XILP001 | Markdown script parser |
| `xil-produce` | XILP002 | Voice + SFX generation (ElevenLabs API) |
| `xil-assemble` | XILP003 | Two-pass audio assembly |
| `xil-studio` | XILP004 | ElevenLabs Studio project onboarding |
| `xil-daw` | XILP005 | DAW layer export (4 WAVs for Audacity) |
| `xil-cues` | XILP006 | Sound cues sheet ingester |
| `xil-migrate` | XILP007 | Stem migrator for script revisions |
| `xil-cleanup` | XILP008 | Stale stem cleanup |
| `xil-regen` | XILP009 | Reverse script generator |
| `xil-import` | XILP010 | ElevenLabs Studio export importer |
| `xil-master` | XILP011 | Final master MP3 export |

### Utilities

| Command | Script | Description |
|---------|--------|-------------|
| `xil-voices` | XILU001 | Voice discovery and audition |
| `xil-sfx` | XILU002 | Standalone SFX generation |
| `xil-csv-join` | XILU003 | CSV + SFX/cast annotation |
| `xil-sample` | XILU004 | Voice sample generator |
| `xil-sfx-lib` | XILU005 | SFX library discovery |
| `xil-splice` | XILU006 | Parsed JSON splice utility |

## Configuration

- **`project.json`** -- show name (derives all file paths via slug)
- **`speakers.json`** -- speaker names the parser recognizes (optional, built-in defaults for sample)
- **`cast_<slug>_<TAG>.json`** -- voice assignments, speaker settings
- **`sfx_<slug>_<TAG>.json`** -- sound effect mappings and API parameters

All scripts accept `--show` to override the show name. Resolution order: `--show` flag > `project.json` > default `"sample"`.

### Episode tag formats

The `<TAG>` portion of all file and directory names supports any string. Use `--episode` for standard episodic content or `--tag` for non-episodic formats:

| Content type | Tag format | Examples | Notes |
|---|---|---|---|
| Podcast episode | `S01E04` | `S02E11`, `S03E01` | Default — derived from script header |
| Audiobook chapter | `V01C03` | `V01C01`–`V01C20` | Volume + Chapter; use `--tag V01C03` with `xil-parse` |
| Drama short | `S01D01` | `S01D03` | Season + Drama number; or just use `S01E01` |
| Standalone one-shot | `E01` | `E01`–`E99` | No season prefix; standard `--episode E01` works |
| Bonus / special | `BONUS01` | `TRAILER`, `BONUS02` | Any string via `--tag`; use uppercase by convention |

Episodic tags (`S01E04`, `E01`) are derived automatically from the script header. All other formats require `--tag` on `xil-parse`:

```bash
xil-parse scripts/gatsby_V01C03.md --tag V01C03
xil-produce --episode V01C03 --dry-run
xil-daw --episode V01C03
```

Stems are stored under `stems/<slug>/<TAG>/`, so multiple shows and tag formats coexist safely in one workspace.

See the [SFX Reuse Guide](sfx-reuse-guide.md) for workflows that minimize ElevenLabs API credit usage by referencing existing assets in the `SFX/` library.

## Environment

- Python 3.12+
- `ffmpeg` required for audio processing (pydub dependency)

### ElevenLabs API Key

Several pipeline commands call the ElevenLabs API and require an API key:

| Commands | Requires key |
|----------|--------------|
| `xil-produce`, `xil-sfx`, `xil-studio`, `xil-sample`, `xil-cues --generate` | Yes |
| All other commands (`xil-scan`, `xil-parse`, `xil-daw`, `xil-master`, etc.) | No |

**Obtain a key:** <https://elevenlabs.io> → Profile → API Keys

**Set for the current shell session:**
```bash
export ELEVENLABS_API_KEY=your_key_here
```

**Persist it** (add to `~/.bashrc` or `~/.zshrc`):
```bash
echo 'export ELEVENLABS_API_KEY=your_key_here' >> ~/.bashrc
source ~/.bashrc
```

**Verify it is set:**
```bash
echo $ELEVENLABS_API_KEY
```

Always use `--dry-run` first to preview character cost before making API calls.

## Man Pages

Man pages for all 19 commands are included in the package and installed automatically with `pip install`.

### Enable man pages after `pip install --user`

Pages land in `~/.local/share/man/man1/`. Add this line to `~/.bashrc` (or `~/.profile` on Debian):

```bash
export MANPATH="$HOME/.local/share/man:$(manpath 2>/dev/null)"
```

Then reload your shell and use `man` normally:

```bash
source ~/.bashrc
man xil-parse
man xil-produce
man xil           # overview of all commands
```

For `apropos` / `whatis` support, update the man database once:

```bash
mandb --user-db ~/.local/share/man
```

### System-wide installs (`sudo pip install`)

Pages land in `/usr/local/share/man/man1/` which is indexed by default. Run `sudo mandb` to refresh if pages don't appear immediately.

## Development

```bash
git clone <repo-url>
cd xil-pipeline
pip install -e ".[all,dev]"
pytest tests/ -v
```

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.
