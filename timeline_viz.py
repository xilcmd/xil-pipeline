"""Multitrack timeline visualization for THE 413 audio pipeline.

Renders a visual representation of asset placement across all four audio
layers (dialogue, ambience, music, SFX).  Two output formats are supported:

- **Terminal ASCII timeline** — printed to stdout, auto-scaled to terminal width.
- **HTML interactive timeline** — self-contained file with hover tooltips and zoom.

No pydub dependency — consumes label tuples only.

Usage (from XILP005):
    python XILP005_the413_daw_export.py --episode S02E03 --timeline
    python XILP005_the413_daw_export.py --episode S02E03 --timeline-html
"""

import html
import json
import math
import os
import shutil
from dataclasses import dataclass, field


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
    """

    start_s: float
    end_s: float
    label: str
    ramp_in_s: float | None = None
    ramp_out_s: float | None = None
    play_duration: float | None = None
    snippet: str | None = None


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
) -> TimelineData:
    """Wrap the four label lists into a :class:`TimelineData` object.

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
            spans.append(LayerSpan(s, e, t, ri, ro, pd, sn))
        return spans

    return TimelineData(
        tag=tag,
        total_duration_s=total_s,
        layers={
            "dialogue": to_spans(dlg_labels),
            "ambience": to_spans(amb_labels),
            "music": to_spans(mus_labels),
            "sfx": to_spans(sfx_labels),
        },
    )


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
    tick_line = " " * label_col
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
        ("dialogue", "DIALOGUE", "█"),
        ("ambience", "AMBIENCE", "▓"),
        ("music", "MUSIC", "█"),
        ("sfx", "SFX", "█"),
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
  .span .tooltip {{ display: none; position: absolute; bottom: 110%; left: 50%; transform: translateX(-50%);
    background: #333; color: #fff; padding: 6px 10px; border-radius: 4px; font-size: 12px; white-space: nowrap;
    z-index: 100; pointer-events: none; box-shadow: 0 2px 8px rgba(0,0,0,0.5); }}
  .span:hover .tooltip {{ display: block; }}
  .c-dialogue {{ background: #4a9eff; }}
  .c-ambience {{ background: #4caf50; }}
  .c-music {{ background: #ffc107; }}
  .c-sfx {{ background: #ef5350; }}
  .ramp-badge {{ position: absolute; top: 1px; font-size: 9px; font-weight: bold; color: rgba(0,0,0,0.65);
    line-height: 1; pointer-events: none; z-index: 5; }}
  .ramp-badge.ri {{ left: 2px; }}
  .ramp-badge.ro {{ right: 2px; }}
  .ramp-badge.pd {{ left: 50%; transform: translateX(-50%); }}
  .controls {{ margin-bottom: 12px; display: flex; gap: 8px; align-items: center; }}
  .controls button {{ background: #333; color: #ccc; border: 1px solid #555; padding: 4px 12px; border-radius: 3px; cursor: pointer; font-size: 12px; }}
  .controls button:hover {{ background: #444; }}
  .zoom-info {{ font-size: 12px; color: #888; }}
</style>
</head>
<body>
<h1>Timeline: {tag}</h1>
<p class="subtitle">Duration: {duration_fmt} &middot; {span_count} assets across 4 layers</p>
<div class="controls">
  <button onclick="zoomIn()">Zoom +</button>
  <button onclick="zoomOut()">Zoom &minus;</button>
  <button onclick="zoomReset()">Reset</button>
  <span class="zoom-info" id="zoom-info">100%</span>
</div>
<div class="timeline-container" id="tc">
  <div class="timeline-inner" id="ti">
    <div class="ruler" id="ruler"></div>
    <div id="layers"></div>
  </div>
</div>
<script>
const DATA = {data_json};
const TOTAL = DATA.total_duration_s;
const COLORS = {{dialogue:'c-dialogue', ambience:'c-ambience', music:'c-music', sfx:'c-sfx'}};
const LABELS = {{dialogue:'Dialogue', ambience:'Ambience', music:'Music', sfx:'SFX'}};
let zoom = 1;
const BASE_WIDTH = Math.max(document.getElementById('tc').clientWidth - 100, 400);

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
    const pct = (t / TOTAL * 100);
    rhtml += '<div class="ruler-tick" style="left:calc(90px + ' + (t/TOTAL*W) + 'px)"><span>' + fmtTime(t) + '</span></div>';
  }}
  document.getElementById('ruler').innerHTML = rhtml;
  // Layers
  let lhtml = '';
  for (const key of ['dialogue','ambience','music','sfx']) {{
    const spans = DATA.layers[key] || [];
    lhtml += '<div class="layer"><div class="layer-label">' + LABELS[key] + '</div><div class="layer-track" style="width:'+W+'px">';
    for (const sp of spans) {{
      const left = sp.start_s / TOTAL * 100;
      const w = Math.max((sp.end_s - sp.start_s) / TOTAL * 100, 0.15);
      const dur = (sp.end_s - sp.start_s).toFixed(1);
      // Build ramp indicator badges (↑ for ramp-in, ↓ for ramp-out, % for play_duration)
      let rampBadges = '';
      let rampTip = '';
      if (sp.ramp_in_s) {{ rampBadges += '<span class="ramp-badge ri" title="ramp in: '+sp.ramp_in_s+'s">\u2191</span>'; rampTip += '\u2191 ramp in: '+sp.ramp_in_s+'s  '; }}
      if (sp.ramp_out_s) {{ rampBadges += '<span class="ramp-badge ro" title="ramp out: '+sp.ramp_out_s+'s">\u2193</span>'; rampTip += '\u2193 ramp out: '+sp.ramp_out_s+'s  '; }}
      if (sp.play_duration != null) {{ rampBadges += '<span class="ramp-badge pd" title="play: '+sp.play_duration+'%">%</span>'; rampTip += '% play: '+sp.play_duration+'%'; }}
      const tipExtra = rampTip ? '<br><span style="opacity:0.8">'+rampTip.trim()+'</span>' : '';
      const snippetLine = sp.snippet ? '<br><em style="opacity:0.75">'+sp.snippet.replace(/</g,'&lt;')+'\u2026</em>' : '';
      lhtml += '<div class="span '+COLORS[key]+'" style="left:'+left+'%;width:'+w+'%">'+rampBadges+'<div class="tooltip">'+
        sp.label.replace(/</g,'&lt;')+snippetLine+'<br>'+fmtTime(sp.start_s)+' \u2192 '+fmtTime(sp.end_s)+' ('+dur+'s)'+tipExtra+'</div></div>';
    }}
    lhtml += '</div></div>';
  }}
  document.getElementById('layers').innerHTML = lhtml;
  document.getElementById('zoom-info').textContent = Math.round(zoom*100) + '%';
}}

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
</script>
</body>
</html>
"""


def render_html_timeline(data: TimelineData, output_path: str) -> str:
    """Write a self-contained HTML timeline file.

    Args:
        data: Timeline data from :func:`build_timeline_data`.
        output_path: Path to write the HTML file.

    Returns:
        The path written (same as *output_path*).
    """
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
                }
                for sp in spans
            ]
            for key, spans in data.layers.items()
        },
    }

    span_count = sum(len(spans) for spans in data.layers.values())

    content = _HTML_TEMPLATE.format(
        tag=html.escape(data.tag),
        duration_fmt=_format_time(data.total_duration_s),
        span_count=span_count,
        data_json=json.dumps(json_data),
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path
