import database
from jobs import _has_exclusion_term, _location_allowed
from match import filter_by_threshold, score_jobs
from skills import extract_skills


def test_extract_skills_handles_symbols_and_explicit_experience():
    profile = extract_skills(
        "5 years of experience. Python, FastAPI, C#, .NET, Node.js, "
        "CI/CD, PostgreSQL, Docker."
    )
    assert profile["experience_years"] == 5
    assert {"python", "fastapi", "c#", ".net", "node.js", "ci/cd"}.issubset(
        set(profile["skills"])
    )


def test_date_ranges_do_not_crash_and_are_summed():
    profile = extract_skills(
        "Python engineer. 2019 - 2021 at Acme. 2021 - present at Beta."
    )
    assert profile["experience_years"] == 7


def test_scoring_is_job_centric_and_filter_uses_percentage():
    resume = (
        "5 years of experience with Python, FastAPI, PostgreSQL, Docker "
        "and Redis."
    )
    jobs = [
        {
            "id": "backend",
            "title": "Python Backend Engineer",
            "description": "Build FastAPI services with PostgreSQL and Docker.",
        },
        {
            "id": "chef",
            "title": "Chef",
            "description": "Prepare meals in a restaurant kitchen.",
        },
    ]

    scored = score_jobs(resume, jobs)
    by_id = {job["id"]: job for job in scored}
    assert by_id["backend"]["match_score"] > by_id["chef"]["match_score"]
    assert {"python", "fastapi", "postgresql", "docker"}.issubset(
        set(by_id["backend"]["matched_skills"])
    )
    assert "redis" not in by_id["backend"]["matched_skills"]
    assert [job["id"] for job in filter_by_threshold(scored, 0.20)] == ["backend"]


def test_location_and_exclusion_rules():
    assert _location_allowed("Worldwide")
    assert _location_allowed("India")
    assert not _location_allowed("USA")
    assert _has_exclusion_term("Remote, but US only")
    assert not _has_exclusion_term("Remote, worldwide")


def test_database_roundtrip(tmp_path):
    database.init_db(tmp_path / "jobs.db")
    assert not database.is_duplicate("job-1")

    database.mark_seen(
        {
            "id": "job-1",
            "title": "Python Engineer",
            "company": "Acme",
            "url": "https://example.com/job-1",
            "description": "FastAPI",
            "match_score": 81.5,
            "matched_skills": ["python"],
        }
    )
    assert database.is_duplicate("job-1")
    assert database.update_job_status("job-1", "saved")
    jobs = database.get_latest_jobs()
    assert jobs[0]["id"] == "job-1"
    assert jobs[0]["saved_status"] == "saved"
    assert not database.update_job_status("missing", "saved")
