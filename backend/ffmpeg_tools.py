"""Thin wrappers around ffmpeg/ffprobe for metadata probing and quality analysis."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")


class ToolError(RuntimeError):
    pass


def find_binaries() -> dict:
    return {
        "ffmpeg": shutil.which(FFMPEG_BIN),
        "ffprobe": shutil.which(FFPROBE_BIN),
    }


async def probe_file(path: str) -> dict:
    """Run ffprobe and return the full format+stream metadata as a dict."""
    if not Path(path).is_file():
        raise ToolError(f"File not found: {path}")

    args = [
        FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-show_error",
        path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 and not stdout:
        raise ToolError(stderr.decode(errors="replace") or "ffprobe failed")

    try:
        data = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError as exc:
        raise ToolError(f"Could not parse ffprobe output: {exc}") from exc

    if "error" in data:
        raise ToolError(data["error"].get("string", "ffprobe error"))

    return data


async def _probe_basic(path: str) -> dict:
    """Lightweight probe used internally for duration/frame-count/resolution."""
    args = [
        FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,r_frame_rate,avg_frame_rate,nb_frames",
        path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise ToolError(stderr.decode(errors="replace") or "ffprobe failed")
    return json.loads(stdout.decode(errors="replace"))


def _parse_frame_rate(rate: str) -> float:
    try:
        if "/" in rate:
            num, den = rate.split("/")
            den = float(den)
            return float(num) / den if den else 0.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


async def estimate_frame_count(path: str) -> Optional[int]:
    """Best-effort total frame count for the main (distorted) video, used for progress %."""
    data = await _probe_basic(path)
    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0) or 0)
    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video_stream is None:
        return None

    nb_frames = video_stream.get("nb_frames")
    if nb_frames and str(nb_frames).isdigit():
        return int(nb_frames)

    fps = _parse_frame_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0")
    if fps > 0 and duration > 0:
        return int(duration * fps)
    return None


async def get_resolution(path: str) -> Optional[tuple[int, int]]:
    data = await _probe_basic(path)
    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video_stream:
        return None
    w, h = video_stream.get("width"), video_stream.get("height")
    if w and h:
        return int(w), int(h)
    return None


def _escape_lavfi_value(value: str) -> str:
    """Escape a value embedded in an -lavfi filter option string.

    The filtergraph description goes through two nested parsers: the outer
    graph parser and, per-filter, av_set_options_string (which splits on
    ':'). The outer parser consumes one level of backslash escaping before
    the inner parser ever sees the string, so a single '\\:' is not enough
    to protect a literal colon (e.g. a Windows drive letter) — it takes a
    double backslash for the escape to survive both passes.
    """
    return value.replace("\\", "\\\\\\\\").replace(":", "\\\\:").replace("'", "\\\\'")


VMAF_MODELS = {
    "vmaf_v0.6.1": "version=vmaf_v0.6.1",
    "vmaf_v0.6.1neg": "version=vmaf_v0.6.1neg",
    "vmaf_4k_v0.6.1": "version=vmaf_4k_v0.6.1",
}


@dataclass
class AnalysisProgress:
    percent: float = 0.0
    current_frame: int = 0
    total_frames: Optional[int] = None
    fps: float = 0.0
    stage: str = "starting"


@dataclass
class AnalysisResult:
    vmaf: Optional[dict] = None
    psnr: Optional[dict] = None
    ssim: Optional[dict] = None
    raw_pooled_metrics: dict = field(default_factory=dict)
    log_path: Optional[str] = None
    frames: list = field(default_factory=list)


FRAME_SERIES_KEYS = ("vmaf", "psnr_y", "float_ssim")
MAX_REPORT_FRAMES = 600


def _extract_frame_series(vmaf_json: dict) -> list:
    """Per-frame VMAF/PSNR/SSIM values for the report's quality-over-time chart.

    Downsampled to MAX_REPORT_FRAMES points so long videos don't bloat the
    in-memory job or the report page.
    """
    frames = vmaf_json.get("frames", [])
    if not frames:
        return []

    step = max(1, len(frames) // MAX_REPORT_FRAMES)
    series = []
    for f in frames[::step]:
        metrics = f.get("metrics", {})
        point = {"frame": f.get("frameNum", f.get("frame_num"))}
        for key in FRAME_SERIES_KEYS:
            if key in metrics:
                point[key] = metrics[key]
        series.append(point)
    return series


def _extract_pooled(vmaf_json: dict) -> dict:
    pooled = vmaf_json.get("pooled_metrics", {})
    out = {}
    for key, stats in pooled.items():
        out[key] = {
            "min": stats.get("min"),
            "max": stats.get("max"),
            "mean": stats.get("mean"),
            "harmonic_mean": stats.get("harmonic_mean"),
        }
    return out


def _summarize(pooled: dict) -> AnalysisResult:
    result = AnalysisResult(raw_pooled_metrics=pooled)

    if "vmaf" in pooled:
        result.vmaf = pooled["vmaf"]

    psnr_keys = [k for k in pooled if k.startswith("psnr")]
    if psnr_keys:
        result.psnr = {k: pooled[k] for k in psnr_keys}

    ssim_keys = [k for k in pooled if "ssim" in k]
    if ssim_keys:
        result.ssim = {k: pooled[k] for k in ssim_keys}

    return result


async def run_quality_analysis(
    reference: str,
    distorted: str,
    *,
    model: str = "vmaf_v0.6.1",
    n_threads: int = 0,
    n_subsample: int = 1,
    on_progress: Optional[Callable[[AnalysisProgress], None]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> AnalysisResult:
    """Run VMAF+PSNR+SSIM in a single ffmpeg pass via the libvmaf filter."""
    if not Path(reference).is_file():
        raise ToolError(f"Reference file not found: {reference}")
    if not Path(distorted).is_file():
        raise ToolError(f"Distorted file not found: {distorted}")

    total_frames = await estimate_frame_count(distorted)

    ref_res, dist_res = await asyncio.gather(get_resolution(reference), get_resolution(distorted))

    tmp_dir = Path(tempfile.gettempdir()) / "vmaf-compare"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_dir / f"vmaf_{os.getpid()}_{id(reference)}.json"

    model_opt = VMAF_MODELS.get(model, VMAF_MODELS["vmaf_v0.6.1"])

    filter_parts = []
    ref_label = "[1:v]"
    if ref_res and dist_res and ref_res != dist_res:
        filter_parts.append(f"[1:v]scale={dist_res[0]}:{dist_res[1]}[ref_scaled]")
        ref_label = "[ref_scaled]"

    escaped_log_path = _escape_lavfi_value(log_path.as_posix())
    libvmaf_opts = (
        f"model={model_opt}:psnr=true:ssim=true:log_fmt=json:"
        f"log_path={escaped_log_path}:n_threads={n_threads}:n_subsample={n_subsample}"
    )
    filter_parts.append(f"[0:v]{ref_label}libvmaf={libvmaf_opts}")
    filter_complex = ";".join(filter_parts)

    args = [
        FFMPEG_BIN,
        "-i", distorted,
        "-i", reference,
        "-lavfi", filter_complex,
        "-f", "null",
        "-progress", "pipe:1",
        "-nostats",
        "-y",
        "-",
    ]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_chunks: list[bytes] = []

    async def read_stderr():
        assert proc.stderr is not None
        async for line in proc.stderr:
            stderr_chunks.append(line)

    async def read_progress():
        assert proc.stdout is not None
        current = AnalysisProgress(total_frames=total_frames, stage="analyzing")
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key == "frame":
                try:
                    current.current_frame = int(value)
                except ValueError:
                    pass
                if total_frames:
                    current.percent = min(99.0, 100.0 * current.current_frame / total_frames)
            elif key == "fps":
                try:
                    current.fps = float(value)
                except ValueError:
                    pass
            elif key == "progress" and value == "end":
                current.percent = 100.0
                current.stage = "finalizing"
            if on_progress:
                on_progress(current)

    stderr_task = asyncio.create_task(read_stderr())
    progress_task = asyncio.create_task(read_progress())

    if cancel_event is not None:
        cancel_wait = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait(
            {asyncio.create_task(proc.wait()), cancel_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_event.is_set():
            proc.kill()
            await proc.wait()
            for t in pending:
                t.cancel()
            raise asyncio.CancelledError("Analysis cancelled")
        for t in pending:
            t.cancel()
    else:
        await proc.wait()

    await asyncio.gather(stderr_task, progress_task, return_exceptions=True)

    if proc.returncode != 0:
        err_text = b"".join(stderr_chunks).decode(errors="replace")
        raise ToolError(err_text[-4000:] or "ffmpeg exited with an error")

    if not log_path.is_file():
        raise ToolError("ffmpeg finished but no VMAF log was produced")

    try:
        vmaf_json = json.loads(log_path.read_text(encoding="utf-8"))
    finally:
        try:
            log_path.unlink()
        except OSError:
            pass

    pooled = _extract_pooled(vmaf_json)
    result = _summarize(pooled)
    result.frames = _extract_frame_series(vmaf_json)
    return result
