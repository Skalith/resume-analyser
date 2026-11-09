"""Job-match scoring — corrected TF-IDF + job-centric skill overlap.

Two bugs in the original version are fixed here:

1. **Isolated TF-IDF per pair** — the original called
   ``vectorizer.fit_transform([resume, job])`` inside a loop, so every job got
   its own private vocabulary and the scores were incomparable across jobs.
   This version fits *one* ``TfidfVectorizer`` on the entire corpus
   (resume + all job texts) and then scores each job against that shared space.

2. **Profile-centric skill overlap** — the original divided matched-skill count
   by ``len(profile_skills)``, which means a candidate with many skills always
   got a lower overlap than one with few skills, even when both matched all the
   job's requirements.  This version divides by ``len(job_skill_mentions)``
   (job-centric).

The final ``match_score`` is a weighted blend:
    match_score = (0.6 × cosine + 0.4 × skill_overlap) × 100
"""
import re
from typing import Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from skills import extract_skills

# Weights for blending TF-IDF cosine with skill-overlap ratio
_COSINE_WEIGHT = 0.6
_OVERLAP_WEIGHT = 0.4


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _job_text(job: dict) -> str:
    """Concatenate title + description into the text used for scoring."""
    return " ".join(
        filter(None, [job.get("title", ""), job.get("description", "")])
    )


def _job_required_skills(job_text_lower: str) -> list[str]:
    """Return list of known skills explicitly mentioned in a job posting."""
    from skills import _SKILL_PATTERNS  # reuse pre-compiled patterns

    return [s for s, pat in _SKILL_PATTERNS.items() if pat.search(job_text_lower)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_jobs(resume_text: str, jobs: list[dict], profile: Optional[dict] = None) -> list[dict]:
    """Attach a ``match_score`` (0–100 %) to each job dict.

    Parameters
    ----------
    resume_text : str
        Raw text extracted from the candidate's resume.
    jobs : list[dict]
        Job listings, each with at least ``title`` and ``description`` keys.
    profile : dict or None
        Pre-extracted profile ``{"skills": [...], "experience_years": int}``.
        Computed from ``resume_text`` if not provided.

    Returns
    -------
    The same list with ``match_score`` and ``matched_skills`` added in-place.
    """
    if profile is None:
        profile = extract_skills(resume_text)

    candidate_skills: list[str] = profile.get("skills", [])

    if not jobs:
        return jobs

    # Build all job texts before constructing the shared TF-IDF corpus
    job_texts = [_job_text(j) for j in jobs]

    # -----------------------------------------------------------------
    # Step 5: One shared TF-IDF vectorizer across resume + all jobs
    # -----------------------------------------------------------------
    corpus = [resume_text] + job_texts
    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
    )
    try:
        tfidf_matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        # Fallback: empty corpus after preprocessing
        for job in jobs:
            job["match_score"] = 0.0
            job["matched_skills"] = []
        return jobs

    resume_vec = tfidf_matrix[0:1]  # shape (1, vocab_size)

    for idx, job in enumerate(jobs):
        job_vec = tfidf_matrix[idx + 1 : idx + 2]  # shape (1, vocab_size)

        # Cosine similarity from shared vector space
        cosine = float(cosine_similarity(resume_vec, job_vec)[0][0])

        # -----------------------------------------------------------------
        # Step 6: Job-centric skill overlap
        # overlap = matched_skills / job_required_skills   (not / candidate_skills)
        # -----------------------------------------------------------------
        job_text_lower = job_texts[idx].lower()
        job_required = _job_required_skills(job_text_lower)

        # Skills the candidate has that the job also requires
        matched = [s for s in candidate_skills if s in set(job_required)]

        # Skills the job requires that the candidate does NOT have
        candidate_set = set(candidate_skills)
        missing = [s for s in job_required if s not in candidate_set]

        overlap = len(matched) / len(job_required) if job_required else 0.0

        job["match_score"] = round(
            (_COSINE_WEIGHT * cosine + _OVERLAP_WEIGHT * overlap) * 100, 2
        )
        job["matched_skills"] = matched
        job["missing_skills"] = missing

    return jobs


def filter_by_threshold(jobs: list[dict], threshold: float = 0.20) -> list[dict]:
    """Keep only jobs with ``match_score`` ≥ ``threshold * 100``.

    ``threshold`` is in the same 0–1 fraction form used in the .env file.
    ``match_score`` is stored as 0–100, so we multiply threshold by 100.
    """
    cutoff = threshold * 100
    return [j for j in jobs if j.get("match_score", 0) >= cutoff]


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    resume = (
        "Senior Python backend engineer with 5 years of experience using "
        "FastAPI, SQL, PostgreSQL, Pandas, Docker, and Redis."
    )
    sample_jobs = [
        {"id": "1", "title": "Python Backend Developer",
         "description": "We need FastAPI, PostgreSQL, Redis, Docker experience."},
        {"id": "2", "title": "Java Developer",
         "description": "Java, Spring Boot, Hibernate, C# desktop apps"},
        {"id": "3", "title": "Data Scientist",
         "description": "Pandas, NumPy, machine learning, Python, scikit-learn"},
        {"id": "4", "title": "Frontend Engineer",
         "description": "React, TypeScript, CSS, HTML, Node.js"},
    ]
    scored = score_jobs(resume, sample_jobs)
    print("--- Scores (shared TF-IDF corpus, job-centric skill overlap) ---")
    for job in sorted(scored, key=lambda j: j["match_score"], reverse=True):
        print(f"  {job['match_score']:5.1f}%  {job['title']}")
        print(f"         matched: {job['matched_skills']}")

    print("\n--- Above 20% threshold ---")
    for job in filter_by_threshold(scored, 0.20):
        print(f"  {job['title']}")
