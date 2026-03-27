# xil-pipeline

Show-agnostic audio production pipeline that turns a markdown script into a podcast-ready MP3 via the ElevenLabs API.

A `project.json` file sets the show name; every script derives file paths from it via a shared slug. The pipeline parses scripts, generates TTS voices and SFX, assembles a rough master, exports isolated WAV layers for DAW mixing, and produces a final master MP3. Supporting utilities handle voice discovery, SFX generation, stem migration on script revisions, stale cleanup, and Studio import/export. All API-calling scripts support `--dry-run` to preview costs before spending quota.

## Installation

```bash
pip install xil-pipeline            # core dependencies
pip install xil-pipeline[all]       # all optional backends (Google GenAI, gTTS, Ollama)
pip install xil-pipeline[dev]       # development and testing
```

## Quick Start

```bash
# Scaffold a new project workspace
xil-init my-show --show "My Podcast"
cd my-show

# Scan the sample script (pre-flight check)
xil-scan scripts/sample_S01E01.md

# Parse into structured JSON
xil-parse scripts/sample_S01E01.md --episode S01E01

# Preview TTS character cost (no API calls)
xil-produce --episode S01E01 --dry-run

# Generate voice and SFX stems (requires ELEVENLABS_API_KEY)
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

## Environment

- Python 3.12+
- ElevenLabs API key via `ELEVENLABS_API_KEY` environment variable
- `ffmpeg` required for audio processing (pydub dependency)

## Development

```bash
git clone <repo-url>
cd xil-pipeline
pip install -e ".[all,dev]"
pytest tests/ -v
```

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.
