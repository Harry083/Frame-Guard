# Frame Guard

A local web app for comparing a distorted video against a reference using **VMAF**, **SSIM**, and **PSNR**
(computed in a single `ffmpeg`/`libvmaf` pass), plus an `ffprobe`-style metadata viewer for both files.

## Requirements

- Python 3.10+
- `ffmpeg` (5.0 or newer) and `ffprobe` on your `PATH`, built with `--enable-libvmaf` (check with `ffmpeg -filters | findstr vmaf`)

## Setup

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

```bash
.venv\Scripts\python.exe run.py
```

Then open http://localhost:8756

## How it works

- **Analysis** (`backend/ffmpeg_tools.py`): runs `ffmpeg -i distorted -i reference -lavfi "...libvmaf=feature=name=psnr|name=float_ssim:..." -f null -`.
  The `libvmaf` filter computes VMAF, SSIM, and PSNR together in one decode pass and writes a JSON log,
  which is parsed for the pooled (min/max/mean/harmonic-mean) scores. If reference and distorted have
  different resolutions, the reference is auto-scaled to match the distorted video before comparison.
- **Progress**: `ffmpeg -progress pipe:1` is parsed frame-by-frame and turned into a percentage using the
  distorted file's frame count (from `ffprobe`).
- **Metadata** (`/api/probe`): shells out to `ffprobe -show_format -show_streams -show_chapters` and the
  frontend renders the JSON as readable tables, similar to `ffprobe`'s own CLI output.
- **File picking**: since browsers don't expose real filesystem paths from `<input type=file>`, there's a
  small server-side directory browser (`backend/file_browser.py`, `/api/browse`) so you can navigate the
  local filesystem from the UI. You can also just paste an absolute path directly into the text field.
- **Reports** (`backend/report.py`): once a run finishes, "View / Save Report" opens a self-contained HTML
  report (`/api/jobs/{id}/report.html`) with the score summary, per-frame VMAF/PSNR/SSIM charts (inline SVG,
  no JS dependency, prints cleanly), both files' metadata, and the raw pooled metrics — save it with
  Ctrl+S or print to PDF. "Download JSON" (`/api/jobs/{id}/report.json`) exports the same data as a raw
  `.json` file for scripting or archiving.

## Project structure

```
Frame-Guard/
├── backend/
│   ├── main.py           FastAPI app & routes
│   ├── ffmpeg_tools.py    ffmpeg/ffprobe wrappers, VMAF/PSNR/SSIM analysis
│   ├── jobs.py            background job manager (progress, cancel)
│   ├── report.py          HTML/JSON report generation
│   └── file_browser.py    server-side directory listing for the file picker
├── frontend/              vanilla HTML/CSS/JS UI
├── testmedia/             tiny sample reference/distorted clips
├── run.py                 entry point (uvicorn)
└── requirements.txt
```

## Notes

- `testmedia/` has a couple of tiny sample clips (a synthetic reference and a lower-quality re-encode) you
  can use to try the app immediately. Regenerate them with:
  ```bash
  ffmpeg -f lavfi -i testsrc=size=640x360:rate=25:duration=3 -c:v libx264 -crf 18 -pix_fmt yuv420p testmedia/reference.mp4
  ffmpeg -i testmedia/reference.mp4 -c:v libx264 -crf 35 -vf "scale=320:180,scale=640:360" -pix_fmt yuv420p testmedia/distorted.mp4
  ```
- VMAF models available: `vmaf_v0.6.1` (default, general purpose), `vmaf_v0.6.1neg` (no-enhancement-gain,
  more robust to enhancement-based cheating), `vmaf_4k_v0.6.1` (for 4K viewing conditions).
- "Subsample" trades accuracy for speed by only scoring every Nth frame — useful for quick checks on long videos.

## License

MIT — see [LICENSE](LICENSE).
