"""Job fetching from Remotive and Adzuna (plus optional email source).

Fixes compared to the original version
---------------------------------------
1. **Location filter before limit** — ``fetch_remotive`` now fetches the full
   API response, filters by location, *then* slices to ``limit``.  Previously
   the slice happened first so relevant jobs at positions > limit were silently
   dropped.

2. **Exclusion-term check** — jobs that mention "US only", "US residents only",
   "must be based in the US", etc. are rejected even when "remote" also appears
   in the same text.

3. **Adzuna gets a real query param** — the original Adzuna call had no ``q``
   parameter, so it returned random jobs regardless of field.  ``fetch_adzuna``
   now accepts an optional ``query`` string and falls back to a sensible
   default ("python developer").  The country is read from the ``ADZUNA_COUNTRY``
   env var (default ``gb``).
"""
import os
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

USER_AGENT = {
    "User-Agent": "Mozilla/5.0 (resume-job-matcher/1.0; +python-requests)"
}

# ---------------------------------------------------------------------------
# Location allow / exclusion rules
# ---------------------------------------------------------------------------

_ALLOWED_LOCATION_TERMS = (
    "india",
    "tamil nadu",
    "chennai",
    "bangalore",
    "hyderabad",
    "mumbai",
    "asia",
    "apac",
    "remote",
    "worldwide",
    "anywhere",
    "global",
)

# If any of these phrases appear in the job text the job is rejected, even if
# "remote" is also mentioned.
_EXCLUSION_PHRASES = (
    "us only",
    "us residents only",
    "must be based in the us",
    "must be located in the us",
    "united states only",
    "usa only",
    "uk only",
    "eu only",
    "europe only",
    "must be eligible to work in the us",
    "authorized to work in the us",
    "us work authorization",
    "must reside in",
)


def _location_allowed(location: str) -> bool:
    """Return True if the job location string is open to India / worldwide."""
    loc = (location or "").lower()
    if not loc:
        return True
    return any(term in loc for term in _ALLOWED_LOCATION_TERMS)


def _has_exclusion_term(text: str) -> bool:
    """Return True if any geo-exclusion phrase is found in the full job text."""
    lower = text.lower()
    return any(phrase in lower for phrase in _EXCLUSION_PHRASES)


# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------


def _clean_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Remotive
# ---------------------------------------------------------------------------


def fetch_remotive(limit: int = 50) -> list[dict]:
    """Pull job listings from the Remotive remote-jobs API.

    Location filtering is applied **before** the limit slice so that the
    ``limit`` most relevant (location-allowed) jobs are returned.
    """
    response = requests.get(
        "https://remotive.com/api/remote-jobs", headers=USER_AGENT, timeout=15
    )
    response.raise_for_status()
    all_jobs = response.json().get("jobs", [])

    normalized: list[dict] = []
    for job in all_jobs:
        location = job.get("candidate_required_location", "")
        if not _location_allowed(location):
            continue

        tags = " ".join(job.get("tags") or [])
        description = " ".join(
            filter(
                None,
                [_clean_html(job.get("description", "")), job.get("category", ""), tags],
            )
        )

        # Reject jobs with geo-exclusion phrases even if they passed location filter
        if _has_exclusion_term(description):
            continue

        normalized.append(
            {
                "id": str(job["id"]),
                "title": job.get("title", ""),
                "company": job.get("company_name", ""),
                "description": description,
                "url": job.get("url", ""),
                "location": location,
            }
        )

        if len(normalized) >= limit:
            break

    return normalized


# ---------------------------------------------------------------------------
# Adzuna
# ---------------------------------------------------------------------------


def fetch_adzuna(
    app_id: str,
    app_key: str,
    country: Optional[str] = None,
    query: str = "python developer",
    limit: int = 50,
) -> list[dict]:
    """Pull job listings from the Adzuna Jobs API.

    Parameters
    ----------
    app_id, app_key : str
        Adzuna API credentials.
    country : str or None
        Two-letter country code (e.g. ``gb``, ``us``, ``in``).
        Reads from the ``ADZUNA_COUNTRY`` env var; defaults to ``gb``.
    query : str
        Search keywords (e.g. top skills from the candidate's profile).
    limit : int
        Maximum number of results to return.
    """
    if country is None:
        country = os.getenv("ADZUNA_COUNTRY", "gb").lower()

    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": min(limit, 50),  # Adzuna max per page is 50
        "what": query,
        "content-type": "application/json",
    }
    response = requests.get(url, params=params, headers=USER_AGENT, timeout=15)
    response.raise_for_status()

    normalized: list[dict] = []
    for job in response.json().get("results", []):
        description = _clean_html(job.get("description", ""))
        if _has_exclusion_term(description):
            continue
        company = job.get("company", {})
        normalized.append(
            {
                "id": str(job.get("id", "")),
                "title": _clean_html(job.get("title", "")),
                "company": company.get("display_name", "") if isinstance(company, dict) else "",
                "description": description,
                "url": job.get("redirect_url", ""),
                "location": job.get("location", {}).get("display_name", "")
                if isinstance(job.get("location"), dict)
                else "",
            }
        )
    return normalized[:limit]


# ---------------------------------------------------------------------------
# Email / IMAP (LinkedIn Job Alerts)
# ---------------------------------------------------------------------------


def fetch_jobs_from_email(
    imap_host: str,
    email_user: str,
    email_pass: str,
    mailbox: str = "INBOX",
    search: str = "UNSEEN",
) -> list[dict]:
    """Read LinkedIn Job Alert emails from an IMAP inbox.

    Extracts description and apply URL from the email body so that TF-IDF
    scoring and skill-overlap can produce meaningful match scores.  Without
    these fields every email job scores 0 % and is filtered out before
    notification.

    The parser handles both ``text/plain`` and ``text/html`` MIME parts,
    preferring plain text.  URLs are extracted with a simple regex and the
    first ``http`` link in the body is used as the apply URL.
    """
    import email as email_lib
    import imaplib
    import quopri
    from email.header import decode_header

    mail = imaplib.IMAP4_SSL(imap_host)
    mail.login(email_user, email_pass)
    mail.select(mailbox)

    jobs: list[dict] = []
    _, data = mail.search(None, search)
    for num in data[0].split():
        _, msg_data = mail.fetch(num, "(RFC822)")
        raw = msg_data[0][1]
        message = email_lib.message_from_bytes(raw)

        # Decode subject
        raw_subject = decode_header(message["Subject"])[0][0]
        subject = raw_subject.decode() if isinstance(raw_subject, bytes) else str(raw_subject)
        if "job" not in subject.lower():
            continue

        # Extract body text from MIME parts
        body = ""
        for part in message.walk():
            ctype = part.get_content_type()
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            if ctype == "text/plain":
                body = payload.decode(charset, errors="replace")
                break  # prefer plain text; stop searching
            elif ctype == "text/html" and not body:
                body = _clean_html(payload.decode(charset, errors="replace"))

        description = body.strip()

        # Extract the first HTTP(S) URL from the body as the apply link
        url_match = re.search(r"https?://[^\s\"'>]+", description)
        apply_url = url_match.group(0) if url_match else ""

        jobs.append(
            {
                "id": f"email:{message['Message-ID']}",
                "title": subject.strip(),
                "company": "",
                "description": description,
                "url": apply_url,
                "location": "",
            }
        )
    mail.logout()
    return jobs


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


def fetch_jobs(
    source: str = "remotive",
    app_id: str = "",
    app_key: str = "",
    imap_host: str = "",
    email_user: str = "",
    email_pass: str = "",
    limit: int = 50,
    query: str = "python developer",
) -> list[dict]:
    """Fetch and normalise job listings from the configured source.

    Parameters
    ----------
    source : str
        ``"remotive"`` | ``"adzuna"`` | ``"email"``
    query : str
        Keyword query forwarded to Adzuna. Ignored for other sources.
    """
    if source == "remotive":
        return fetch_remotive(limit=limit)
    if source == "adzuna":
        return fetch_adzuna(app_id, app_key, query=query, limit=limit)
    if source == "email":
        return fetch_jobs_from_email(imap_host, email_user, email_pass)
    raise ValueError(f"Unknown job source: {source!r}. Use 'remotive', 'adzuna', or 'email'.")


# ---------------------------------------------------------------------------
# Standalone smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from pprint import pprint

    jobs = fetch_jobs(source="remotive", limit=5)
    print(f"fetched {len(jobs)} jobs\n")
    if jobs:
        pprint(jobs[0])
