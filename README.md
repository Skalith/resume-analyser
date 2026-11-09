# Resume Job Matcher

> **Version 2.** This standalone version extends the backend, matching engine, database layer, and
> React dashboard. Section 6 lists the main fixes and improvements.

An automated, intelligent career-matching platform that analyzes candidate resumes, extracts core competencies and verified experience, aggregates live job listings from remote and regional APIs, computes mathematically sound match scores, filters out weak listings, prevents duplicate alerts, and notifies you the moment a high-affinity role appears — all managed through a responsive **React 18 Dashboard** powered by a **FastAPI** backend.

---

## 1. Project Overview

The **Resume Job Matcher** solves the friction of continuous job hunting. Instead of manually searching job boards, you upload your resume once. The platform:
1. Validates and parses the PDF text.
2. Extracts your technical skills (with symbol-safe regex) and calculates years of experience.
3. Harvests live listings from job APIs (Remotive, Adzuna) with geo-exclusion rules.
4. Scores each listing against your profile using a **shared TF-IDF vector space** and **job-centric skill overlap**.
5. Filters out matches below your configurable score threshold.
6. (Optional) Generates AI-written fit/gap summaries via a multi-provider fallback router (OmniRoute → Gemini → Groq → NVIDIA) traced through Langfuse v4.
7. Deduplicates against SQLite to ensure you are never notified about the same job twice.
8. Pushes instant alerts to **Telegram** or **Discord** with rate-limit protection.
9. Serves an interactive **React 18 SPA** directly from FastAPI for resume management, live filtering, and application tracking (Save/Dismiss).

---

## 2. Tech Stack & Architecture Rationale

| Layer | Technology | Rationale |
|---|---|---|
| **Backend API** | **FastAPI** (`fastapi==0.115.6`, `uvicorn==0.34.0`) | High-performance async ASGI framework, auto-generated OpenAPI docs (`/docs`), type validation, direct demonstration of Python backend engineering. |
| **Background Processing** | **FastAPI `BackgroundTasks`** | In-process asynchronous task execution. Eliminates brittle subprocess calls and interpreter path conflicts. |
| **Persistence & ORM** | **SQLite + SQLAlchemy 2.0** (`sqlalchemy==2.0.36`) | Full historical tracking: candidate profile snapshots, matched jobs state, application tracking, and notification audit log. |
| **Document Parsing** | **pdfplumber** (`pdfplumber==0.11.5`) | Robust PDF extraction page-by-page, retaining structured character sequences. |
| **Machine Learning / NLP** | **scikit-learn** (`scikit-learn==1.6.1`) | Shared corpus TF-IDF vectorization + cosine similarity. |
| **Job Sources** | **Remotive API & Adzuna API** (+ IMAP email alert parser) | Live developer job listings with candidate-targeted query keywords and country selection. |
| **Push Notifications** | **Telegram Bot API / Discord Webhooks** | Real-time push alerts with configurable rate-limit sleep guards. |
| **LLM Fallback Layer** | **OmniRoute → Gemini → Groq → NVIDIA** | Resilient multi-provider AI router using standard OpenAI-compatible endpoints with zero extra SDK bloat. |
| **AI Observability** | **Langfuse v4** (`langfuse==4.15.1`) | Execution tracing for prompts, model completions, and token metrics with graceful no-op fallback when unconfigured. |
| **Frontend UI** | **React 18 SPA** | Component-driven dark UI with hooks (`useState`, `useEffect`, `useMemo`), local-first vendor loading with a CDN fallback, live threshold filtering, and application tracking. |
| **Security & Auth** | **API Key Header (`X-API-Key`)** | Protected REST endpoints with automatic dev-mode bypass when unconfigured. |

---

## 3. System Architecture

```text
                           ┌────────────────────────────────────────┐
                           │          REACT 18 DASHBOARD            │
                           │   • Drag & Drop PDF Resume Upload      │
                           │   • Candidate Profile & Skills View    │
                           │   • Real-Time Min-Score Slider         │
                           │   • Search, Sort & Company Filters     │
                           │   • Skills Gap Analysis & Save/Dismiss │
                           └───────────────────┬────────────────────┘
                                               │ REST (with X-API-Key)
                                               ▼
                         ┌──────────────────────────────────────────┐
                         │               FASTAPI APP                │
                         │  GET  /               GET  /jobs         │
                         │  POST /resume/upload  GET  /profile      │
                         │  POST /pipeline/run   POST /jobs/:id/stat│
                         │  GET  /pipeline/status                   │
                         └──────────────┬─────────────┬─────────────┘
                                        │             │
                Enqueues BackgroundTask │             │ Queries / Updates
                                        ▼             ▼
                         ┌────────────────────┐ ┌───────────────────┐
                         │  PIPELINE RUNNER   │ │  SQLITE DATABASE  │
                         │  (In-Process Thread│ │   (SQLAlchemy)    │
                         │   Worker)          │ │  • jobs           │
                         └─────────┬──────────┘ │  • profile_history│
                                   │            │  • notif_log      │
                                   ▼            └───────────────────┘
 ┌────────────────────────────────────────────────────────────────────────┐
 │                           PIPELINE MODULES                             │
 │                                                                        │
 │  1. extract.py       ──► PDF text extraction via pdfplumber            │
 │  2. skills.py        ──► Symbol-safe skill & date-range parsing        │
 │  3. jobs.py          ──► Geo-filtered job aggregation (Remotive/Adzuna)│
 │  4. match.py         ──► Shared TF-IDF cosine + job-centric overlap    │
 │  5. llm_enhance.py   ──► Optional fit/gap AI summaries (LLM Router)    │
 │  6. database.py      ──► Deduplication against SQLite                  │
 │  7. notify.py        ──► Rate-limited dispatch to Telegram / Discord   │
 └─────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                           EXTERNAL SERVICES                            │
 │  • Remotive API & Adzuna Jobs API                                      │
 │  • Telegram Bot API / Discord Webhook URL                              │
 │  • OmniRoute / Google Gemini / Groq / NVIDIA NIM (Optional AI)         │
 │  • Langfuse Cloud / Self-Hosted (Optional Tracing)                     │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 4. End-to-End Pipeline Workflow (12 Steps)

```text
[STEP 1: Upload]           PDF uploaded via POST /resume/upload; validates %PDF header bytes.
        │
[STEP 2: Extract]          pdfplumber extracts raw text across all pages.
        │
[STEP 3: Profile]          skills.py extracts technical skills and date-range experience years.
        │                  Snapshot is persisted to `profile_history`.
        │
[STEP 4: Fetch Jobs]       jobs.py fetches live listings (Remotive / Adzuna with skills keyword query).
        │                  Location filtering is applied BEFORE limit slicing; geo-exclusions enforced.
        │
[STEP 5: Shared Corpus]    match.py creates ONE TfidfVectorizer fitted on [resume_text] + [all job texts].
        │
[STEP 6: Scoring]          Calculates TF-IDF cosine similarity + Job-Centric Skill Overlap:
        │                  match_score = (0.60 × Cosine + 0.40 × Skill_Overlap) × 100
        │
[STEP 7: Filter]           Discards jobs scoring below THRESHOLD (e.g. 0.20 = 20%).
        │
[STEP 8: AI Summary]       (Optional: USE_LLM=true) Generates 2-3 sentence fit/gap analysis via
        │                  OmniRoute → Gemini → Groq → NVIDIA, traced in Langfuse v4.
        │
[STEP 9: Deduplicate]      Checks jobs table in SQLite. Identifies fresh unseen listings.
        │
[STEP 10: Notify]          Sends alert card to Telegram or Discord with rate-limiting pauses.
        │
[STEP 11: Persist]         Inserts/updates listings in SQLite (matched skills, missing skills, status).
        │
[STEP 12: Dashboard]       React 18 SPA polls /pipeline/status, re-fetches /jobs, and updates view.
```

---

## 5. Scoring Engine & Mathematical Formulation

### 1. Shared TF-IDF Vector Space
Previous implementations fitted isolated vectorizers per `[resume, job]` pair, causing document frequency distortion ($df=2$ for shared terms penalizing relevant keywords). 

The optimized pipeline fits **one global vocabulary** across the batch:
$$\text{Corpus} = [\text{Resume}] \cup \{\text{Job}_1, \text{Job}_2, \dots, \text{Job}_N\}$$
$$\text{Cosine Similarity} = \frac{\mathbf{v}_{\text{resume}} \cdot \mathbf{v}_{\text{job}}}{\|\mathbf{v}_{\text{resume}}\| \|\mathbf{v}_{\text{job}}\|}$$

### 2. Job-Centric Skill Overlap
Instead of dividing matched skills by candidate skill count (which severely penalized experienced candidates with 20+ skills), overlap is measured strictly against **what the job demands**:
$$\text{Job Required Skills} = \{s \in \text{KNOWN\_SKILLS} \mid s \in \text{Job Text}\}$$
$$\text{Matched Skills} = \text{Candidate Skills} \cap \text{Job Required Skills}$$
$$\text{Missing Skills} = \text{Job Required Skills} \setminus \text{Candidate Skills}$$
$$\text{Skill Overlap} = \begin{cases} \frac{|\text{Matched Skills}|}{|\text{Job Required Skills}|} & \text{if } |\text{Job Required Skills}| > 0 \\ 0.0 & \text{otherwise} \end{cases}$$

### 3. Composite Match Score
$$\text{Match Score} = \Big(0.60 \times \text{Cosine Similarity} + 0.40 \times \text{Skill Overlap}\Big) \times 100$$
The final score is stored as a normalized percentage ($0.0 - 100.0\%$).

---

## 6. Key Fixes & Resilience Improvements

* **Magic-Byte PDF Validation:** Uploads are validated against `b"%PDF"` magic bytes before writing to disk.
* **Symbol-Safe Skill Regex:** Skills containing symbols (`c#`, `.net`, `node.js`, `ci/cd`) bypass word-boundary anchors (`\b`) that fail next to non-word characters, while alpha-tokens (`go`, `sql`) retain boundary anchors to avoid substring false-positives.
* **Noisy Term Exclusions:** Generic words (`apis`, `caching`, `backend`, `frontend`, `software`) are stripped from skill results.
* **Unpack Crash Fix:** Date-range regex uses two capturing groups `(20\d{2})\s*[-–—]\s*(20\d{2}|present|now|current|ongoing)`, preventing unpack crashes on resumes without explicit "X years of experience" phrasing.
* **Pre-Slice Location Filtering:** Remotive listings are filtered by geography *before* slicing to `limit`, ensuring eligible worldwide/regional roles are not dropped prematurely.
* **Geo-Exclusion Enforcement:** Postings mentioning "US Only", "must be based in the US", or "UK Only" are rejected even when "Remote" appears in the description.
* **Notification Rate-Limiting:** Inter-message sleep delay (`rate_limit_sleep=1.5s`) prevents HTTP 429 rate-limit errors from Telegram or Discord.
* **Langfuse v4 API Compliance:** Upgraded from legacy syntax to `client.trace()`, `gen.end()`, and `client.flush()`, with silent no-op fallback when unconfigured.
* **In-Process Task Concurrency:** Standardized on FastAPI `BackgroundTasks`, eliminating subprocess failures and interpreter mismatches.

---

## 7. Project Layout

```text
resume_analyser/
├── backend/
│   ├── requirements.txt      # Pinned production dependencies (no dead deps)
│   ├── .env.example          # Comprehensive configuration template
│   ├── .env                  # Local secret configuration (excluded from git)
│   ├── jobs.db               # SQLite database (jobs, profile_history, notification_log)
│   ├── resumes/              # Storage directory for active candidate resumes
│   │   ├── resume.pdf        # Created when you upload a resume (git-ignored)
│   │   └── sample_resume.pdf # Sanitized fictional test fixture
│   └── src/
│       ├── __init__.py
│       ├── main.py           # FastAPI app, routing, background execution, auth
│       ├── database.py       # SQLAlchemy ORM models & database queries
│       ├── extract.py        # PDF text extraction (pdfplumber)
│       ├── skills.py         # Skill catalogue & experience extraction
│       ├── jobs.py           # Remotive, Adzuna & Email job harvesters
│       ├── match.py          # Shared TF-IDF vectorizer & scoring engine
│       ├── notify.py         # Telegram & Discord notifications + rate limiter
│       ├── llm.py            # Multi-provider fallback router (OmniRoute/Gemini/Groq/NVIDIA)
│       ├── llm_enhance.py    # Per-job AI match summary generator
│       └── tracing.py        # Langfuse v4 observability wrapper
└── frontend/
    ├── index.html            # React 18 Single-Page Application
    ├── vendor/               # (not in git) optional local React/ReactDOM/Babel copies; the page falls back to a CDN
    └── server.py             # Deprecated shim (notifies users to start FastAPI)
```

---

## 8. Prerequisites & Installation

### Prerequisites
* Python 3.10+
* Git
* Modern Web Browser

### 1. Clone & Set Up Virtual Environment

```bash
cd backend
python3 -m venv venv
source venv/bin/activate       # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` to configure your preferred sources and notification channels:

```ini
# Server Settings
HOST=127.0.0.1
PORT=8000
API_KEY=                      # Leave empty for dev mode; set for token auth

# Core Pipeline Configuration
RESUME_PATH=resumes/sample_resume.pdf
JOB_SOURCE=remotive           # remotive | adzuna | email
JOBS_LIMIT=50
THRESHOLD=0.20                # 0.20 = 20% minimum match score

# Notification Settings
NOTIFY_CHANNEL=none           # telegram | discord | none (safe default)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DISCORD_WEBHOOK_URL=

# Adzuna API (Required if JOB_SOURCE=adzuna)
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
ADZUNA_COUNTRY=gb             # gb | us | in

# Optional LLM & Observability (Off by default)
USE_LLM=false
GEMINI_API_KEY=
GROQ_API_KEY=
NVIDIA_API_KEY=
OMNIROUTE_API_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

---

### 3. First run: provide a resume

The pipeline needs a resume PDF. Either upload yours in the dashboard (it is saved as
`backend/resumes/resume.pdf`, which is git-ignored so it is never committed), or try the built-in fictional sample:

```ini
RESUME_PATH=resumes/sample_resume.pdf
```

Without one, running the pipeline stops with `Resume not found`.

## 9. Running the Application

Start the unified FastAPI server:

```bash
cd backend
source venv/bin/activate
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

* **React Dashboard:** Open [`http://127.0.0.1:8000`](http://127.0.0.1:8000)
* **Interactive API Documentation:** Open [`http://127.0.0.1:8000/docs`](http://127.0.0.1:8000/docs) (Swagger UI)
* **OpenAPI Schema:** Open [`http://127.0.0.1:8000/openapi.json`](http://127.0.0.1:8000/openapi.json)

---

## 10. React 18 Dashboard Features

The dashboard is served directly by FastAPI and runs a modern React 18 component tree:

* **Drag-and-Drop Resume Upload:** Drag any `.pdf` file into the upload zone or browse files. Uploads stream binary data directly to `POST /resume/upload`, triggering the background pipeline and auto-initiating status polling.
* **Candidate Profile Snapshot:** Displays total calculated experience years and skill badges identified on your resume.
* **Interactive Threshold Slider:** Slide the minimum match score threshold between 0% and 100% to filter displayed roles in real time.
* **Search & Filters:**
  * **Full-text search:** Real-time search across job title, company, matched skills, and full job descriptions.
  * **Sort:** By match score (high to low), title (A–Z), or company (A–Z).
  * **Company filter:** Auto-populated dropdown containing companies with matched jobs.
* **Skills Gap Analysis:** Each card highlights both matched skills (`✓ skill`) and missing required skills (`✗ skill`) with strikethrough styling.
* **Application Tracking:** Built-in **Save** (`💾`) and **Dismiss** (`✕`) buttons that persist status directly into SQLite via `POST /jobs/{id}/status`.
* **Expandable Job Cards:** Click any job card to expand the full description, external apply link, and AI fit/gap summary (when `USE_LLM=true`).
* **API Key Modal:** Configure or update the `X-API-Key` token stored in browser `localStorage`.

---

## 11. Tests

The backend has a small offline test suite for skill extraction, scoring,
location rules, and persistence:

```bash
cd backend
source venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=src pytest -q
```

The suite does not call job APIs, LLM providers, Telegram, or Discord.

---

## 12. REST API Reference

All API endpoints except the dashboard and OpenAPI docs require the `X-API-Key` header when `API_KEY` is set. Auth is automatically bypassed when `API_KEY` is empty in `.env` (local development only).

| Method | Endpoint | Description | Request Body | Response |
|---|---|---|---|---|
| `GET` | `/` | Serves the React 18 Single-Page Application | None | `text/html` |
| `POST` | `/resume/upload` | Uploads PDF resume and kicks off pipeline in background | `multipart/form-data` (`file`) | `{"status": "running", "message": "..."}` |
| `POST` | `/pipeline/run` | Triggers pipeline run using existing resume | None | `{"status": "running", "last_run_at": "..."}` |
| `GET` | `/pipeline/status` | Checks background execution status | None | `{"running": bool, "last_run_at": "...", "last_error": null}` |
| `GET` | `/jobs` | Returns matched jobs sorted by score desc | Query: `?limit=200` | `{"jobs": [...], "count": int}` |
| `GET` | `/profile` | Returns latest candidate profile snapshot | None | `{"profile": {"skills": [...], "experience_years": int}}` |
| `POST` | `/jobs/{job_id}/status` | Updates job saved status | `{"status": "saved" \| "dismissed" \| null}` | `{"job_id": str, "status": str}` |

---

## 13. Optional LLM & Observability Layer

The pipeline is completely functional using scikit-learn TF-IDF without any LLM configured. To activate AI fit summaries:

1. In `.env`, set:
   ```ini
   USE_LLM=true
   GEMINI_API_KEY=your_gemini_key_here
   ```
2. **Provider Fallback Strategy:** `src/llm.py` attempts providers in order:
   $$\text{OmniRoute} \longrightarrow \text{Gemini} \longrightarrow \text{Groq} \longrightarrow \text{NVIDIA}$$
   Setting any **single** provider key is sufficient; others act as automatic fallbacks.
3. **Langfuse Tracing:** When `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are provided, every LLM call records generations, latency, and prompt/response metadata in your Langfuse dashboard.

---

## 14. Database Schema (SQLite via SQLAlchemy)

### `jobs` Table
* `id` (`String`, Primary Key) — Unique job listing ID.
* `title` (`String`), `company` (`String`), `url` (`String`), `description` (`Text`).
* `match_score` (`Float`) — Combined score (0–100%).
* `matched_skills` (`JSON`) — Array of candidate skills required by the job.
* `missing_skills` (`JSON`) — Array of required skills absent from candidate profile.
* `ai_summary` (`Text`, Nullable) — AI fit summary.
* `notified` (`Boolean`) — Flag preventing duplicate notifications.
* `saved_status` (`String`, Nullable) — `"saved"`, `"dismissed"`, or `null`.
* `first_seen_at` (`DateTime`) — Timestamp of initial discovery.

### `profile_history` Table
* `id` (`Integer`, Primary Key) — Auto-incrementing snapshot ID.
* `skills` (`JSON`) — Extracted skills list.
* `experience_years` (`Integer`) — Verified experience years.
* `resume_path` (`String`) — Source file path.
* `recorded_at` (`DateTime`) — Pipeline execution timestamp.

### `notification_log` Table
* `id` (`Integer`, Primary Key) — Log ID.
* `job_id` (`String`), `channel` (`String`), `success` (`Boolean`), `error_message` (`Text`), `sent_at` (`DateTime`).

---

## 15. License

No license has been chosen yet. Add a `LICENSE` file before redistributing this project.
