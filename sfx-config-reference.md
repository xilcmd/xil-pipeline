# SFX Config Reference — `sfx_<slug>_<TAG>.json`

The SFX config file maps every sound direction in a parsed episode script to its audio
source and playback parameters. One file covers an entire episode; it is read by
`xil produce`, `xil sfx`, `xil mix`, and `xil daw`.

**Filename pattern:** `sfx_<slug>_<TAG>.json`
(e.g. `sfx_the413_S03E02.json`, `sfx_nightowls_V01C03.json`)

---

## Top-level Structure

```json
{
  "show":    "The 4 1 3",
  "season":  3,
  "episode": 2,
  "defaults": { ... },
  "effects":  { ... }
}
```

| Field | Type | Description |
|:-------|:------ |:------------- |
| `show` | string | Show name (informational; slug is derived from `project.json`) |
| `season` | int | Season number |
| `episode` | int | Episode number |
| `defaults` | object | Fallback values applied to all effects unless overridden per-entry |
| `effects` | object | Map of direction text → effect config |

---

## `defaults` Block

All keys are optional. Per-entry values override these.

```json
"defaults": {
  "prompt_influence":          0.3,
  "volume_percentage":         20,
  "ramp_in_seconds":           1.0,
  "ramp_out_seconds":          1.0,
  "ambience_volume_percentage": 30,
  "ambience_ramp_in_seconds":   1.0,
  "ambience_ramp_out_seconds":  1.0
}
```

| Key | Type | Description|
|-----|------|-------------|
| `prompt_influence` | float 0–1 | How closely the API follows the prompt vs. free creativity. Default `0.3`. |
| `volume_percentage` | float | Playback volume for SFX and MUSIC entries. `100` = unity gain. |
| `ramp_in_seconds` | float | Fade-in duration in seconds for SFX and MUSIC entries. |
| `ramp_out_seconds` | float | Fade-out duration in seconds for SFX and MUSIC entries. |
| `ambience_volume_percentage` | float | Volume for AMBIENCE entries specifically. |
| `ambience_ramp_in_seconds` | float | Fade-in for AMBIENCE entries. |
| `ambience_ramp_out_seconds` | float | Fade-out for AMBIENCE entries. |

Category-specific keys (`ambience_*`) take precedence over the generic ones for
AMBIENCE entries. If no category-specific key exists, the generic key is the fallback.

---

## `effects` Keys

Each key must exactly match the `text` field of a parsed direction entry. The parser
emits direction text verbatim from the script, so capitalisation and punctuation matter.

```json
"AMBIENCE: RADIO BOOTH — SOFT EQUIPMENT HUM": { ... }
```

---

## Effect Entry Fields

### `type`

Controls how audio is produced. Most entries don't need this field — the default is
inferred from the key prefix (`AMBIENCE:`, `MUSIC:`, `SFX:`, `BEAT`, etc.) by the
direction classifier.

| Value | Behaviour |
|-------|-----------|
| `"sfx"` | *(default)* Generate via ElevenLabs Sound Effects API or copy from `source`. |
| `"silence"` | Generate local silent audio. No API call. |

### `source`

Path to an existing audio file (relative to the project root or absolute).

```json
"SFX: RADIO STATIC — BRIEF TUNING": {
  "source": "SFX/sfx_radio-static-tuning-transition.mp3",
  "duration_seconds": 1.5
}
```

- Resolved with `os.path.realpath()` before use (symlinks are followed; path traversal
  is blocked).
- If present and the file exists → the file is **copied** to the shared SFX library.
  No API credit is spent.
- If present but the file is missing and a `prompt` is also set → falls back to API
  generation with a warning.
- If present but the file is missing and no `prompt` is set → raises `FileNotFoundError`.

### `prompt`

Text sent to the ElevenLabs Sound Effects API when no usable `source` file is found.

```json
"MUSIC: STING OUT": {
  "prompt": "MUSIC: STING OUT",
  "duration_seconds": 15.0
}
```

- If `source` is absent → API is always called using this prompt.
- `prompt_influence` (from this entry or `defaults`) controls creativity vs. adherence.

### `duration_seconds`

Meaning depends on context:

| Context | Meaning |
|---------|---------|
| Entry has `source`, no `play_duration` | Clip the source file to this many seconds at **mix time** |
| Entry has no `source` (API generation) | Requested generation length sent to ElevenLabs API |
| `type: "silence"` | Duration of the generated silence |
| `type: "silence"`, value `0.0` | Stop-marker — inserts a boundary in the timeline with no audio |

```json
"AMBIENCE: RADIO BOOTH — SOFT EQUIPMENT HUM": {
  "source": "SFX/ambience_radio-booth.mp3",
  "duration_seconds": 30.0,
  "loop": true
}
```

> **Watch this one on `source` cues.** `xil parse` writes a default of `5.0` into every
> skeleton entry, so a long music bed dropped into a hinted cue is clipped to five
> seconds unless you change it. To see every cue this is affecting across the workspace,
> run `xil sfx-impact` — it grades each one by how much audio is being cut and names the
> edit that would restore it, without modifying anything.
>
> **To un-clip a cue, set `play_duration: 100` rather than `duration_seconds: 0`.** Both
> play the whole file, but `duration_seconds` is not in `SFX_EDIT_FIELDS`, so it is not
> replayed by the timeline edit journal — a `duration_seconds` edit reverts to `5.0` the
> next time the config is rebuilt from a skeleton, silently re-clipping the cue.
> `play_duration` survives regeneration.

### `play_duration`

Percentage of the source file to use (0–100). Applied at **stem copy time** (XILP002),
so the resulting stem file is already trimmed — all downstream tools see the correct
duration.

```json
"OUTRO MUSIC": {
  "source": "SFX/The Porch Light.mp3",
  "volume_percentage": 40,
  "play_duration": 25
}
```

- `25` = use the first 25% of "The Porch Light.mp3".
- Only valid when `source` is set.
- Mutually exclusive with `duration_seconds` for music entries; use one or the other.
- Best for music where the natural length is unknown and you want a proportional excerpt
  rather than a hard-coded second count.

### `loop`

Boolean. Controls whether the audio tiles to fill the scene duration.

| Value | Behaviour |
|-------|-----------|
| `true` *(default for AMBIENCE)* | Tile the file repeatedly to cover the full scene |
| `false` | Play the file once; silence fills the remainder of the scene |

```json
"AMBIENCE: OUTDOOR SPRING": {
  "source": "SFX/AMBPark-Outdoor_spring_ambie-Elevenlabs.mp3",
  "duration_seconds": 30.0,
  "loop": true
}
```

### `volume_percentage`

Per-entry volume override. Overrides the `defaults` category key for this entry only.

```json
"INTRO MUSIC": {
  "source": "SFX/The Porch Light.mp3",
  "volume_percentage": 40,
  "play_duration": 30
}
```

- `100` = unity gain (no change).
- `40` = 40% of full volume (roughly −8 dB).
- Applies to SFX, MUSIC, and AMBIENCE entries.

It can also come straight from the script. A `play_volume_pct` pipe-hint on the
direction sets this field, and the script wins over whatever the config holds:

```
[OUTRO MUSIC | sundy3M4_v3.mp3 | play_volume_pct=20%]
```

Reparsing (`xil parse`) or `xil sfx-hydrate` applies it; `xil regen` writes it back
into the markdown, so the annotation survives a full round-trip. Hand-editing this
field is still fine — just remember a later re-parse will restore the script's value
if the direction still carries the hint.

### `prompt_influence`

Per-entry override for ElevenLabs API creativity control (0.0–1.0).

- `0.0` = maximum creative freedom; ignores prompt text almost entirely.
- `1.0` = strict adherence to the prompt.
- Falls back to `defaults.prompt_influence` when absent.

---

## Reserved Keys

Two effect keys have special pipeline behaviour beyond normal playback:

| Key | Behaviour |
|-----|-----------|
| `"INTRO MUSIC"` | XILP002 reads `source` and copies it to `n001_preamble_sfx.mp3`. The `play_duration` trim is applied at copy time. No API call. |
| `"OUTRO MUSIC"` | Same as INTRO MUSIC but written as the postamble SFX stem. |

---

## Stop Markers

Use `type: "silence"` with `duration_seconds: 0.0` to insert an ambience boundary
without generating audio. The mixer treats this as an instruction to stop the current
ambience loop at that timeline position.

```json
"AMBIENCE: STOP": {
  "type": "silence",
  "duration_seconds": 0.0
},
"MUSIC: THEME FADES OUT": {
  "type": "silence",
  "duration_seconds": 0.0
}
```

The `source` field is ignored when `duration_seconds` is `0.0`.

---

## How Fields Combine — Decision Tree

```
effect entry
│
├── source present and file exists?
│   ├── play_duration set?  → trim to % of file at copy time (stem file is pre-trimmed)
│   └── duration_seconds set? → clip to N seconds at mix time
│
├── source absent (or file missing) and prompt set?
│   └── call ElevenLabs API; duration_seconds = requested length
│
└── type = "silence"?
    └── duration_seconds = 0.0 → stop marker (no audio)
        duration_seconds > 0   → generate local silence
```

---

## Field Reference Summary

| Field | Type | Default | When used |
|-------|------|---------|-----------|
| `type` | `"sfx"` \| `"silence"` | `"sfx"` | All entries |
| `source` | string (path) | — | Source-based entries |
| `prompt` | string | — | API-generated entries |
| `duration_seconds` | float | — | Clip/generate/silence length |
| `play_duration` | float (0–100) | — | Source music % trim |
| `loop` | bool | `true` for AMBIENCE | Tile vs. one-shot |
| `volume_percentage` | float | from `defaults` | Per-entry volume |
| `prompt_influence` | float 0–1 | from `defaults` | API creativity control |

---

## Complete Entry Examples

### Silence Beat

```json
"BEAT": {
  "type": "silence",
  "duration_seconds": 1.0
}
```

### Looping Ambience (source)

```json
"AMBIENCE: RADIO BOOTH — SOFT EQUIPMENT HUM, SLIGHT STATIC, INTIMATE": {
  "source": "SFX/ambience_radio-booth-soft-equipment-hum-slight-static-intimate.mp3",
  "duration_seconds": 30.0,
  "loop": true
}
```

### One-shot SFX (source, clipped)

```json
"SFX: RADIO STATIC — BRIEF TUNING": {
  "source": "SFX/sfx_radio-static-tuning-transition.mp3",
  "duration_seconds": 1.5
}
```

### Music Sting (source, clipped)

```json
"MUSIC: THEME — BRIEF STING, RETURNS SOFT": {
  "source": "SFX/MUSCStngr-Short_medieval_melod-Elevenlabs.mp3",
  "duration_seconds": 5.0
}
```

### Music with Percentage Trim (source, play_duration)

```json
"OUTRO MUSIC": {
  "source": "SFX/The Porch Light.mp3",
  "volume_percentage": 40,
  "play_duration": 25
}
```

### API-Generated Music

```json
"MUSIC: STING OUT": {
  "prompt": "MUSIC: STING OUT",
  "duration_seconds": 15.0
}
```

### Ambience Stop Marker

```json
"MUSIC: UNDERSCORE FADES OUT": {
  "type": "silence",
  "duration_seconds": 0.0
}
```

---

## See Also

- [SFX Reuse Guide](../guides/sfx-reuse-guide.md) — how to minimise ElevenLabs credit spend
- [DIRECTION_TYPES](DIRECTION_TYPES_info.md) — how the parser classifies direction text
- `xil sfx --dry-run` — preview EXISTS / CACHED / NEW status before generating
- `xil produce --dry-run` — full voice + SFX cost estimate
