"""SQLAlchemy-backed persistence layer for the Resume Job Matcher.

Tables
------
jobs             — every job seen by the pipeline; used for deduplication
                   and for serving /jobs results.
profile_history  — snapshot of the candidate profile on each pipeline run.
notification_log — record of every notification sent (Telegram/Discord).

Usage
-----
    from database import init_db, is_duplicate, mark_seen, save_profile_snapshot

    init_db()                 # call once at startup
    if not is_duplicate(job_id):
        mark_seen(job)
        save_profile_snapshot(profile)
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _utcnow() -> datetime:
    """Naive UTC timestamp for SQLite compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# ---------------------------------------------------------------------------
# Engine / Session setup
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "jobs.db"


def _make_engine(db_path: str | Path = DEFAULT_DB_PATH):
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )


_engine = _make_engine()
_SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------


class _Base(DeclarativeBase):
    pass


class Job(_Base):
    """One job seen (and potentially notified) by the pipeline."""

    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=False, default="")
    company = Column(String, nullable=False, default="")
    url = Column(String, nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    match_score = Column(Float, nullable=False, default=0.0)
    matched_skills = Column(JSON, nullable=False, default=list)
    missing_skills = Column(JSON, nullable=False, default=list)
    ai_summary = Column(Text, nullable=True)
    notified = Column(Boolean, nullable=False, default=False)
    saved_status = Column(String, nullable=True)  # "saved" | "dismissed" | None
    first_seen_at = Column(DateTime, nullable=False, default=_utcnow)


class ProfileHistory(_Base):
    """Snapshot of the candidate profile saved on every pipeline run."""

    __tablename__ = "profile_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    skills = Column(JSON, nullable=False, default=list)
    experience_years = Column(Integer, nullable=False, default=0)
    resume_path = Column(String, nullable=False, default="")
    recorded_at = Column(DateTime, nullable=False, default=_utcnow)


class NotificationLog(_Base):
    """One notification sent (Telegram or Discord) per job."""

    __tablename__ = "notification_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, nullable=False)
    channel = Column(String, nullable=False, default="")
    success = Column(Boolean, nullable=False, default=True)
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=False, default=_utcnow)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Create tables if they don't already exist."""
    global _engine, _SessionLocal
    _engine = _make_engine(db_path)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    _Base.metadata.create_all(_engine)


def _session() -> Session:
    return _SessionLocal()


def is_duplicate(job_id: str) -> bool:
    """Return True if a job with this ID has already been processed."""
    with _session() as s:
        return s.get(Job, job_id) is not None


def mark_seen(job: dict, notified: bool = True) -> None:
    """Insert or update a job record in the database."""
    with _session() as s:
        existing = s.get(Job, job.get("id", ""))
        if existing:
            # Update score and notification flag if already present
            existing.match_score = job.get("match_score", existing.match_score)
            existing.notified = notified or existing.notified
            existing.ai_summary = job.get("ai_summary", existing.ai_summary)
        else:
            s.add(
                Job(
                    id=job.get("id", ""),
                    title=job.get("title", ""),
                    company=job.get("company", ""),
                    url=job.get("url", ""),
                    description=job.get("description", ""),
                    match_score=job.get("match_score", 0.0),
                    matched_skills=job.get("matched_skills", []),
                    missing_skills=job.get("missing_skills", []),
                    ai_summary=job.get("ai_summary"),
                    notified=notified,
                    first_seen_at=_utcnow(),
                )
            )
        s.commit()


def save_profile_snapshot(profile: dict, resume_path: str = "") -> None:
    """Persist a profile snapshot for historical tracking."""
    with _session() as s:
        s.add(
            ProfileHistory(
                skills=profile.get("skills", []),
                experience_years=profile.get("experience_years", 0),
                resume_path=resume_path,
                recorded_at=_utcnow(),
            )
        )
        s.commit()


def log_notification(job_id: str, channel: str, success: bool = True, error: str = "") -> None:
    """Record a notification attempt."""
    with _session() as s:
        s.add(
            NotificationLog(
                job_id=job_id,
                channel=channel,
                success=success,
                error_message=error or None,
                sent_at=_utcnow(),
            )
        )
        s.commit()


def get_latest_jobs(limit: int = 200) -> list[dict]:
    """Return the most recently seen jobs as plain dicts."""
    with _session() as s:
        rows = (
            s.query(Job)
            .order_by(Job.match_score.desc(), Job.first_seen_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "title": r.title,
                "company": r.company,
                "url": r.url,
                "description": r.description,
                "match_score": r.match_score,
                "matched_skills": r.matched_skills or [],
                "missing_skills": r.missing_skills or [],
                "ai_summary": r.ai_summary,
                "notified": r.notified,
                "saved_status": r.saved_status,
                "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
            }
            for r in rows
        ]


def update_job_status(job_id: str, status: Optional[str]) -> bool:
    """Set or clear the saved_status field for a job.

    Parameters
    ----------
    job_id : str
        The job's primary key.
    status : str or None
        ``'saved'``, ``'dismissed'``, or ``None`` (clears the flag).

    Returns
    -------
    True if the job existed and was updated, False if not found.
    """
    with _session() as s:
        job = s.get(Job, job_id)
        if job is None:
            return False
        job.saved_status = status
        s.commit()
        return True


def get_profile() -> Optional[dict]:
    """Return the most recent profile snapshot, or None."""
    with _session() as s:
        row = (
            s.query(ProfileHistory)
            .order_by(ProfileHistory.recorded_at.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "skills": row.skills or [],
            "experience_years": row.experience_years,
            "resume_path": row.resume_path,
            "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        }


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile, os

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        init_db(db)

        test_job = {
            "id": "test-001",
            "title": "Python Backend Engineer",
            "company": "Acme",
            "url": "https://example.com",
            "description": "FastAPI, Postgres",
            "match_score": 82.5,
            "matched_skills": ["python", "fastapi"],
        }
        print("duplicate before:", is_duplicate("test-001"))
        mark_seen(test_job)
        print("duplicate after:", is_duplicate("test-001"))

        save_profile_snapshot({"skills": ["python", "fastapi"], "experience_years": 3})
        profile = get_profile()
        print("profile:", profile)

        jobs = get_latest_jobs()
        print("jobs in db:", len(jobs), "—", jobs[0]["title"])
        print("ALL OK")
