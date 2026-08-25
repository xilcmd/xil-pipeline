# Cast Config Reference — `cast_<slug>_<TAG>.json`

The cast config file assigns ElevenLabs voices to characters, configures stereo
positioning and audio effects, and optionally defines broadcast preamble and postamble
blocks read by a host. One file covers an entire episode; it is read by `xil produce`,
`xil daw`, `xil mix`, and `xil studio`.

**Filename pattern:** `cast_<slug>_<TAG>.json`
(e.g. `cast_the413_S03E02.json`, `cast_nightowls_V01C03.json`)

---

## Top-level Structure

```json
{
  "show":         "The 4 1 3",
  "season":       3,
  "episode":      2,
  "title":        "The Weight of the Letters",
  "season_title": "The Architect",
  "artist":       "XIL Pipeline",
  "preamble":     { ... },
  "postamble":    { ... },
  "cast":         { ... }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `show` | string | yes | Show name (informational; slug derived from `project.json`) |
| `season` | int | no | Season number; used in preamble `{season}` placeholder and ID3 metadata |
| `episode` | int | yes* | Episode number; used in preamble `{episode}` placeholder |
| `tag_override` | string | no | Raw non-episodic tag (e.g. `V01C03`, `D01`). When set, overrides `season`/`episode` for tag derivation |
| `title` | string | no | Episode title; used in preamble `{title}` placeholder and ID3 metadata |
| `season_title` | string | no | Season arc title; used in preamble `{season_title}` placeholder |
| `artist` | string | no | Artist credit written to MP3/WAV ID3 tags. Default: `"XIL Pipeline"` |
| `preamble` | object | no | Broadcast intro block (see below) |
| `postamble` | object | no | Broadcast outro block (see below) |
| `cast` | object | yes | Map of speaker key → voice config (see below) |

\* `episode` is required unless `tag_override` is set.

---

## `cast` Block

Each key must match a speaker key used in the production script (all lowercase, no
spaces — e.g. `adam`, `mr_patterson`). Keys are matched case-sensitively against the
`speaker` field of parsed dialogue entries.

```json
"cast": {
  "adam": { ... },
  "maya": { ... }
}
```

### Cast Member Fields

```json
"adam": {
  "full_name":        "Adam Santos",
  "voice_id":         "6wrQFQPrvgNyJY74SxwG",
  "pan":              0.0,
  "filter":           false,
  "role":             "Host/Narrator. American male, early 20s. Warm, intimate...",
  "stability":        0.5,
  "similarity_boost": 0.75,
  "style":            0.0,
  "use_speaker_boost": true,
  "language_code":    "en"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `full_name` | string | yes | Character display name (used in ID3 tags and voice sample filenames) |
| `voice_id` | string | yes | ElevenLabs voice ID. Set to `"TBD"` when not yet assigned — pipeline will skip TTS for this speaker |
| `pan` | float −1.0 to 1.0 | yes | Stereo position. `0.0` = centre, `−1.0` = full left, `1.0` = full right |
| `filter` | bool or string | yes | Voice treatment. `false`/`null` = none. See the [Voice Filters](#voice-filters) table |
| `role` | string | yes | Character description passed as `previous_text` context to ElevenLabs |
| `stability` | float 0–1 | no | Voice stability. `0.0` = expressive/variable, `1.0` = monotone/consistent. `null` uses the voice's ElevenLabs default |
| `similarity_boost` | float 0–1 | no | Adherence to the original cloned voice. `1.0` = strict match. `null` uses the voice default |
| `style` | float 0–1 | no | Style exaggeration relative to the original speaker. `0.0` = neutral. `null` uses the voice default |
| `use_speaker_boost` | bool | no | Boosts similarity to the original speaker at the cost of slightly higher latency. `null` uses the voice default |
| `language_code` | string | no | ISO 639-1 code for ElevenLabs text normalisation (e.g. `"en"`, `"de"`, `"uk"` for British English). `null` = auto-detect |

#### Voice Filters

The `filter` field names a voice treatment applied to that speaker's dialogue
stems during assembly and DAW export. Pick by how it should *sound*:

| Value | Sounds like | Use for |
|-------|-------------|---------|
| `false` / `null` | untreated | everyone in the room |
| `true` / `"phone"` | thin and band-limited (300 Hz–3 kHz), gentle | a landline caller; the long-standing default |
| `"vintage"` | dark, mono, mid-forward, no saturation | aged tape or a record player; period flashbacks |
| `"film"` | warm and reflective, rolled-off top, recessed presence, audible grain | dialogue heard from a film print (1990s indie register) |
| `"speakerphone"` | narrow, crunchy, hard-levelled, with a room slap | a phone on a table, on speaker |

For a character who is on the phone in one scene and in the room in another, prefer the
script span markers over a second cast entry — the span decides line by line. Use
`[PHONE FILTER: ENGAGES <speaker>]` for an ordinary call and
`[SPEAKERPHONE ENGAGES]` for a phone on speaker in the room. Use the cast field when a
voice is only ever heard through a phone.

Comma-separated values chain left to right, so `"vintage,phone"` and
`"phone,vintage"` give different results. Unknown names are ignored with a
warning in the log.

`"vintage"` and `"film"` are easy to confuse: both are "old and warm". `vintage`
is darker and collapses to mono; `film` keeps stereo, keeps low-end body, and
adds compression and grain. When in doubt, `film` is the one that still sounds
like a modern recording of something old.

`phone` and `speakerphone` are independent — `speakerphone` is not a stronger
`phone`, it is a separate chain with saturation, hard AGC and a short delay.

`film` and `speakerphone` are rendered through ffmpeg. If ffmpeg is unavailable
the stem passes through untreated and a warning is logged; set `XIL_STRICT_FX=1`
to make that a hard error instead.

Note that a speaker whose `filter` names a treatment **and** who speaks inside a
matching script span (see `FILM AUDIO` in the direction types reference) receives
that treatment twice. This has always been the behaviour for `VINTAGE FILTER`;
it is more audible for `film`, which compounds compression and grain.

**`phone` is the one exception.** A `phone` span treatment is skipped when the
speaker's own `filter` already applied it, because `apply_phone_filter` is a
band-limit plus a fixed +5 dB — applying it twice band-limits twice and lands
+10 dB, which is wrong rather than merely doubled. The exception is deliberately
narrow: only `phone` is deduped, and only against an identical cast filter. A
`vintage` speaker inside a `PHONE FILTER` span still gets both.

#### Pan Guide

| Value | Position | Typical use |
|-------|----------|-------------|
| `0.0` | Centre | Host/narrator, solo speaker |
| `−0.15` to `−0.3` | Left | Female character A |
| `0.15` to `0.3` | Right | Male character B |
| `−0.5` / `0.5` | Hard L/R | Distinct telephone or off-screen voice |

Avoid values beyond `±0.4` for dialogue — extreme panning fatigues listeners on headphones.

#### Voice Settings Guide

| Parameter | Lower | Higher | Suggested starting point |
|-----------|-------|--------|--------------------------|
| `stability` | More emotional variation | Predictable, consistent | `0.5` |
| `similarity_boost` | Looser, more creative | Strict clone adherence | `0.75` |
| `style` | Neutral delivery | Exaggerated expressiveness | `0.0` |

Omitting any of these fields (or setting to `null`) inherits the ElevenLabs voice default.
Use `xil voice-sample --episode TAG` to audition voice changes before committing them.

---

## `preamble` and `postamble` Blocks

Both blocks are optional and share an identical schema. They control **TTS speaking rate
only** — the spoken text lives in the production script as `PREAMBLE` and `POSTAMBLE`
section blocks, not in the cast config.

```json
"preamble":  { "speaker": "tina", "speed": 0.85 },
"postamble": { "speaker": "tina", "speed": 0.85 }
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `speaker` | string | yes | Cast key of the reader (must exist in `cast`) |
| `speed` | float 0.7–1.2 | no | TTS speaking rate override for this section. `1.0` = normal speed; `0.85` = slightly slowed for broadcast clarity |

`xil produce` applies the `speed` override when generating stems for entries whose
`section` field is `"preamble"` or `"postamble"`. All other voice settings (voice ID,
stability, pan) come from the speaker's `cast` entry as normal.

The spoken text, music cues (`[INTRO MUSIC]`, `[OUTRO MUSIC]`), and section placement
are written in the production script:

```
===

PREAMBLE

TINA  Welcome to the show. Season 1, Episode 1, Pilot.

[INTRO MUSIC]

===

COLD OPEN
…

===

POSTAMBLE

[OUTRO MUSIC]

TINA  That was the show. Thank you for listening.

===

END OF EPISODE
```

Preamble/postamble voice stems follow the same `{seq:03d}_preamble_{speaker}.mp3` naming
convention as all other dialogue stems. Music stems are generated by `xil sfx` via the
standard SFX pipeline using `INTRO MUSIC` / `OUTRO MUSIC` entries in the sfx config.

---

## Non-episodic Tags

For content that isn't organised as `S01E01` (audiobooks, shorts, one-offs), use
`tag_override` instead of `season`/`episode`:

```json
{
  "show":         "Signals",
  "tag_override": "V01C01",
  "title":        "The Signal",
  "cast": { ... }
}
```

`tag_override` is used verbatim for all file path derivations (`stems/signals/V01C01/`,
`parsed/parsed_signals_V01C01.json`, etc.). It must match `^[A-Za-z0-9_-]+$`.

---

## Auto-generated Skeleton

When you run `xil parse` with `--episode TAG` and no cast file exists yet, a skeleton
is written automatically:

```json
{
  "show": "THE 413", "season": 1, "episode": 1, "title": "...",
  "cast": {
    "adam": { "voice_id": "TBD", "pan": 0.0, "filter": false, "role": "" },
    ...
  }
}
```

Fill in each `voice_id` using `xil discover-voices` to browse available ElevenLabs
voices, then audition assignments with `xil voice-sample --episode TAG`.

---

## Field Reference Summary

### Top-level

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `show` | string | — | Required |
| `season` | int | `null` | Optional; used in tag and placeholders |
| `episode` | int | — | Required unless `tag_override` is set |
| `tag_override` | string | `null` | Non-episodic tag; overrides season/episode |
| `title` | string | `null` | Episode title for metadata and placeholders |
| `season_title` | string | `null` | Arc title for `{season_title}` placeholder |
| `artist` | string | `"XIL Pipeline"` | ID3 Artist tag on all audio outputs |
| `preamble` | object | `null` | Broadcast intro |
| `postamble` | object | `null` | Broadcast outro |
| `cast` | object | — | Required |

### Cast Member

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `full_name` | string | — | Required |
| `voice_id` | string | — | Required; `"TBD"` defers generation |
| `pan` | float −1–1 | — | Required |
| `filter` | bool | — | Required |
| `role` | string | — | Required; used as TTS context |
| `stability` | float 0–1 | voice default | Optional |
| `similarity_boost` | float 0–1 | voice default | Optional |
| `style` | float 0–1 | voice default | Optional |
| `use_speaker_boost` | bool | voice default | Optional |
| `language_code` | string | auto | Optional; ISO 639-1 |

### Preamble / Postamble

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `speaker` | string | — | Required; must be a key in `cast` |
| `speed` | float 0.7–1.2 | voice default | Optional TTS rate override for this section |

---

## Complete Minimal Example

```json
{
  "show": "Night Owls",
  "season": 1,
  "episode": 3,
  "title": "The Long Shift",
  "cast": {
    "jordan": {
      "full_name": "Jordan Blake",
      "voice_id": "abc123ElevenLabsVoiceID",
      "pan": 0.0,
      "filter": false,
      "role": "Host. American, warm, late-night energy."
    },
    "caller": {
      "full_name": "The Caller",
      "voice_id": "xyz456ElevenLabsVoiceID",
      "pan": 0.35,
      "filter": true,
      "role": "Nervous, mid-30s, calls in anonymously."
    }
  }
}
```

## Complete Preamble/Postamble Example

Cast config (`cast_nightowls_S01E03.json`):

```json
{
  "show": "Night Owls",
  "season": 1,
  "episode": 3,
  "title": "The Long Shift",
  "season_title": "Season of Static",
  "preamble":  { "speaker": "host", "speed": 0.9 },
  "postamble": { "speaker": "host", "speed": 0.9 },
  "cast": {
    "host": {
      "full_name": "Jordan Blake",
      "voice_id": "abc123ElevenLabsVoiceID",
      "pan": 0.0,
      "filter": false,
      "role": "Host. American, warm, late-night energy.",
      "stability": 0.5,
      "similarity_boost": 0.75,
      "style": 0.0,
      "use_speaker_boost": true,
      "language_code": "en"
    }
  }
}
```

Script (`scripts/nightowls/S01E03_the_long_shift.md`, beginning and end):

```
===

PREAMBLE

HOST  You're listening to Night Owls. [pause] Season of Static [pause] Episode 3 [pause] "The Long Shift".

[INTRO MUSIC]

===

COLD OPEN
…

===

POSTAMBLE

[OUTRO MUSIC]

HOST  That was Night Owls, Episode 3, "The Long Shift". [pause] Thank you for listening.

===

END OF EPISODE
```

---

## See Also

- [SFX Config Reference](sfx-config-reference.md) — intro/outro music lives in the sfx config under `INTRO MUSIC` / `OUTRO MUSIC` keys, not in the cast config
- `xil discover-voices` — browse and search available ElevenLabs voices
- `xil voice-sample --episode TAG` — generate short TTS samples to audition voice assignments
- `xil produce --dry-run --episode TAG` — preview TTS character cost before committing
