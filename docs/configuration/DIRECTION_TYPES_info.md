# DIRECTION_TYPES — Reference and Pipeline Impact

## Definition

Defined in `XILP001_script_parser.py` at module level:

```python
DIRECTION_TYPES = ["SFX", "MUSIC", "AMBIENCE", "BEAT", "VINTAGE FILTER"]
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
but the current five are disjoint.

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

## Effect in XILP005 and XILP011 — VINTAGE FILTER

`"VINTAGE FILTER"` entries are used by `mix_common.collect_stem_plans()` to determine
span boundaries. A `[VINTAGE FILTER: ENGAGES]` entry marks the start of a vintage-filter
span; `[VINTAGE FILTER: DISENGAGES]` marks the end. During DAW layer export (XILP005) and
master export (XILP011), dialogue stems within the span are written to the `vintage_filter`
WAV layer rather than the standard `dialogue` layer. The fifth `LAYER_SUFFIXES` entry is
`"vintage_filter"`. No stems are generated for the direction entries themselves — they serve
only as span markers in the entries index.

---

## The Pydantic Constraint (models.py line 46)

```python
direction_type: Literal["SFX", "MUSIC", "AMBIENCE", "BEAT", "VINTAGE FILTER"] | None = Field(
    default=None, description="Sound category for direction entries"
)
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
