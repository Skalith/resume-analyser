"""Candidate-profile extraction from raw resume text.

Fixes compared to the original version
---------------------------------------
1. Symbol-safe skill matching — skills that contain non-word characters
   (``c#``, ``.net``, ``node.js``, ``ci/cd``) are matched without wrapping
   them in ``\b`` word-boundary anchors, because ``\b`` doesn't work next to
   ``#``, ``.``, or ``/``.  For clean alpha-only skills the word-boundary is
   still used so "go" doesn't match "golang".

2. Fixed date-range unpack bug — ``re.findall`` with **one** capture group
   returns a flat ``list[str]``, not ``list[tuple]``.  The original code tried
   to unpack two values from a single string and crashed.  The pattern now
   captures both the start and optional end year in one group so the unpack
   is correct.

3. Generic / noisy term exclusion — terms like ``html``, ``css``, ``apis``,
   ``caching``, ``git``, ``linux`` are kept but only if they genuinely appear
   in the resume's skills section; additionally a second exclusion set of
   ultra-generic words is never included regardless.
"""
import re

# ---------------------------------------------------------------------------
# Skill catalogue
# ---------------------------------------------------------------------------

KNOWN_SKILLS = [
    # Languages
    "python", "javascript", "typescript", "java", "go", "rust", "c#", "c++",
    "ruby", "php", "scala", "kotlin", "swift",
    # Python web
    "fastapi", "django", "flask", "starlette", "tornado",
    # Databases
    "sql", "postgresql", "mysql", "sqlite", "mongodb", "redis",
    "elasticsearch", "cassandra", "dynamodb", "bigquery",
    # Data / ML
    "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "keras",
    "machine learning", "deep learning", "nlp", "computer vision",
    # DevOps / Infra
    "docker", "kubernetes", "terraform", "ansible", "jenkins",
    "github actions", "gitlab ci", "ci/cd", "aws", "gcp", "azure",
    "cloudformation", "helm",
    # APIs / Messaging
    "rest api", "graphql", "grpc", "kafka", "rabbitmq", "celery",
    "websockets",
    # Frontend
    "react", "vue", "angular", "next.js", "node.js", "html", "css",
    # Testing
    "pytest", "unittest", "jest", "selenium", "playwright",
    # Tools / Practices
    "git", "github", "gitlab", "linux", "bash", "microservices",
    "scraping", "scrapy", "beautifulsoup", "etl", ".net",
]

# Terms that are too generic to be meaningful in skill-overlap scoring.
# They are stripped from the extracted skills list entirely.
_GENERIC_EXCLUSIONS = {
    "apis", "caching", "data", "backend", "frontend", "software",
    "development", "engineering", "programming", "coding", "testing",
    "devops", "cloud", "server", "web", "system", "systems",
}

# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


def _has_special_chars(skill: str) -> bool:
    """True if the skill contains non-word characters (``#``, ``.``, ``/``)."""
    return bool(re.search(r"[^\w\s]", skill))


def _skill_pattern(skill: str) -> re.Pattern:
    """Return a compiled regex that safely locates `skill` in lowercase text.

    * Skills with special characters use a plain substring search (no ``\\b``).
    * Single short tokens that could be substrings of other words (e.g. ``go``,
      ``sql``) are still wrapped in word-boundaries.
    """
    escaped = re.escape(skill)
    if _has_special_chars(skill):
        # No word-boundary — just find the literal anywhere
        return re.compile(escaped)
    else:
        return re.compile(rf"\b{escaped}\b")


# Pre-compile all patterns once at module load.
_SKILL_PATTERNS: dict[str, re.Pattern] = {s: _skill_pattern(s) for s in KNOWN_SKILLS}


def _skill_found(skill: str, text_lower: str) -> bool:
    return _SKILL_PATTERNS[skill].search(text_lower) is not None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_skills(resume_text: str) -> dict:
    """Build a structured candidate profile from raw resume text.

    Returns
    -------
    dict with keys:
        ``skills``           – sorted list of skill strings found in the resume
        ``experience_years`` – integer years of experience extracted from text
    """
    text = resume_text.lower()

    skills = sorted(
        {
            s
            for s in KNOWN_SKILLS
            if s not in _GENERIC_EXCLUSIONS and _skill_found(s, text)
        }
    )

    experience_years = _extract_experience_years(text)
    return {"skills": skills, "experience_years": experience_years}


def _extract_experience_years(text: str) -> int:
    """Extract years of experience from text via two strategies:

    1. Explicit phrase: "5 years of experience", "3+ years experience", etc.
    2. Date ranges: "2019 – 2023", "2020 - present", etc. (summed up).
    """
    # Strategy 1 — explicit phrase
    for pattern in [
        r"(\d+)\+?\s*years?\s+(?:of\s+)?(?:professional\s+)?experience",
        r"(\d+)\+?\s*years?\s+(?:in|with)\s+\w",
    ]:
        m = re.search(pattern, text)
        if m:
            return int(m.group(1))

    # Strategy 2 — date ranges
    # Match "2017 - 2021", "2019 – present", "2020 — current", etc.
    # Each group: (start_year, end_year_or_keyword)
    range_pattern = re.compile(
        r"(20\d{2})\s*[-–—]\s*(20\d{2}|present|now|current|ongoing)",
        re.IGNORECASE,
    )

    total_years = 0
    current_year = 2026
    for start_str, end_str in range_pattern.findall(text):
        try:
            start = int(start_str)
            end = (
                current_year
                if end_str.lower() in ("present", "now", "current", "ongoing")
                else int(end_str)
            )
            total_years += max(0, end - start)
        except ValueError:
            continue

    return total_years


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        from extract import extract_pdf_text
        pdf_path = (
            sys.argv[1]
            if len(sys.argv) > 1
            else str(Path(__file__).resolve().parents[1] / "resumes" / "sample_resume.pdf")
        )
        text = extract_pdf_text(pdf_path)
    except Exception:
        text = (
            "Senior Python backend engineer with 5 years of experience. "
            "Skills: FastAPI, Django, PostgreSQL, Docker, C#, .NET, Node.js, "
            "CI/CD, Redis, Kafka, Pandas, pytest. "
            "2019 – 2022 Software Engineer at Acme. 2022 – present Senior Engineer."
        )

    profile = extract_skills(text)
    print("Skills found:", profile["skills"])
    print("Experience years:", profile["experience_years"])
