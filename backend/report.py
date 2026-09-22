"""Generates a self-contained HTML report (and a JSON export) for a finished job."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

# Single-hue line color per metric — each chart has exactly one series, so a
# legend isn't needed (the chart title names the series); color only has to
# read clearly against the dark report background, not be CVD-distinguishable
# from a sibling series within the same chart.
METRIC_CHART_COLORS = {
    "vmaf": "#7fa8ff",
    "psnr_y": "#7fd0ff",
    "float_ssim": "#5fd9a6",
}
METRIC_LABELS = {
    "vmaf": "VMAF",
    "psnr_y": "PSNR (Y, dB)",
    "float_ssim": "SSIM",
}

SCORE_META = {
    "vmaf": {"label": "VMAF", "digits": 2},
    "psnr_y": {"label": "PSNR (Y)", "digits": 2, "unit": "dB"},
    "psnr_cb": {"label": "PSNR (Cb)", "digits": 2, "unit": "dB"},
    "psnr_cr": {"label": "PSNR (Cr)", "digits": 2, "unit": "dB"},
    "float_ssim": {"label": "SSIM", "digits": 4},
    "float_ms_ssim": {"label": "MS-SSIM", "digits": 4},
}
SCORE_ORDER = ["vmaf", "psnr_y", "psnr_cb", "psnr_cr", "float_ssim", "float_ms_ssim"]

FORMAT_FIELDS = [
    ("format_long_name", "Format"),
    ("duration", "Duration (s)"),
    ("size", "Size (bytes)"),
    ("bit_rate", "Bit rate (bps)"),
    ("nb_streams", "Streams"),
]
VIDEO_FIELDS = [
    ("codec_long_name", "Codec"),
    ("profile", "Profile"),
    ("width", "Width"),
    ("height", "Height"),
    ("pix_fmt", "Pixel format"),
    ("r_frame_rate", "Frame rate"),
    ("avg_frame_rate", "Avg frame rate"),
    ("bit_rate", "Bit rate (bps)"),
    ("nb_frames", "Frame count"),
    ("color_space", "Color space"),
    ("color_range", "Color range"),
]
AUDIO_FIELDS = [
    ("codec_long_name", "Codec"),
    ("sample_rate", "Sample rate"),
    ("channels", "Channels"),
    ("channel_layout", "Channel layout"),
    ("bit_rate", "Bit rate (bps)"),
]


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _fmt_num(value, digits=3):
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _rows_table(obj: dict, fields: list[tuple[str, str]]) -> str:
    rows = ""
    for key, label in fields:
        val = obj.get(key)
        if val in (None, ""):
            continue
        rows += f"<tr><td>{_esc(label)}</td><td>{_esc(val)}</td></tr>"
    return f"<table>{rows}</table>" if rows else "<p class='muted'>No data</p>"


def _metadata_section(meta: dict | None, title: str) -> str:
    if not meta:
        return f"<div class='col'><h3>{_esc(title)}</h3><p class='muted'>Metadata unavailable</p></div>"

    fmt = meta.get("format", {})
    blocks = [f"<div class='block'><div class='block-title'>Format</div>{_rows_table(fmt, FORMAT_FIELDS)}</div>"]

    for i, stream in enumerate(meta.get("streams", [])):
        codec_type = stream.get("codec_type", "unknown")
        fields = VIDEO_FIELDS if codec_type == "video" else AUDIO_FIELDS if codec_type == "audio" else [
            ("codec_long_name", "Codec"), ("codec_type", "Type")
        ]
        blocks.append(
            f"<div class='block'><div class='block-title'>Stream #{stream.get('index', i)} — {_esc(codec_type)}"
            f"</div>{_rows_table(stream, fields)}</div>"
        )

    return f"<div class='col'><h3>{_esc(title)}</h3>{''.join(blocks)}</div>"


def _line_chart_svg(series: list, key: str, width: int = 760, height: int = 160) -> str:
    """A single-series line chart: thin 2px line, rounded caps, recessive gridlines,
    per-point hover values via native <title> tooltips (no JS needed in a static report)."""
    points = [(p.get("frame", i), p[key]) for i, p in enumerate(series) if key in p]
    if len(points) < 2:
        return "<p class='muted'>No frame-level data for this metric.</p>"

    pad_l, pad_r, pad_t, pad_b = 44, 12, 12, 24
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if y_max == y_min:
        y_min, y_max = y_min - 1, y_max + 1
    x_span = (x_max - x_min) or 1

    def sx(x):
        return pad_l + (x - x_min) / x_span * plot_w

    def sy(y):
        return pad_t + (1 - (y - y_min) / (y_max - y_min)) * plot_h

    path_d = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(points))
    color = METRIC_CHART_COLORS.get(key, "#7fa8ff")

    gridlines = ""
    for frac in (0.0, 0.5, 1.0):
        gy = pad_t + frac * plot_h
        label = y_max - frac * (y_max - y_min)
        gridlines += (
            f"<line x1='{pad_l}' y1='{gy:.1f}' x2='{width - pad_r}' y2='{gy:.1f}' "
            f"stroke='#2a313b' stroke-width='1'/>"
            f"<text x='{pad_l - 6}' y='{gy + 4:.1f}' text-anchor='end' class='axis-label'>{_fmt_num(label, 1)}</text>"
        )

    # sparse hover targets (~40 points) so the SVG stays small for long videos
    hover_step = max(1, len(points) // 40)
    hover_dots = ""
    for i in range(0, len(points), hover_step):
        x, y = points[i]
        hover_dots += (
            f"<circle cx='{sx(x):.1f}' cy='{sy(y):.1f}' r='6' fill='transparent' class='hover-dot'>"
            f"<title>frame {x}: {_fmt_num(y, 4)}</title></circle>"
        )

    return f"""
<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img"
     aria-label="{_esc(METRIC_LABELS.get(key, key))} over frames">
  {gridlines}
  <path d="{path_d}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  {hover_dots}
  <text x="{pad_l}" y="{height - 6}" class="axis-label">frame {x_min}</text>
  <text x="{width - pad_r}" y="{height - 6}" text-anchor="end" class="axis-label">frame {x_max}</text>
</svg>
""".strip()


def _score_cards(pooled: dict) -> str:
    cards = ""
    for key in SCORE_ORDER:
        if key not in pooled:
            continue
        stats = pooled[key]
        meta = SCORE_META.get(key, {"label": key, "digits": 3})
        unit = meta.get("unit", "")
        cards += f"""
<div class="card">
  <div class="card-label">{_esc(meta['label'])}</div>
  <div class="card-value">{_fmt_num(stats.get('mean'), meta['digits'])}{(' ' + unit) if unit else ''}</div>
  <div class="card-sub">min {_fmt_num(stats.get('min'), meta['digits'])} ·
    max {_fmt_num(stats.get('max'), meta['digits'])} ·
    hmean {_fmt_num(stats.get('harmonic_mean'), meta['digits'])}</div>
</div>"""
    return cards or "<p class='muted'>No pooled metrics.</p>"


def _charts_section(frames: list) -> str:
    if not frames:
        return "<p class='muted'>No per-frame data was recorded for this run.</p>"
    charts = ""
    for key in ("vmaf", "psnr_y", "float_ssim"):
        if not any(key in p for p in frames):
            continue
        charts += f"""
<div class="chart-block">
  <div class="block-title">{_esc(METRIC_LABELS[key])} over frames</div>
  {_line_chart_svg(frames, key)}
</div>"""
    return charts or "<p class='muted'>No matching per-frame series.</p>"


def build_report_context(job) -> dict:
    """Plain-dict snapshot of a finished job, used for both the HTML and JSON report."""
    result = job.result or {}
    return {
        "job_id": job.id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference_path": job.reference,
        "distorted_path": job.distorted,
        "model": job.model,
        "n_threads": job.n_threads,
        "n_subsample": job.n_subsample,
        "pooled_metrics": result.get("raw_pooled_metrics", {}),
        "frames": result.get("frames", []),
        "reference_metadata": job.reference_meta,
        "distorted_metadata": job.distorted_meta,
    }


def generate_report_html(job) -> str:
    ctx = build_report_context(job)
    pooled = ctx["pooled_metrics"]
    generated = datetime.fromisoformat(ctx["generated_at"]).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Video Quality Report — {_esc(job.id)}</title>
<style>
  :root {{
    --bg: #0f1216; --panel: #171b21; --panel-2: #1e242c; --border: #2a313b;
    --text: #e6e9ef; --text-dim: #9aa4b2; --accent: #7fa8ff;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
          font-family: "Segoe UI", system-ui, -apple-system, sans-serif; font-size: 14px; }}
  .wrap {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 60px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 28px 0 12px; }}
  h3 {{ font-size: 12.5px; margin: 0 0 8px; color: var(--text-dim); text-transform: uppercase; letter-spacing: .04em; }}
  .meta-line {{ color: var(--text-dim); font-size: 12.5px; margin-bottom: 24px; }}
  .muted {{ color: var(--text-dim); }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px; margin-bottom: 20px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }}
  .card {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }}
  .card-label {{ color: var(--text-dim); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }}
  .card-value {{ font-size: 24px; font-weight: 700; }}
  .card-sub {{ margin-top: 6px; font-size: 11px; color: var(--text-dim); }}
  .chart-block {{ margin-bottom: 22px; }}
  .block {{ margin-bottom: 16px; }}
  .block-title {{ color: var(--accent); font-weight: 600; margin-bottom: 6px; font-size: 12.5px; }}
  .axis-label {{ fill: var(--text-dim); font-size: 10px; font-family: Consolas, monospace; }}
  .hover-dot {{ cursor: default; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 3px 6px; border-bottom: 1px solid var(--border); word-break: break-word; }}
  td:first-child {{ color: var(--text-dim); width: 40%; font-family: Consolas, monospace; white-space: nowrap; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .col {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }}
  pre {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 12px;
         overflow: auto; font-size: 12px; }}
  @media print {{
    body {{ background: white; color: black; }}
    .panel, .col, .card {{ background: white; border-color: #ccc; }}
    .axis-label {{ fill: #555; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Video Quality Report</h1>
  <div class="meta-line">
    Generated {_esc(generated)} · job {_esc(ctx['job_id'])} · model {_esc(ctx['model'])}
    · subsample {_esc(ctx['n_subsample'])}
  </div>

  <div class="panel">
    <h3>Files compared</h3>
    <table>
      <tr><td>Reference</td><td>{_esc(ctx['reference_path'])}</td></tr>
      <tr><td>Distorted</td><td>{_esc(ctx['distorted_path'])}</td></tr>
    </table>
  </div>

  <div class="panel">
    <h2 style="margin-top:0">Quality Metrics</h2>
    <div class="cards">{_score_cards(pooled)}</div>
  </div>

  <div class="panel">
    <h2 style="margin-top:0">Quality over time</h2>
    {_charts_section(ctx['frames'])}
  </div>

  <div class="panel">
    <h2 style="margin-top:0">Metadata</h2>
    <div class="cols">
      {_metadata_section(ctx['reference_metadata'], 'Reference')}
      {_metadata_section(ctx['distorted_metadata'], 'Distorted')}
    </div>
  </div>

  <div class="panel">
    <h2 style="margin-top:0">Raw pooled metrics</h2>
    <pre>{_esc(json.dumps(pooled, indent=2))}</pre>
  </div>
</div>
</body>
</html>
"""


def generate_report_json(job) -> dict:
    return build_report_context(job)
