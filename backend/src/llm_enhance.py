"""Optional LLM enhancement layer (used only when USE_LLM=true).

Given the candidate profile and matched jobs, produces a concise AI-written
match summary for each job via the multi-provider fallback router, and traces
those LLM calls through Langfuse.
"""
from typing import Optional

from llm import call_llm
from tracing import trace_run


def _system_prompt() -> str:
    return (
        "You are a career-match assistant. Given a candidate's skills/experience "
        "and a job description, write 2-3 crisp sentences on why the candidate "
        "is a fit and any gaps. Be concise, factual, no markdown."
    )


def enhance_jobs(profile: dict, jobs: list[dict]) -> list[dict]:
    """Attach an `ai_summary` to each job via the LLM router.
    Returns a copy of the jobs list (originals are not mutated).
    """
    total = len(jobs)
    with trace_run(
        "llm_enhance",
        metadata={
            "job_count": total,
            "experience_years": profile.get("experience_years", 0),
            "skills": profile.get("skills", []),
        },
    ) as span:
        out = []
        for job in jobs:
            skills_txt = ", ".join(profile.get("skills", []))
            prompt = (
                f"Candidate skills: {skills_txt}\n"
                f"Candidate years of experience: {profile.get('experience_years', 'n/a')}\n"
                f"Job title: {job.get('title', '')}\n"
                f"Job description: {job.get('description', '')}\n"
                f"Match score: {job.get('match_score', 0)}%\n\n"
                "Write the short fit/gaps summary."
            )
            try:
                summary = call_llm(prompt, system_prompt=_system_prompt())
            except RuntimeError as exc:
                summary = f"AI summary unavailable ({exc})"
            span.generation(
                name="job_match_summary",
                model="fallback-router",
                prompt=prompt,
                output=summary,
            )
            item = dict(job)
            item["ai_summary"] = summary
            out.append(item)
    return out


if __name__ == "__main__":
    demo_profile = {
        "skills": ["python", "fastapi", "sql", "docker", "git"],
        "experience_years": 5,
    }
    demo_jobs = [
        {"title": "Python Backend Engineer", "description": "FastAPI, PostgreSQL, Docker", "match_score": 82.5},
    ]
    print(enhance_jobs(demo_profile, demo_jobs))
