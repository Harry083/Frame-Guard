from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ffmpeg_tools as ft
from . import file_browser
from . import report as report_mod
from .jobs import job_manager

app = FastAPI(title="Frame Guard")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/api/health")
async def health():
    return {"ok": True, "binaries": ft.find_binaries()}


@app.get("/api/browse")
async def api_browse(path: str = Query(default="")):
    try:
        return file_browser.browse(path or None)
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/probe")
async def api_probe(path: str = Query(...)):
    try:
        return await ft.probe_file(path)
    except ft.ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AnalyzeRequest(BaseModel):
    reference: str
    distorted: str
    model: str = "vmaf_v0.6.1"
    n_threads: int = 0
    n_subsample: int = 1


@app.get("/api/models")
async def api_models():
    return {"models": list(ft.VMAF_MODELS.keys())}


@app.post("/api/analyze")
async def api_analyze(req: AnalyzeRequest):
    if not Path(req.reference).is_file():
        raise HTTPException(status_code=400, detail=f"Reference file not found: {req.reference}")
    if not Path(req.distorted).is_file():
        raise HTTPException(status_code=400, detail=f"Distorted file not found: {req.distorted}")
    if req.model not in ft.VMAF_MODELS:
        raise HTTPException(status_code=400, detail=f"Unknown model: {req.model}")

    job = job_manager.create(
        req.reference, req.distorted, req.model, n_threads=req.n_threads, n_subsample=req.n_subsample
    )
    asyncio.create_task(job_manager.run(job.id))
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.public_dict()


@app.post("/api/jobs/{job_id}/cancel")
async def api_job_cancel(job_id: str):
    ok = job_manager.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")
    return {"cancelled": True}


def _get_finished_job(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(status_code=400, detail=f"Job is not finished (status: {job.status})")
    return job


@app.get("/api/jobs/{job_id}/report.html")
async def api_job_report_html(job_id: str):
    job = _get_finished_job(job_id)
    return HTMLResponse(report_mod.generate_report_html(job))


@app.get("/api/jobs/{job_id}/report.json")
async def api_job_report_json(job_id: str):
    job = _get_finished_job(job_id)
    return JSONResponse(
        report_mod.generate_report_json(job),
        headers={"Content-Disposition": f'attachment; filename="frame-guard-report-{job_id}.json"'},
    )


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
