"""In-memory background job manager for long-running analysis runs."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from . import ffmpeg_tools as ft


@dataclass
class Job:
    id: str
    reference: str
    distorted: str
    model: str
    n_threads: int = 0
    n_subsample: int = 1
    status: str = "queued"  # queued | running | done | error | cancelled
    percent: float = 0.0
    current_frame: int = 0
    total_frames: Optional[int] = None
    fps: float = 0.0
    stage: str = "queued"
    error: Optional[str] = None
    result: Optional[dict] = None
    reference_meta: Optional[dict] = None
    distorted_meta: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "reference": self.reference,
            "distorted": self.distorted,
            "model": self.model,
            "n_threads": self.n_threads,
            "n_subsample": self.n_subsample,
            "status": self.status,
            "percent": self.percent,
            "current_frame": self.current_frame,
            "total_frames": self.total_frames,
            "fps": self.fps,
            "stage": self.stage,
            "error": self.error,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(
        self, reference: str, distorted: str, model: str, *, n_threads: int = 0, n_subsample: int = 1
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            reference=reference,
            distorted=distorted,
            model=model,
            n_threads=n_threads,
            n_subsample=n_subsample,
        )
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status not in ("queued", "running"):
            return False
        job.cancel_event.set()
        return True

    async def run(self, job_id: str) -> None:
        job = self._jobs[job_id]
        job.status = "running"
        job.stage = "analyzing"
        job.updated_at = time.time()

        def on_progress(p: ft.AnalysisProgress) -> None:
            job.percent = p.percent
            job.current_frame = p.current_frame
            job.total_frames = p.total_frames
            job.fps = p.fps
            job.stage = p.stage
            job.updated_at = time.time()

        try:
            try:
                job.reference_meta, job.distorted_meta = await asyncio.gather(
                    ft.probe_file(job.reference), ft.probe_file(job.distorted)
                )
            except ft.ToolError:
                pass  # metadata is a report nicety, not required for the analysis itself

            result = await ft.run_quality_analysis(
                job.reference,
                job.distorted,
                model=job.model,
                n_threads=job.n_threads,
                n_subsample=job.n_subsample,
                on_progress=on_progress,
                cancel_event=job.cancel_event,
            )
            job.result = {
                "vmaf": result.vmaf,
                "psnr": result.psnr,
                "ssim": result.ssim,
                "raw_pooled_metrics": result.raw_pooled_metrics,
                "frames": result.frames,
            }
            job.status = "done"
            job.stage = "done"
            job.percent = 100.0
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.stage = "cancelled"
        except ft.ToolError as exc:
            job.status = "error"
            job.stage = "error"
            job.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.stage = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.updated_at = time.time()


job_manager = JobManager()
