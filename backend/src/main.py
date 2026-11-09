"""FastAPI backend for the Resume Job Matcher.

Endpoints
---------
GET  /                    — serve the frontend dashboard (index.html)
POST /resume/upload       — accept a PDF upload, save it, trigger pipeline
POST /pipeline/run        — trigger a pipeline run with the current resume
GET  /jobs                — return matched jobs from the database
GET  /profile             — return the latest candidate profile snapshot

Auth
----
All API endpoints require the ``X-API-Key`` header to match the ``API_KEY``
env var. If ``API_KEY`` is empty the check is skipped (dev mode).

Pipeline
--------
``POST /pipeline/run`` enqueues the pipeline via FastAPI ``BackgroundTasks``
so the response returns immediately while the pipeline runs in-process in a
background thread.  No subprocess, no wrong-interpreter bug.

Startup
-------
    cd backend
    uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
"""
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader

# ---------------------------------------------------------------------------
# Paths & env
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
RESUMES_DIR = BACKEND_DIR / "resumes"
OUTPUT_DIR = BACKEND_DIR / "output"
ENV_FILE = BACKEND_DIR / ".env"

load_dotenv(ENV_FILE)

sys.path.insert(0, str(SRC_DIR))

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _check_api_key(api_key: Optional[str] = Depends(_API_KEY_HEADER)) -> None:
    """Dependency that validates X-API-Key.  No-ops when API_KEY env is empty."""
    expected = os.getenv("API_KEY", "").strip()
    if not expected:
        return  # dev mode: no key required
    if not secrets.compare_digest(api_key or "", expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )


# ---------------------------------------------------------------------------
# Pipeline state (simple in-memory flag — not for multi-worker deployments)
# ---------------------------------------------------------------------------

_pipeline_state: dict[str, Any] = {
    "running": False,
    "last_run_at": None,
    "last_error": None,
}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Resume Job Matcher",
    description="Upload your resume, run the pipeline, browse matched jobs.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
def on_startup() -> None:
    """Initialise the database on startup."""
    RESUMES_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    from database import init_db

    init_db()
    print("[main] database initialised")


# ---------------------------------------------------------------------------
# Internal pipeline runner
# ---------------------------------------------------------------------------


def _run_pipeline_background(resume_path: Optional[str] = None) -> None:
    """Execute the full pipeline in a background thread."""
    _pipeline_state["running"] = True
    _pipeline_state["last_error"] = None

    try:
        _execute_pipeline(resume_path)
    except Exception as exc:  # noqa: BLE001
        _pipeline_state["last_error"] = str(exc)
        print(f"[main] pipeline error: {exc}")
    finally:
        _pipeline_state["running"] = False
        _pipeline_state["last_run_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")


def _execute_pipeline(override_resume_path: Optional[str] = None) -> None:
    """Core pipeline logic — runs synchronously, called from background thread."""
    from database import (
        init_db,
        is_duplicate,
        log_notification,
        mark_seen,
        save_profile_snapshot,
    )
    from extract import extract_pdf_text
    from jobs import fetch_jobs
    from match import filter_by_threshold, score_jobs
    from notify import send_notification
    from skills import extract_skills

    # Ensure latest .env values are loaded
    load_dotenv(ENV_FILE, override=True)

    # Ensure DB tables exist (idempotent)
    init_db()

    # Step 2 — extract resume text
    resume_path_str = override_resume_path or os.getenv("RESUME_PATH", "resumes/resume.pdf")
    resume_file = Path(resume_path_str)
    if not resume_file.is_absolute():
        resume_file = BACKEND_DIR / resume_file
    if not resume_file.exists():
        raise FileNotFoundError(f"Resume not found: {resume_file}")

    resume_text = extract_pdf_text(str(resume_file))
    print(f"[pipeline] extracted resume: {len(resume_text)} chars")

    # Step 3 — build candidate profile
    profile = extract_skills(resume_text)
    print(
        f"[pipeline] profile: {profile['experience_years']}y exp, "
        f"{len(profile['skills'])} skills"
    )
    save_profile_snapshot(profile, resume_path=str(resume_file))

    # Step 4 — fetch jobs
    source = os.getenv("JOB_SOURCE", "remotive")
    # Use top skills as the Adzuna query
    query = " ".join(profile.get("skills", [])[:5]) or "python developer"
    jobs = fetch_jobs(
        source=source,
        app_id=os.getenv("ADZUNA_APP_ID", ""),
        app_key=os.getenv("ADZUNA_APP_KEY", ""),
        imap_host=os.getenv("IMAP_HOST", ""),
        email_user=os.getenv("EMAIL_USER", ""),
        email_pass=os.getenv("EMAIL_PASS", ""),
        limit=int(os.getenv("JOBS_LIMIT", "50")),
        query=query,
    )
    print(f"[pipeline] fetched {len(jobs)} jobs from {source}")

    # Steps 5-6 — shared TF-IDF score
    jobs = score_jobs(resume_text, jobs, profile=profile)

    # Step 7 — threshold filter
    threshold = float(os.getenv("THRESHOLD", "0.20"))
    jobs = filter_by_threshold(jobs, threshold)
    print(f"[pipeline] {len(jobs)} jobs above {threshold:.0%} threshold")

    # Step 8 — optional LLM enhancement
    if os.getenv("USE_LLM", "false").lower() == "true":
        from llm_enhance import enhance_jobs

        print("[pipeline] USE_LLM=true — enhancing with AI summaries")
        jobs = enhance_jobs(profile, jobs)

    # Steps 9-10 — deduplicate + notify
    fresh = 0
    channel = os.getenv("NOTIFY_CHANNEL", "telegram")
    for job in sorted(jobs, key=lambda j: j["match_score"], reverse=True):
        if is_duplicate(job["id"]):
            mark_seen(job, notified=False)
            continue
        mark_seen(job, notified=True)
        fresh += 1
        try:
            sent = send_notification(job)
            log_notification(job["id"], channel, success=sent)
        except Exception as exc:  # noqa: BLE001
            log_notification(job["id"], channel, success=False, error=str(exc))
            print(f"[pipeline] notification failed for {job['title']}: {exc}")

    print(f"[pipeline] sent {fresh} new notifications")


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def serve_frontend() -> FileResponse:
    """Serve the frontend dashboard."""
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index))


@app.post("/resume/upload", dependencies=[Depends(_check_api_key)])
async def upload_resume(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> JSONResponse:
    """Accept a PDF resume, save it, and kick off a pipeline run.

    Returns immediately with ``{"status": "running"}``; the pipeline
    executes in the background.
    """
    if _pipeline_state["running"]:
        raise HTTPException(status_code=409, detail="Pipeline is already running")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    contents = await file.read()
    if len(contents) < 4 or contents[:4] != b"%PDF":
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF")

    RESUMES_DIR.mkdir(exist_ok=True)
    dest = RESUMES_DIR / "resume.pdf"
    dest.write_bytes(contents)
    print(f"[upload] saved resume: {dest} ({len(contents)} bytes)")

    # Update RESUME_PATH in .env if it exists
    _update_env_value("RESUME_PATH", "resumes/resume.pdf")

    background_tasks.add_task(_run_pipeline_background, str(dest))
    return JSONResponse(
        {"status": "running", "message": "Resume saved. Pipeline started in background."},
        status_code=202,
    )


@app.post("/pipeline/run", dependencies=[Depends(_check_api_key)])
def run_pipeline(background_tasks: BackgroundTasks) -> JSONResponse:
    """Trigger a pipeline run with the current resume.

    Returns immediately with ``{"status": "running"}``.
    """
    if _pipeline_state["running"]:
        return JSONResponse(
            {"status": "already_running", "message": "Pipeline is already running"},
            status_code=409,
        )

    background_tasks.add_task(_run_pipeline_background)
    return JSONResponse(
        {
            "status": "running",
            "message": "Pipeline started in background.",
            "last_run_at": _pipeline_state.get("last_run_at"),
        },
        status_code=202,
    )


@app.get("/pipeline/status", dependencies=[Depends(_check_api_key)])
def pipeline_status() -> JSONResponse:
    """Return current pipeline running state."""
    return JSONResponse(
        {
            "running": _pipeline_state["running"],
            "last_run_at": _pipeline_state.get("last_run_at"),
            "last_error": _pipeline_state.get("last_error"),
        }
    )


@app.get("/jobs", dependencies=[Depends(_check_api_key)])
def get_jobs(limit: int = 200) -> JSONResponse:
    """Return matched jobs from the database, sorted by match_score desc."""
    from database import get_latest_jobs

    jobs = get_latest_jobs(limit=limit)
    return JSONResponse({"jobs": jobs, "count": len(jobs)})


@app.get("/profile", dependencies=[Depends(_check_api_key)])
def get_profile() -> JSONResponse:
    """Return the latest candidate profile snapshot."""
    from database import get_profile as _get_profile

    profile = _get_profile()
    if profile is None:
        return JSONResponse(
            {"profile": None, "message": "No profile yet — run the pipeline first"},
            status_code=404,
        )
    return JSONResponse({"profile": profile})


@app.post("/jobs/{job_id}/status", dependencies=[Depends(_check_api_key)])
def set_job_status(job_id: str, body: dict) -> JSONResponse:
    """Set the saved_status of a job to 'saved', 'dismissed', or None.

    Request body: ``{"status": "saved" | "dismissed" | null}``
    """
    from database import update_job_status

    allowed = {"saved", "dismissed", None}
    new_status = body.get("status")
    if new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: 'saved', 'dismissed', or null",
        )
    updated = update_job_status(job_id, new_status)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return JSONResponse({"job_id": job_id, "status": new_status})


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _update_env_value(key: str, value: str) -> None:
    """Set/replace a KEY=value line in .env, creating the file if missing."""
    import re

    if not ENV_FILE.exists():
        return  # don't create .env from scratch — user should copy .env.example
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    found = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entrypoint (for running directly with `python src/main.py`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=False)
