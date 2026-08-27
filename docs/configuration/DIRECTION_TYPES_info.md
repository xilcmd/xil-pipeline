# DIRECTION_TYPES — Reference and Pipeline Impact

## Definition

Defined in `XILP001_script_parser.py` at module level:

```python
DIRECTION_TYPES = ["SFX", "MUSIC", "AMBIENCE", "BEAT", "VINTAGE FILTER", "FILM AUDIO", "SPEAKERPHONE"]
```

These are the recognized sound-category prefixes for stage directions enclosed in
brackets (`[...]`) in the production script markdown. The values classify direction
entries and are stored on `ScriptEntry` as `direction_type`.

---

## Effect in XILP001

### Role 1: Prefix scanner in `classify_direction()` (lines 84–86)

```python
for dt in DIRECTION_TYPES:
    if text.strip().startswith(dt):
        return dt
```

Called with the **bracket-interior text** only (e.g., `"SFX: DOOR SLAMS"`, not the full
`"[SFX: DOOR SLAMS]"`). The first prefix match wins and is returned as the
`direction_type` value. Order in the list matters if any prefixes were overlapping,
but the current seven are disjoint.

`is_stage_direction()` fires on any `[...]` line regardless — it does not consult
`DIRECTION_TYPES`. Classification happens after detection, not before.

### Role 2: `direction_type` field on direction entries only (line 429)

```python
entries.append({
    "type": "direction",
    ...
    "direction_type": classify_direction(bracket_text),
})
```

All other entry types hardcode `"direction_type": None`:

| Entry type | `direction_type` |
|------------|-----------------|
| `section_header` | Always `None` |
| `scene_header` | Always `None` |
| `dialogue` | Always `None` |
| `direction` | `classify_direction()` result — one of the five values, or `None` |

### The BEAT quirk (lines 87–88)

```python
if text.strip() == "BEAT" or text.strip() == "LONG BEAT":
    return "BEAT"
```

`"BEAT"` appears both in `DIRECTION_TYPES` (loop at line 84) and as an explicit
exact-match fallback here. The loop handles `[BEAT: some qualifier]` variants; the
fallback handles bare `[BEAT]` and `[LONG BEAT]`. Without the fallback, bare `[BEAT]`
would be caught by the loop (since `"BEAT".startswith("BEAT")` is true), but `[LONG BEAT]`
would return `None`. The duplication is intentional coverage for both forms.

---

## Effect in XILP002 — None

Zero references. `load_production()` filters to `entry["type"] == "dialogue"` only.
All direction entries (the only entries that can have a non-`None` `direction_type`) are
discarded before voice generation begins. `direction_type` never influences which stems
are generated, what text is sent to ElevenLabs, or any filename.

---

## Effect in XILP003 — None

Zero references. Assembly reads only the speaker suffix from stem filenames and the
`config` dict (pan/filter). `direction_type` is not present in stems or in the cast
configuration.

---

## Effect in XILP005 and XILP011 — span markers

`"VINTAGE FILTER"` and `"FILM AUDIO"` are **span markers**: an `ENGAGES` entry opens a
span, the next `DISENGAGES` (or end of episode) closes it, and every dialogue stem inside
receives the corresponding voice treatment. The mapping lives in
`mix_common.SPAN_DIRECTION_TREATMENTS`:

| Direction type | Treatment applied to enclosed dialogue |
|----------------|----------------------------------------|
| `VINTAGE FILTER` | `vintage` — mono, dark, mid-forward |
| `FILM AUDIO` | `film` — warm, soft top, gentle compression and grain |
| `SPEAKERPHONE` | `speakerphone` — narrow-band, hard AGC, crunch, room slap |
| `PHONE FILTER` | `phone` — 300 Hz–3 kHz band-limit, the same treatment as the cast config `filter: phone` |

Spans of different types may overlap; treatments then stack in
`SPAN_DIRECTION_TREATMENTS` declaration order. `PHONE FILTER` is declared last
because a band-limit reads best applied after any tone shaping.

### Narrowing a span to one speaker

By default a span treats **every** dialogue line it encloses. Naming a speaker on the
`ENGAGES` marker narrows it to that speaker's lines:

```
[PHONE FILTER: ENGAGES DEZ]
```

This matters for call scenes. A writer naturally wraps the whole conversation in one
marker pair, but only the remote character belongs on the filter — a blanket span would
band-limit the in-room voice too. The speaker key is the cast config key (matched
case-insensitively against the stem filename suffix), the scope applies to every span
type, and the closing marker may repeat it or not. A bare `ENGAGES` keeps the original
treat-everything behaviour.

Naming a speaker who has no lines inside the span treats nothing — the span is still
opened and closed, it just matches no dialogue.

**Treated dialogue stays in the `dialogue` layer.** The separate
`{TAG}_layer_vintage_filter.wav` holds record-player crackle audio for the span, which is a
different mechanism that happens to share the name — it is not where filtered voices go.

No stems are generated for `FILM AUDIO`, `SPEAKERPHONE` or `PHONE FILTER` markers: `generate_sfx_config()` writes them as
`{"type": "silence", "duration_seconds": 0.0}`, which `load_sfx_entries()` skips, so they
cost no API credits and produce no files. `VINTAGE FILTER ENGAGES` differs — it binds to a
real crackle asset, and only its `DISENGAGES` marker is a pure boundary.

Marker spelling: the scanner accepts both `[FILM AUDIO ENGAGES]` and `[FILM AUDIO: ENGAGES]`,
and likewise for `SPEAKERPHONE` and `PHONE FILTER`. For `VINTAGE FILTER` only the colon-free
form is validated, for backward compatibility with scripts already in production; the mixer
itself tolerates either.

---

## The Pydantic Constraint (models.py line 46)

```python
direction_type: Literal[
    "SFX", "MUSIC", "AMBIENCE", "BEAT", "VINTAGE FILTER", "FILM AUDIO",
    "SPEAKERPHONE", "PHONE FILTER",
] | None = Field(default=None, description="Sound category for direction entries")
```

`DIRECTION_TYPES` in the parser and the `Literal` in `ScriptEntry` are **independently
maintained** — they are not derived from each other. A new sound category requires
updates in both places:

1. Add to `DIRECTION_TYPES` in `XILP001_script_parser.py`
2. Add to the `Literal` in `ScriptEntry` in `models.py`

Updating only `DIRECTION_TYPES` without updating the `Literal` will cause
`parse_script()` to raise a `ValidationError` at parse time when the new category
is returned by `classify_direction()`.

---

## Comparison with SECTION_MAP

| | `SECTION_MAP` | `DIRECTION_TYPES` |
|--|--|--|
| Controls entry detection | Yes — unknown headers are silently dropped | No — `[...]` brackets always create direction entries |
| Propagates to stem filenames | Yes — slug appears in every stem MP3 name | No |
| Used downstream in XILP002/XILP003 | Indirectly (via stem filenames) | Not at all |
| Silent failure mode | High risk — mislabels all subsequent content | Low risk — unrecognized directions get `direction_type: None`, entry still created |
| Pydantic mirror required | No | Yes — `Literal` in `ScriptEntry` must match |

---

## Adding a New Direction Type

To add `"FOLEY"` as a recognized category:

1. Add to `DIRECTION_TYPES` in `XILP001_script_parser.py`:
   ```python
   DIRECTION_TYPES = ["SFX", "MUSIC", "AMBIENCE", "BEAT", "VINTAGE FILTER", "FOLEY"]
   ```
2. Add to the `Literal` in `ScriptEntry` in `models.py`:
   ```python
   direction_type: Literal["SFX", "MUSIC", "AMBIENCE", "BEAT", "VINTAGE FILTER", "FOLEY"] | None
   ```
3. Re-run XILP001 to regenerate the parsed JSON with `"direction_type": "FOLEY"` on
   matching entries.

No changes required in XILP002 or XILP003 — direction types do not affect voice
generation or audio assembly.
