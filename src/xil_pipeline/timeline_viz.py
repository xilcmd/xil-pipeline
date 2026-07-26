# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Multitrack timeline visualization for the audio pipeline.

Renders a visual representation of asset placement across all four audio
layers (dialogue, ambience, music, SFX).  Two output formats are supported:

- **Terminal ASCII timeline** — printed to stdout, auto-scaled to terminal width.
- **HTML interactive timeline** — self-contained file with hover tooltips and zoom.

No pydub dependency — consumes label tuples only.

Usage (from XILP005):
    python XILP005_daw_export.py --episode S02E03 --timeline
    python XILP005_daw_export.py --episode S02E03 --timeline-html
"""

import html
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class LayerSpan:
    """A single asset placement on the timeline.

    Attributes:
        start_s: Start time in seconds.
        end_s: End time in seconds.
        label: Human-readable label (speaker name, SFX text, etc.).
        ramp_in_s: Fade-in duration in seconds, or ``None`` if not set.
        ramp_out_s: Fade-out duration in seconds, or ``None`` if not set.
        play_duration: Percentage of file to play, or ``None`` if not set.
        snippet: First 5 words of dialogue text for HTML tooltip, or ``None``.
        volume_pct: Volume percentage (100 = unity), or ``None`` if not set.
        seq: Sequence number from the parsed script, or ``None``.
    """

    start_s: float
    end_s: float
    label: str
    ramp_in_s: float | None = None
    ramp_out_s: float | None = None
    play_duration: float | None = None
    snippet: str | None = None
    volume_pct: float | None = None
    seq: int | None = None
    tts_model: str | None = None


@dataclass
class TimelineData:
    """Complete timeline data for all four layers.

    Attributes:
        tag: Episode tag (e.g. ``"S02E03"``).
        total_duration_s: Total episode duration in seconds.
        layers: Mapping of layer name to list of :class:`LayerSpan` instances.
    """

    tag: str
    total_duration_s: float
    layers: dict[str, list[LayerSpan]] = field(default_factory=dict)


def build_timeline_data(
    tag: str,
    total_s: float,
    dlg_labels: list,
    amb_labels: list,
    mus_labels: list,
    sfx_labels: list,
    vf_labels: list | None = None,
) -> TimelineData:
    """Wrap the layer label lists into a :class:`TimelineData` object.

    Label tuples may be 3-element ``(start_s, end_s, text)``,
    5-element ``(start_s, end_s, text, ramp_in_s, ramp_out_s)``,
    6-element ``(start_s, end_s, text, ramp_in_s, ramp_out_s, play_duration)``, or
    7-element ``(start_s, end_s, text, ramp_in_s, ramp_out_s, play_duration, snippet)``.

    Args:
        tag: Episode tag.
        total_s: Total episode duration in seconds.
        dlg_labels: Dialogue label 7-tuples ``(start_s, end_s, speaker, None, None, None, snippet)``.
        amb_labels: Ambience label tuples (may carry ramp data).
        mus_labels: Music label tuples (may carry ramp data).
        sfx_labels: SFX label tuples.
        vf_labels: Vintage filter label tuples (may carry ramp data).

    Returns:
        A populated :class:`TimelineData` instance.
    """
    def to_spans(labels):
        spans = []
        for tup in labels:
            s, e, t = tup[0], tup[1], tup[2]
            ri = tup[3] if len(tup) > 3 else None
            ro = tup[4] if len(tup) > 4 else None
            pd = tup[5] if len(tup) > 5 else None
            sn = tup[6] if len(tup) > 6 else None
            vp = tup[7] if len(tup) > 7 else None
            sq = tup[8] if len(tup) > 8 else None
            tm = tup[9] if len(tup) > 9 else None
            spans.append(LayerSpan(s, e, t, ri, ro, pd, sn, vp, sq, tm))
        return spans

    layers = {
        "dialogue":       to_spans(dlg_labels),
        "ambience":       to_spans(amb_labels),
        "music":          to_spans(mus_labels),
        "sfx":            to_spans(sfx_labels),
        "vintage_filter": to_spans(vf_labels or []),
    }
    return TimelineData(tag=tag, total_duration_s=total_s, layers=layers)


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"


def render_terminal_timeline(data: TimelineData, width: int | None = None) -> str:
    """Render a multi-line Unicode timeline string for terminal display.

    Args:
        data: Timeline data from :func:`build_timeline_data`.
        width: Terminal width in characters.  If ``None``, auto-detected
            via :func:`shutil.get_terminal_size`.

    Returns:
        Multi-line string suitable for printing to stdout.
    """
    if width is None:
        width = shutil.get_terminal_size((120, 24)).columns

    total_s = data.total_duration_s
    if total_s <= 0:
        return f"--- Timeline: {data.tag} (0:00) ---\n  (no audio)\n"

    # Layout constants
    label_col = 12  # width of "  DIALOGUE  " left column
    track_width = max(width - label_col - 2, 20)

    # Choose ruler interval: 30s for short episodes, 60s for longer
    if total_s <= 180:
        interval = 30
    elif total_s <= 600:
        interval = 60
    else:
        interval = 120

    lines = []
    lines.append(f"--- Timeline: {data.tag} ({_format_time(total_s)}) ---")
    lines.append("")

    # ── Time ruler ──
    ruler_line = " " * label_col
    num_ticks = int(total_s // interval) + 1
    for i in range(num_ticks):
        t = i * interval
        col = int(t / total_s * track_width) if total_s > 0 else 0
        if col >= track_width:
            break
        time_str = _format_time(t)
        # Place time label at col position
        pad = col - (len(ruler_line) - label_col)
        if pad > 0:
            ruler_line += " " * pad
        ruler_line += time_str

    # Tick marks line
    tick_chars = [" "] * track_width
    for i in range(num_ticks):
        t = i * interval
        col = int(t / total_s * track_width) if total_s > 0 else 0
        if col >= track_width:
            break
        if i == 0:
            tick_chars[col] = "├"
        elif col == track_width - 1:
            tick_chars[col] = "┤"
        else:
            tick_chars[col] = "┼"
    # Fill between ticks with ─
    for idx in range(track_width):
        if tick_chars[idx] == " ":
            tick_chars[idx] = "─"

    lines.append(ruler_line)
    lines.append(" " * label_col + "".join(tick_chars))
    lines.append("")

    # ── Layer rendering ──
    layer_config = [
        ("dialogue",       "DIALOGUE",       "█"),
        ("ambience",       "AMBIENCE",       "▓"),
        ("music",          "MUSIC",          "█"),
        ("sfx",            "SFX",            "█"),
        ("vintage_filter", "VTG FILTER",     "▒"),
    ]

    for layer_key, layer_name, fill_char in layer_config:
        spans = data.layers.get(layer_key, [])
        if not spans:
            continue

        # Build the bar row
        bar = [" "] * track_width
        label_positions: list[tuple[int, str]] = []

        for span in spans:
            col_start = int(span.start_s / total_s * track_width)
            col_end = int(span.end_s / total_s * track_width)
            col_start = max(0, min(col_start, track_width - 1))
            col_end = max(col_start + 1, min(col_end, track_width))

            # Short items (< 1 col) get a dot for SFX/BEAT
            if col_end - col_start <= 1 and layer_key == "sfx":
                char = "·" if span.end_s - span.start_s < 1.5 else fill_char
            else:
                char = fill_char

            for c in range(col_start, col_end):
                bar[c] = char

            # Truncate label to fit
            label = span.label
            if len(label) > 12:
                label = label[:11] + "…"
            label_positions.append((col_start, label))

        # Build label row
        label_row = [" "] * track_width
        for col, lbl in label_positions:
            end = min(col + len(lbl), track_width)
            # Don't overwrite existing labels
            if all(label_row[i] == " " for i in range(col, end)):
                for i, ch in enumerate(lbl):
                    if col + i < track_width:
                        label_row[col + i] = ch

        # Format output
        name_padded = f"  {layer_name:<{label_col - 2}}"
        lines.append(name_padded + "".join(bar))
        lines.append(" " * label_col + "".join(label_row))
        lines.append("")

    return "\n".join(lines)


_MODAL_CSS = """\
  #sfx-modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.65); z-index:2000; }
  #sfx-modal { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
    background:#2a2a3e; border:1px solid #555; border-radius:8px; padding:20px;
    min-width:300px; max-width:460px; color:#eee; font-size:13px; }
  #sfx-modal h3 { margin-bottom:12px; font-size:0.9em; color:#b0b0ff; word-break:break-all; font-weight:600; }
  .sfx-field-wrap { margin-bottom:10px; }
  .sfx-field-wrap label { display:block; font-size:11px; color:#aaa; margin-bottom:3px; text-transform:uppercase; letter-spacing:0.04em; }
  .sfx-field-wrap input[type=number] { width:100%; background:#111; color:#eee; border:1px solid #444; border-radius:4px; padding:4px 8px; font-size:13px; }
  .sfx-field-wrap input[type=number]:focus { outline:none; border-color:#6688cc; }
  .sfx-field-wrap input[disabled] { opacity:0.3; cursor:not-allowed; }
  .sfx-help { font-size:10px; color:#666; margin-top:3px; line-height:1.3; }
  #sfx-modal-status { min-height:1.2em; font-size:12px; margin-top:8px; color:#aaa; }
  .sfx-modal-btns { margin-top:14px; display:flex; gap:8px; }
  .sfx-modal-btns button { background:#333; color:#ccc; border:1px solid #555; padding:5px 14px;
    border-radius:4px; cursor:pointer; font-size:12px; }
  .sfx-modal-btns button:hover:not([disabled]) { background:#444; }
  .sfx-modal-btns button[disabled] { opacity:0.45; cursor:not-allowed; }
  #sfx-modal-save { background:#1a3a1a; border-color:#4caf50; color:#8fca8f; }
  #sfx-modal-save:hover:not([disabled]) { background:#1e481e; }
"""

_MODAL_HTML = """\
<div id="sfx-modal-overlay">
  <div id="sfx-modal">
    <h3 id="sfx-modal-title"></h3>
    <div id="sfx-modal-fields"></div>
    <div id="sfx-modal-status"></div>
    <div class="sfx-modal-btns">
      <button id="sfx-modal-save">Save</button>
      <button id="sfx-modal-cancel">Cancel</button>
    </div>
  </div>
</div>
"""

_MODAL_JS = """\
// === Right-click Sound Profile Editor ===
function _sfxField(id, label, min, max, step, val, pfx, defs, help, disabled) {
  const catDef = defs[pfx + id];
  const globDef = defs[id];
  const ph = catDef != null ? 'category default: ' + catDef
           : (globDef != null ? 'global default: ' + globDef : 'no default set');
  const va = (val != null) ? ' value="' + val + '"' : '';
  const da = disabled ? ' disabled' : '';
  return '<div class="sfx-field-wrap"><label>' + label + '</label>' +
    '<input type="number" id="sfxf-' + id + '" min="' + min + '" max="' + max +
    '" step="' + step + '"' + va + ' placeholder="' + ph + '"' + da + '>' +
    '<div class="sfx-help">' + help + '</div></div>';
}

let _sfxModalCtx = null;  // {key, layer, effect, defaults} of the open editor

function _showSfxModal(key, layer, effect, defaults) {
  _sfxModalCtx = {key: key, layer: layer, effect: effect, defaults: defaults};
  const isAmb = layer === 'ambience';
  const pfx = layer + '_';
  document.getElementById('sfx-modal-title').textContent = key;
  document.getElementById('sfx-modal-fields').innerHTML =
    _sfxField('volume_percentage', 'Volume %', 0, 200, 1,
      effect.volume_percentage ?? null, pfx, defaults,
      'Per-cue override (0–200, 100 = unity). Clear to inherit ' + pfx + 'volume_percentage default.', false) +
    _sfxField('ramp_in_seconds', 'Ramp In (s)', 0, 30, 0.1,
      effect.ramp_in_seconds ?? null, pfx, defaults,
      'Fade-in duration in seconds. Clear to inherit ' + pfx + 'ramp_in_seconds default.', false) +
    _sfxField('ramp_out_seconds', 'Ramp Out (s)', 0, 30, 0.1,
      effect.ramp_out_seconds ?? null, pfx, defaults,
      'Fade-out duration in seconds. Clear to inherit ' + pfx + 'ramp_out_seconds default.', false) +
    _sfxField('play_duration', 'Play Duration %', 0, 100, 1,
      effect.play_duration ?? null, pfx, defaults,
      'Percentage of clip to play (0–100). Not applicable to AMBIENCE.', isAmb);
  const modal = document.getElementById('sfx-modal');
  modal.dataset.effectKey = key;
  modal.dataset.layer = layer;
  const st = document.getElementById('sfx-modal-status');
  st.textContent = '';
  st.style.color = '#aaa';
  document.getElementById('sfx-modal-save').disabled = false;
  document.getElementById('sfx-modal-overlay').style.display = 'block';
}

document.addEventListener('dblclick', function(e) {
  const span = e.target.closest('.span[data-effect-key]');
  if (!span) return;
  e.stopPropagation();
  clearTimeout(clickTimer);  // cancel the deferred single-click preview
  const key = span.dataset.effectKey;
  const layer = span.dataset.layer;
  fetch('/xil/get-sfx?slug=' + encodeURIComponent(XIL_SLUG) +
        '&tag=' + encodeURIComponent(XIL_TAG) +
        '&key=' + encodeURIComponent(key))
    .then(function(r) { return r.json(); })
    .then(function(d) { _showSfxModal(key, layer, d.effect || {}, d.defaults || {}); })
    .catch(function(err) { console.error('sfx-modal:', err); });
});

document.getElementById('sfx-modal-cancel').addEventListener('click', function() {
  document.getElementById('sfx-modal-overlay').style.display = 'none';
});
document.getElementById('sfx-modal-overlay').addEventListener('click', function(e) {
  if (e.target === this) this.style.display = 'none';
});
// Push a saved edit into the in-page span data so the next click-to-play
// preview reflects it immediately, without waiting for a daw regen.
function _applySfxEditToSpans(payload) {
  if (!_sfxModalCtx) return;
  const layer = _sfxModalCtx.layer, defaults = _sfxModalCtx.defaults;
  function eff(field) {
    const v = payload[field];
    if (v != null) return v;
    const cat = defaults[layer + '_' + field];
    return cat != null ? cat : (defaults[field] != null ? defaults[field] : null);
  }
  const spans = (DATA.layers[layer] || []).filter(function(sp) { return sp.label === payload.key; });
  const durS = _sfxModalCtx.effect && _sfxModalCtx.effect.duration_seconds;
  spans.forEach(function(sp) {
    sp.volume_pct = eff('volume_percentage');
    sp.ramp_in_s = eff('ramp_in_seconds');
    sp.ramp_out_s = eff('ramp_out_seconds');
    if (layer !== 'ambience') {
      sp.play_duration = payload.play_duration;
      if (durS) {
        const pd = payload.play_duration != null ? payload.play_duration : 100;
        sp.end_s = sp.start_s + durS * pd / 100;
      }
    }
  });
}

document.getElementById('sfx-modal-save').addEventListener('click', function() {
  const modal = document.getElementById('sfx-modal');
  function getVal(id) {
    const el = document.getElementById('sfxf-' + id);
    if (!el || el.disabled) return null;
    const v = el.value.trim();
    return v === '' ? null : parseFloat(v);
  }
  const payload = {
    slug: XIL_SLUG, tag: XIL_TAG, key: modal.dataset.effectKey,
    volume_percentage: getVal('volume_percentage'),
    ramp_in_seconds: getVal('ramp_in_seconds'),
    ramp_out_seconds: getVal('ramp_out_seconds'),
    play_duration: getVal('play_duration'),
  };
  const st = document.getElementById('sfx-modal-status');
  const btn = this;
  st.style.color = '#aaa';
  st.textContent = 'Saving…';
  btn.disabled = true;
  fetch('/xil/update-sfx', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      _applySfxEditToSpans(payload);
      st.style.color = '#4caf50';
      st.textContent = d.message || 'Saved.';
      setTimeout(function() {
        document.getElementById('sfx-modal-overlay').style.display = 'none';
        btn.disabled = false;
      }, 1800);
    } else {
      st.style.color = '#ef5350';
      st.textContent = 'Error: ' + (d.error || 'Save failed.');
      btn.disabled = false;
    }
  })
  .catch(function(err) {
    st.style.color = '#ef5350';
    st.textContent = 'Network error: ' + err.message;
    btn.disabled = false;
  });
});
"""

_TRANSPORT_CSS = """\
  #transport { display:flex; gap:14px; align-items:center; margin-bottom:10px;
    background:#222238; padding:6px 12px; border-radius:4px; }
  #transport-play, #transport-restart { background:#333; color:#eee; border:1px solid #666; width:34px; height:26px;
    border-radius:4px; cursor:pointer; font-size:13px; line-height:1; }
  #transport-play:hover, #transport-restart:hover { background:#444; }
  #transport-time { font-size:12px; color:#b0b0ff; font-variant-numeric:tabular-nums; min-width:110px; }
  #transport-mutes { display:flex; gap:10px; }
  .mute-caption { font-size:11px; color:#777; text-transform:uppercase; letter-spacing:0.05em; align-self:center; }
  .mute-toggle { font-size:11px; color:#aaa; cursor:pointer; user-select:none; display:flex; align-items:center; gap:3px; }
  .mute-toggle input { cursor:pointer; margin:0; }
  .mute-toggle input:disabled { opacity:0.3; cursor:not-allowed; }
  .mute-toggle:has(input:checked) { color:#ef5350; text-decoration:line-through; }
  #transport-hint { display:none; font-size:12px; color:#777; font-style:italic; }
  #playhead { display:none; position:absolute; top:0; bottom:0; width:2px;
    background:#ff5252; z-index:50; pointer-events:none;
    box-shadow:0 0 4px rgba(255,82,82,0.7); }
  #ruler { cursor: pointer; }
"""

_TRANSPORT_HTML = """\
<div id="transport">
  <button id="transport-restart" title="Return to start" aria-label="Return to start">&#9198;</button>
  <button id="transport-play" title="Play/pause full mix" aria-label="Play/pause full mix">&#9205;</button>
  <span id="transport-time">0:00 / 0:00</span>
  <span id="transport-mutes">
    <span class="mute-caption">Mute:</span>
    <label class="mute-toggle" title="Mute the Dialogue layer"><input type="checkbox" data-layer="dialogue">Dlg</label>
    <label class="mute-toggle" title="Mute the SFX layer"><input type="checkbox" data-layer="sfx">SFX</label>
    <label class="mute-toggle" title="Mute the Music layer"><input type="checkbox" data-layer="music">Mus</label>
    <label class="mute-toggle" title="Mute the Ambience layer"><input type="checkbox" data-layer="ambience">Amb</label>
    <label class="mute-toggle" title="Mute the Vintage Filter layer"><input type="checkbox" data-layer="vintage_filter">VF</label>
  </span>
  <span id="transport-hint">run xil daw to enable full-mix playback</span>
</div>
"""

_TRANSPORT_JS = """\
// === DAW-style transport: synced full-mix playback + playhead ===
const MIX_ORDER = ['dialogue','sfx','music','ambience','vintage_filter'];
let mixEls = null, mixMaster = null, mixPlaying = false;
let mixState = 'idle';   // idle -> loading -> ready
let mixAbort = null;     // AbortController for the in-flight prefetch
const mixMuted = {};     // layer -> muted?  (recorded even before the mix is built)
let phRaf = null, syncIv = null;

function hideMixLoading() {
  const box = document.getElementById('mix-loading');
  box.classList.remove('visible');
  box.classList.remove('error');
}

function showMixError(msg) {
  const box = document.getElementById('mix-loading');
  box.classList.add('visible');
  box.classList.add('error');
  document.getElementById('mix-loading-text').textContent = msg;
  document.getElementById('mix-loading-layers').innerHTML = '';
}

// Prefetch all layer WAVs to Blobs with an aggregate byte-progress readout.
// Resolves true once the mix elements exist; false when idle-with-no-audio,
// already loading (caller should treat the click as a cancel), or aborted.
async function ensureMix() {
  if (mixState === 'ready') return true;
  if (mixState === 'loading') return false;
  const keys = MIX_ORDER.filter(function(k) { return LAYER_AUDIO[k]; });
  if (!keys.length) return false;
  mixState = 'loading';
  mixAbort = new AbortController();
  const prog = {};
  keys.forEach(function(k) { prog[k] = {received: 0, total: 0}; });
  const box = document.getElementById('mix-loading');
  box.classList.add('visible');
  box.classList.remove('error');
  document.getElementById('mix-loading-text').textContent = 'Loading audio\\u2026';
  document.getElementById('mix-loading-layers').innerHTML = '';
  document.getElementById('transport-play').innerHTML = '&#8987;';
  function repaint() {
    let recv = 0, total = 0, allKnown = true;
    const rows = [];
    for (const k of keys) {
      recv += prog[k].received;
      total += prog[k].total;
      if (!prog[k].total) allKnown = false;
      rows.push('<span>' + LABELS[k] + ' ' + fmtMB(prog[k].received) +
                (prog[k].total ? ' / ' + fmtMB(prog[k].total) : '') + '</span>');
    }
    let txt = 'Loading audio\\u2026 ';
    if (allKnown && total) {
      txt += Math.round(recv / total * 100) + '% (' + fmtMB(recv) + ' / ' + fmtMB(total) + ')';
    } else {
      txt += fmtMB(recv);
    }
    document.getElementById('mix-loading-text').textContent = txt;
    document.getElementById('mix-loading-layers').innerHTML = rows.join('');
  }
  try {
    const urls = await Promise.all(keys.map(function(k) {
      return fetchAudioBlob(LAYER_AUDIO[k], function(received, total) {
        prog[k].received = received;
        prog[k].total = total;
        repaint();
      }, mixAbort.signal);
    }));
    mixEls = {};
    keys.forEach(function(k, i) {
      const a = new Audio(urls[i]);
      a.preload = 'auto';
      a.muted = !!mixMuted[k];
      mixEls[k] = a;
    });
    mixMaster = mixEls['dialogue'] || mixEls[keys[0]];
    mixMaster.addEventListener('ended', pauseMix);
    mixState = 'ready';
    hideMixLoading();
    document.getElementById('transport-play').innerHTML = '&#9205;';
    return true;
  } catch (err) {
    mixState = 'idle';
    mixEls = null;
    mixMaster = null;
    document.getElementById('transport-play').innerHTML = '&#9205;';
    if (err && err.name === 'AbortError') {
      hideMixLoading();
    } else {
      showMixError('Load failed: ' + (err && err.message ? err.message : err));
    }
    return false;
  }
}

async function playMix() {
  if (mixState === 'loading') { mixAbort.abort(); return; }  // toggle = cancel
  const token = ++_playToken;
  if (!await ensureMix()) return;
  if (token !== _playToken) return;  // user clicked a clip mid-load
  if (!mixMaster) return;
  // Mutual exclusion: stop the single-stem preview
  clearTimeout(clickTimer);
  clearTimeout(stopTimer);
  document.getElementById('audio-el').pause();
  const t = mixMaster.currentTime;
  for (const k in mixEls) {
    if (mixEls[k] !== mixMaster) mixEls[k].currentTime = t;
    mixEls[k].play();
  }
  mixPlaying = true;
  document.getElementById('transport-play').innerHTML = '&#9208;';
  document.getElementById('playhead').style.display = 'block';
  phTick();
  syncIv = setInterval(syncSlaves, 1000);
}

function pauseMix() {
  if (!mixEls) return;
  for (const k in mixEls) mixEls[k].pause();
  mixPlaying = false;
  document.getElementById('transport-play').innerHTML = '&#9205;';
  cancelAnimationFrame(phRaf);
  clearInterval(syncIv);
}

function syncSlaves() {
  const t = mixMaster.currentTime;
  for (const k in mixEls) {
    const el = mixEls[k];
    if (el !== mixMaster && Math.abs(el.currentTime - t) > 0.06) el.currentTime = t;
  }
}

function phTick() {
  if (!mixMaster) return;
  const W = BASE_WIDTH * zoom;
  const t = mixMaster.currentTime;
  const phx = 90 + t / TOTAL * W;
  document.getElementById('playhead').style.left = phx + 'px';
  document.getElementById('transport-time').textContent = fmtTime(t) + ' / ' + fmtTime(TOTAL);
  if (mixPlaying) {
    // keep the playhead visible while zoomed in
    const tc = document.getElementById('tc');
    if (phx < tc.scrollLeft + 90 || phx > tc.scrollLeft + tc.clientWidth - 40) {
      tc.scrollLeft = Math.max(phx - 120, 0);
    }
    phRaf = requestAnimationFrame(phTick);
  }
}

async function seekTo(t) {
  if (!await ensureMix()) return;
  if (!mixMaster) return;
  t = Math.max(0, Math.min(t, TOTAL));
  for (const k in mixEls) mixEls[k].currentTime = t;
  document.getElementById('playhead').style.display = 'block';
  phTick();
}

document.getElementById('transport-play').addEventListener('click', function() {
  if (mixPlaying) pauseMix(); else playMix();
});

// Return to start (accessibility): seekTo(0) preserves play/pause state —
// keeps playing if playing, stays paused if paused.
document.getElementById('transport-restart').addEventListener('click', function() {
  seekTo(0);
});

document.addEventListener('keydown', function(e) {
  // Home = return to start. Ignore while typing or when the SFX dialog is open.
  const tag = (e.target && e.target.tagName) || '';
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  const modal = document.getElementById('sfx-modal-overlay');
  if (modal && modal.style.display !== 'none' && modal.style.display !== '') return;
  if (e.key === 'Home') { e.preventDefault(); seekTo(0); }
});

document.getElementById('ruler').addEventListener('click', function(e) {
  const rect = this.getBoundingClientRect();
  const x = e.clientX - rect.left - 90;
  if (x < 0) return;
  seekTo(x / (BASE_WIDTH * zoom) * TOTAL);
});

document.getElementById('mix-loading-cancel').addEventListener('click', function() {
  if (mixState === 'loading' && mixAbort) mixAbort.abort();
  else hideMixLoading();  // dismiss a lingering error state
});

// Record mute preference without triggering the prefetch — a pre-load
// toggle must not start a multi-GB download over the NAS.
document.querySelectorAll('.mute-toggle input').forEach(function(cb) {
  if (!LAYER_AUDIO[cb.dataset.layer]) cb.disabled = true;
  cb.addEventListener('change', function() {
    mixMuted[this.dataset.layer] = this.checked;
    if (mixEls && mixEls[this.dataset.layer]) mixEls[this.dataset.layer].muted = this.checked;
  });
});

if (!Object.keys(LAYER_AUDIO).length) {
  document.getElementById('transport-restart').style.display = 'none';
  document.getElementById('transport-play').style.display = 'none';
  document.getElementById('transport-time').style.display = 'none';
  document.getElementById('transport-mutes').style.display = 'none';
  document.getElementById('transport-hint').style.display = 'inline';
} else {
  document.getElementById('transport-time').textContent = '0:00 / ' + fmtTime(TOTAL);
}
"""

_LOADER_CSS = """\
  #mix-loading { display:none; align-items:center; gap:12px; margin-bottom:10px;
    background:#1d2b3a; border:1px solid #345; padding:6px 12px; border-radius:4px; }
  #mix-loading.visible { display:flex; }
  #mix-loading.error { background:#3a1d1d; border-color:#844; }
  #mix-loading.error #mix-loading-text { color:#ef9a9a; }
  #mix-loading-cancel { background:#333; color:#ccc; border:1px solid #666; padding:2px 10px;
    border-radius:3px; cursor:pointer; font-size:11px; }
  #mix-loading-cancel:hover { background:#444; }
  #mix-loading-text { font-size:12px; color:#9fd0ff; font-variant-numeric:tabular-nums; }
  #mix-loading-layers { display:flex; gap:10px; font-size:11px; color:#7a8aa0;
    font-variant-numeric:tabular-nums; }
"""

_LOADER_HTML = """\
<div id="mix-loading">
  <button id="mix-loading-cancel" title="Cancel loading">Cancel</button>
  <span id="mix-loading-text">Loading audio&hellip;</span>
  <div id="mix-loading-layers"></div>
</div>
"""

_LOADER_JS = """\
// === NAS-aware audio prefetch: fetch-to-Blob with byte progress ===
// Workspace reads may cross a NAS mount; streaming <audio> from
// /gradio_api/file= gives zero feedback while gigabytes trickle in.
const _blobCache = {};  // full URL (incl. ?v= cache-buster) -> object URL
let _playToken = 0;     // bumped on each play intent; stale awaits bail

function fmtMB(bytes) {
  return Math.round(bytes / 1048576) + ' MB';
}

async function fetchAudioBlob(url, onProgress, signal) {
  if (_blobCache[url]) return _blobCache[url];
  const resp = await fetch(url, {signal: signal});
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  const total = parseInt(resp.headers.get('Content-Length') || '0', 10);
  const reader = resp.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const r = await reader.read();
    if (r.done) break;
    chunks.push(r.value);
    received += r.value.length;
    if (onProgress) onProgress(received, total);
  }
  const objUrl = URL.createObjectURL(new Blob(chunks));
  _blobCache[url] = objUrl;
  return objUrl;
}
"""

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Timeline: {tag}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }}
  h1 {{ font-size: 1.3em; margin-bottom: 4px; color: #e0e0ff; }}
  .subtitle {{ color: #888; font-size: 0.9em; margin-bottom: 16px; }}
  .timeline-container {{ position: relative; overflow-x: auto; overflow-y: visible; padding-bottom: 20px; }}
  .timeline-inner {{ position: relative; min-width: 100%; }}
  .ruler {{ height: 30px; position: relative; border-bottom: 1px solid #444; margin-bottom: 4px; }}
  .ruler-tick {{ position: absolute; top: 0; height: 100%; border-left: 1px solid #555; }}
  .ruler-tick span {{ position: absolute; top: 2px; left: 4px; font-size: 11px; color: #999; white-space: nowrap; }}
  .layer {{ display: flex; align-items: center; height: 38px; margin-bottom: 2px; }}
  .layer-label {{ width: 90px; flex-shrink: 0; font-size: 12px; font-weight: 600; text-transform: uppercase; padding-right: 8px; text-align: right; }}
  .layer-track {{ position: relative; flex: 1; height: 28px; background: #222238; border-radius: 3px; overflow: visible; }}
  .span {{ position: absolute; height: 100%; border-radius: 2px; cursor: pointer; min-width: 2px; opacity: 0.85; transition: opacity 0.15s; }}
  .span:hover {{ opacity: 1; z-index: 10; }}
  .span.playing {{ outline: 2px solid rgba(255,255,255,0.9); opacity: 1; z-index: 11; }}
  .span.loading {{ outline: 2px dashed rgba(255,255,255,0.7); opacity: 1; z-index: 11; }}
  #floattip {{ display: none; position: fixed; background: #333; color: #fff; padding: 6px 10px; border-radius: 4px;
    font-size: 12px; white-space: nowrap; z-index: 1000; pointer-events: none;
    box-shadow: 0 2px 8px rgba(0,0,0,0.5); line-height: 1.5; }}
  .c-dialogue {{ background: #4a9eff; }}
  .c-ambience {{ background: #4caf50; }}
  .c-music {{ background: #ffc107; }}
  .c-sfx {{ background: #ef5350; }}
  .c-vintage-filter {{ background: #ab7edb; }}
  .ramp-badge {{ position: absolute; top: 1px; font-size: 9px; font-weight: bold; color: rgba(0,0,0,0.65);
    line-height: 1; pointer-events: none; z-index: 5; }}
  .ramp-badge.ri {{ left: 2px; }}
  .ramp-badge.ro {{ right: 2px; }}
  .ramp-badge.pd {{ left: 50%; transform: translateX(-50%); }}
  .ramp-badge.vb {{ right: 2px; bottom: 1px; }}
  .controls {{ margin-bottom: 12px; display: flex; gap: 8px; align-items: center; }}
  .controls button {{ background: #333; color: #ccc; border: 1px solid #555; padding: 4px 12px; border-radius: 3px; cursor: pointer; font-size: 12px; }}
  .controls button:hover {{ background: #444; }}
  .zoom-info {{ font-size: 12px; color: #888; }}
  #xil-player {{ position: sticky; top: 0; z-index: 200; background: #111;
    padding: 6px 12px; border-bottom: 1px solid #444; margin-bottom: 8px; display: none; }}
  #xil-player.active {{ display: block; }}
  #player-label {{ font-size: 11px; color: #aaa; margin-bottom: 3px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }}
{modal_css}{transport_css}{loader_css}</style>
</head>
<body>
<div id="xil-player">
  <div id="player-label"></div>
  <audio id="audio-el" controls style="width:100%;height:36px;"></audio>
</div>
<h1>Timeline: {tag}</h1>
<p class="subtitle">Duration: {duration_fmt} &middot; {span_count} assets across 5 layers &middot; Generated {generated_at}</p>
<div class="controls">
  <button onclick="zoomIn()">Zoom +</button>
  <button onclick="zoomOut()">Zoom &minus;</button>
  <button onclick="zoomReset()">Reset</button>
  <span class="zoom-info" id="zoom-info">100%</span>
</div>
{transport_html}
{loader_html}
<div id="floattip"></div>
<div class="timeline-container" id="tc">
  <div class="timeline-inner" id="ti">
    <div class="ruler" id="ruler"></div>
    <div id="layers"></div>
    <div id="playhead"></div>
  </div>
</div>
{modal_html}
<script>
const DATA = {data_json};
const CLIPS = {clips_json};
const LAYER_AUDIO = {layer_audio_json};
{slug_js}
const TOTAL = DATA.total_duration_s;
const COLORS = {{dialogue:'c-dialogue', sfx:'c-sfx', music:'c-music', ambience:'c-ambience', vintage_filter:'c-vintage-filter'}};
const LABELS = {{dialogue:'Dialogue', sfx:'SFX', music:'Music', ambience:'Ambience', vintage_filter:'Vtg Filter'}};
function escAttr(s) {{ return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }}
const EDITABLE_LAYERS = {{'sfx':true,'music':true,'ambience':true}};
let zoom = 1;
const BASE_WIDTH = Math.max(document.getElementById('tc').clientWidth - 100, 400);
const tips = {{}};  // span index → tooltip HTML
const tiToSeq = {{}};  // span index → seq number
const tiToSpan = {{}};  // span index → span object (resolved audio params)

function fmtTime(s) {{
  const m = Math.floor(s/60), sec = Math.floor(s%60);
  return m + ':' + String(sec).padStart(2,'0');
}}

function render() {{
  const W = BASE_WIDTH * zoom;
  document.getElementById('ti').style.width = W + 100 + 'px';
  // Ruler
  let interval = 30;
  if (TOTAL > 180) interval = 60;
  if (TOTAL > 600) interval = 120;
  let rhtml = '';
  for (let t = 0; t <= TOTAL; t += interval) {{
    rhtml += '<div class="ruler-tick" style="left:calc(90px + ' + (t/TOTAL*W) + 'px)"><span>' + fmtTime(t) + '</span></div>';
  }}
  document.getElementById('ruler').innerHTML = rhtml;
  // Layers
  let lhtml = '';
  let ti = 0;
  for (const key of ['dialogue','sfx','music','ambience','vintage_filter']) {{
    const spans = DATA.layers[key] || [];
    lhtml += '<div class="layer"><div class="layer-label">' + LABELS[key] + '</div><div class="layer-track" style="width:'+W+'px">';
    for (const sp of spans) {{
      const left = sp.start_s / TOTAL * 100;
      const w = Math.max((sp.end_s - sp.start_s) / TOTAL * 100, 0.15);
      const dur = (sp.end_s - sp.start_s).toFixed(1);
      let rampBadges = '';
      let rampTip = '';
      if (sp.ramp_in_s) {{ rampBadges += '<span class="ramp-badge ri">\u2191</span>'; rampTip += '\u2191 ramp in: '+sp.ramp_in_s+'s  '; }}
      if (sp.ramp_out_s) {{ rampBadges += '<span class="ramp-badge ro">\u2193</span>'; rampTip += '\u2193 ramp out: '+sp.ramp_out_s+'s  '; }}
      if (sp.play_duration != null) {{ rampBadges += '<span class="ramp-badge pd">%</span>'; rampTip += '% play: '+sp.play_duration.toFixed(2)+'  '; }}
      if (sp.volume_pct != null && sp.volume_pct !== 100) {{ rampBadges += '<span class="ramp-badge vb">\U0001f50a'+sp.volume_pct+'%</span>'; rampTip += '\U0001f50a vol: '+sp.volume_pct+'%  '; }}
      else if (sp.volume_pct != null) {{ rampTip += '\U0001f50a vol: '+sp.volume_pct+'%  '; }}
      const tipExtra = rampTip ? '<br><span style="opacity:0.8">'+rampTip.trim()+'</span>' : '';
      const snippetLine = sp.snippet ? '<br><em style="opacity:0.75">'+sp.snippet.replace(/</g,'&lt;')+'\u2026</em>' : '';
      const modelLine = sp.tts_model ? '<br><span style="opacity:0.55;font-size:0.85em">\u26a1\ufe0f '+sp.tts_model+'</span>' : '';
      const seqPrefix = sp.seq != null ? '<span style="opacity:0.6">#'+String(sp.seq).padStart(3,'0')+'</span> ' : '';
      tips[ti] = seqPrefix+'<strong>'+sp.label.replace(/</g,'&lt;')+'</strong>'+snippetLine+'<br>'+fmtTime(sp.start_s)+' \u2192 '+fmtTime(sp.end_s)+' ('+dur+'s)'+tipExtra+modelLine;
      tiToSeq[ti] = sp.seq;
      tiToSpan[ti] = sp;
      const seqAttr = (sp.seq != null) ? ' data-seq="'+sp.seq+'"' : '';
      const editable = EDITABLE_LAYERS[key];
      const eAttr = editable ? ' data-effect-key="'+escAttr(sp.label)+'" data-layer="'+key+'"' : '';
      if (editable) {{ tips[ti] += '<br><span style="opacity:0.4;font-size:0.85em">✎ double-click to edit sound profile</span>'; }}
      lhtml += '<div class="span '+COLORS[key]+'" style="left:'+left+'%;width:'+w+'%" data-ti="'+ti+'"'+seqAttr+eAttr+'>'+rampBadges+'</div>';
      ti++;
    }}
    lhtml += '</div></div>';
  }}
  document.getElementById('layers').innerHTML = lhtml;
  document.getElementById('zoom-info').textContent = Math.round(zoom*100) + '%';
}}

// Floating tooltip — uses position:fixed to escape overflow clipping
(function() {{
  const tip = document.getElementById('floattip');
  document.addEventListener('mouseover', function(e) {{
    const sp = e.target.closest('.span[data-ti]');
    if (sp) {{
      tip.innerHTML = tips[sp.dataset.ti] || '';
      tip.style.display = 'block';
    }}
  }});
  document.addEventListener('mouseout', function(e) {{
    if (!e.relatedTarget || !e.relatedTarget.closest('.span[data-ti]')) {{
      tip.style.display = 'none';
    }}
  }});
  document.addEventListener('mousemove', function(e) {{
    if (tip.style.display === 'block') {{
      let x = e.clientX + 14, y = e.clientY - 10;
      if (x + tip.offsetWidth > window.innerWidth - 8) x = e.clientX - tip.offsetWidth - 14;
      if (y + tip.offsetHeight > window.innerHeight - 8) y = e.clientY - tip.offsetHeight - 10;
      tip.style.left = x + 'px';
      tip.style.top = y + 'px';
    }}
  }});
}})();

function zoomIn() {{ zoom = Math.min(zoom * 1.5, 20); render(); }}
function zoomOut() {{ zoom = Math.max(zoom / 1.5, 0.5); render(); }}
function zoomReset() {{ zoom = 1; render(); }}

document.getElementById('tc').addEventListener('wheel', function(e) {{
  if (e.ctrlKey || e.metaKey) {{
    e.preventDefault();
    if (e.deltaY < 0) zoomIn(); else zoomOut();
  }}
}}, {{passive: false}});

render();

{loader_js}

{modal_js}

// === Preview playback with sound profile ===
// Web Audio gain graph: audio.volume caps at 1.0, so volumes >100% and
// scheduled ramps require a GainNode.  A media element can only be
// connected to a context once — create lazily on first play and reuse.
let audioCtx = null, gainNode = null, stopTimer = null, clickTimer = null;
function ensureAudioGraph() {{
  if (audioCtx) return;
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const src = audioCtx.createMediaElementSource(document.getElementById('audio-el'));
  gainNode = audioCtx.createGain();
  src.connect(gainNode);
  gainNode.connect(audioCtx.destination);
}}

async function playSpan(el) {{
  const seq = el.dataset.seq;
  const fp = CLIPS[seq];
  if (!fp) return;
  const token = ++_playToken;
  document.querySelectorAll('.span.playing').forEach(function(s) {{ s.classList.remove('playing'); }});
  document.querySelectorAll('.span.loading').forEach(function(s) {{ s.classList.remove('loading'); }});
  const audioEl = document.getElementById('audio-el');
  const ti = el.dataset.ti;
  const rawLabel = ti != null ? (tips[ti] || '').replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim() : seq;
  const label = document.getElementById('player-label');
  // .active first — the player strip is display:none until then, and the
  // loading readout must be visible while the clip crosses the NAS.
  document.getElementById('xil-player').classList.add('active');
  el.classList.add('loading');
  label.textContent = 'Loading\\u2026';
  let src;
  try {{
    src = await fetchAudioBlob(fp, function(received, total) {{
      label.textContent = total
        ? 'Loading\\u2026 ' + Math.round(received / total * 100) + '%'
        : 'Loading\\u2026 ' + fmtMB(received);
    }});
  }} catch (err) {{
    el.classList.remove('loading');
    label.textContent = '\\u26a0 ' + (err && err.message ? err.message : err);
    return;
  }}
  el.classList.remove('loading');
  if (token !== _playToken) return;  // user clicked elsewhere mid-load
  if (mixPlaying) pauseMix();  // mutual exclusion with the full-mix transport
  el.classList.add('playing');
  label.textContent = rawLabel;
  audioEl.src = src;

  ensureAudioGraph();
  if (audioCtx.state === 'suspended') audioCtx.resume();
  clearTimeout(stopTimer);
  const sp = ti != null ? tiToSpan[ti] : null;
  const g = gainNode.gain;
  const t0 = audioCtx.currentTime;
  g.cancelScheduledValues(t0);
  const target = (sp && sp.volume_pct != null) ? sp.volume_pct / 100 : 1;
  const ri = (sp && sp.ramp_in_s) || 0;
  const ro = (sp && sp.ramp_out_s) || 0;
  // end_s - start_s is already the play_duration-trimmed clip length
  const playLen = sp ? Math.max(sp.end_s - sp.start_s, 0.1) : 0;
  if (ri > 0) {{
    g.setValueAtTime(0, t0);
    g.linearRampToValueAtTime(target, t0 + ri);
  }} else {{
    g.setValueAtTime(target, t0);
  }}
  if (sp && ro > 0 && playLen > ro) {{
    g.setValueAtTime(target, t0 + playLen - ro);
    g.linearRampToValueAtTime(0, t0 + playLen);
  }}
  audioEl.play();
  if (sp) {{
    stopTimer = setTimeout(function() {{ audioEl.pause(); }}, playLen * 1000);
  }}
}}

document.getElementById('layers').addEventListener('click', function(e) {{
  if (e.detail > 1) return;  // ignore clicks that are part of a double-click
  const el = e.target.closest('.span[data-seq]');
  if (!el) return;
  // Defer 250ms so a double-click (edit) can cancel the preview.
  clearTimeout(clickTimer);
  clickTimer = setTimeout(function() {{ playSpan(el); }}, 250);
}});

{transport_js}
</script>
</body>
</html>
"""


def _fmt_mmss_tenths(seconds: float) -> str:
    """Format seconds as ``M:SS.t`` (tenths), right-aligned to 7 chars."""
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m}:{s:04.1f}".rjust(7)


def render_text_timeline_map(
    data: TimelineData,
    output_path: str,
    *,
    slug: str = "",
) -> str:
    """Write a human-readable cue sheet of the episode's foreground timing.

    Dialogue and SFX spans interleaved chronologically; music and ambience
    are omitted — they are background layers that do not move the
    foreground timeline.

    Args:
        data: Timeline data from :func:`build_timeline_data`.
        output_path: Path to write the text map.
        slug: Show slug for the header (omitted when empty).

    Returns:
        The path written (same as *output_path*).
    """
    spans = list(data.layers.get("dialogue", [])) + list(data.layers.get("sfx", []))
    spans.sort(key=lambda sp: (sp.start_s, sp.seq if sp.seq is not None else 0))
    dialogue_ids = {id(sp) for sp in data.layers.get("dialogue", [])}

    show_part = f" — {slug}" if slug else ""
    lines = [
        f"# Timeline map: {data.tag}{show_part} ({_format_time(data.total_duration_s)})",
        "# dialogue + SFX foreground timing; music/ambience omitted",
        "#",
        "#  START      END       LAYER  SEQ   WHO/WHAT",
    ]
    for sp in spans:
        layer = "DLG" if id(sp) in dialogue_ids else "SFX"
        seq = f"#{sp.seq:03d}" if sp.seq is not None else "    "
        who = sp.label
        if layer == "DLG" and sp.snippet:
            who = f"{sp.label}  “{sp.snippet}…”"
        lines.append(
            f" {_fmt_mmss_tenths(sp.start_s)} – {_fmt_mmss_tenths(sp.end_s)}   "
            f"{layer}   {seq}  {who}"
        )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return output_path


def render_html_timeline(
    data: TimelineData,
    output_path: str,
    stems_dir: str | None = None,
    *,
    slug: str = "",
    tag: str = "",
    layers_dir: str | None = None,
) -> str:
    """Write a self-contained HTML timeline file.

    Args:
        data: Timeline data from :func:`build_timeline_data`.
        output_path: Path to write the HTML file.
        stems_dir: Directory of episode stem MP3 files. When provided, clicking
            a timeline block plays the corresponding stem via an embedded audio
            player (served by Gradio's ``/gradio_api/file=`` endpoint).
        slug: Show slug — embedded as ``XIL_SLUG`` JS constant for the
            right-click sound profile editor.
        tag: Episode tag — embedded as ``XIL_TAG`` JS constant.
        layers_dir: Directory of the DAW layer WAVs
            (``{tag}_layer_{key}.wav``). Existing layer files are embedded as
            ``LAYER_AUDIO`` (with an mtime cache-buster) to power the
            full-mix transport; when absent the transport UI is hidden.

    Returns:
        The path written (same as *output_path*).
    """
    # Audio URLs are stored RELATIVE to the timeline file's own directory so
    # the artifact is root-agnostic: the same file works whether the GUI
    # serves it from a local root or a NAS mount (the iframe loads it via
    # /gradio_api/file=<abs timeline path>, and the browser resolves relative
    # refs against that document URL). A ?v={mtime} cache-buster still keys the
    # browser blob cache so a regenerated asset self-invalidates.
    out_dir = os.path.dirname(os.path.abspath(output_path))

    def _rel_audio(full_abs: str) -> str:
        try:
            rel = os.path.relpath(full_abs, out_dir)
        except ValueError:
            rel = full_abs  # different drive (Windows) — fall back to absolute
        rel = rel.replace(os.sep, "/")
        return f"{rel}?v={int(os.path.getmtime(full_abs))}"

    # Build seq → relative path mapping for click-to-play
    import re as _re
    _seq_re = _re.compile(r"^(n?)(\d+)_")
    clips: dict[str, str] = {}
    if stems_dir and os.path.isdir(stems_dir):
        for fname in sorted(os.listdir(stems_dir)):
            if not fname.endswith(".mp3"):
                continue
            m = _seq_re.match(fname)
            if m:
                seq = -int(m.group(2)) if m.group(1) == "n" else int(m.group(2))
                full = os.path.abspath(os.path.join(stems_dir, fname))
                clips[str(seq)] = _rel_audio(full)
    clips_json = json.dumps(clips)

    # Build JSON-serializable structure
    json_data = {
        "tag": data.tag,
        "total_duration_s": data.total_duration_s,
        "layers": {
            key: [
                {
                    "start_s": sp.start_s,
                    "end_s": sp.end_s,
                    "label": sp.label,
                    "ramp_in_s": sp.ramp_in_s,
                    "ramp_out_s": sp.ramp_out_s,
                    "play_duration": sp.play_duration,
                    "snippet": sp.snippet,
                    "volume_pct": sp.volume_pct,
                    "seq": sp.seq,
                    "tts_model": sp.tts_model,
                }
                for sp in spans
            ]
            for key, spans in data.layers.items()
        },
    }

    span_count = sum(len(spans) for spans in data.layers.values())
    slug_js = f"const XIL_SLUG = {json.dumps(slug or data.tag)};\nconst XIL_TAG  = {json.dumps(tag or data.tag)};"

    # Full-mix transport: embed existing DAW layer WAVs with an mtime
    # cache-buster so a regenerated mix is never served from browser cache.
    layer_audio: dict[str, str] = {}
    if layers_dir and os.path.isdir(layers_dir):
        for key in ("dialogue", "sfx", "music", "ambience", "vintage_filter"):
            wav = os.path.join(layers_dir, f"{data.tag}_layer_{key}.wav")
            if os.path.exists(wav):
                layer_audio[key] = _rel_audio(os.path.abspath(wav))

    content = _HTML_TEMPLATE.format(
        tag=html.escape(data.tag),
        duration_fmt=_format_time(data.total_duration_s),
        span_count=span_count,
        data_json=json.dumps(json_data),
        clips_json=clips_json,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        slug_js=slug_js,
        layer_audio_json=json.dumps(layer_audio),
        modal_css=_MODAL_CSS,
        modal_html=_MODAL_HTML,
        modal_js=_MODAL_JS,
        transport_css=_TRANSPORT_CSS,
        transport_html=_TRANSPORT_HTML,
        transport_js=_TRANSPORT_JS,
        loader_css=_LOADER_CSS,
        loader_html=_LOADER_HTML,
        loader_js=_LOADER_JS,
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path
