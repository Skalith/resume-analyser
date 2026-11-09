"""Frontend server — DEPRECATED.

The dashboard is now served directly by the FastAPI backend.
This file is kept only to avoid breaking any existing scripts that
reference it.  Running it will print the correct startup command
and exit.

To start the full application:

    cd backend
    python -m venv venv            # create venv (first time only)
    source venv/bin/activate       # Windows: venv\\Scripts\\activate
    pip install -r requirements.txt
    cp .env.example .env           # fill in real values
    uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

Then open http://127.0.0.1:8000 in your browser.
The dashboard (frontend/index.html) is served automatically by FastAPI.
"""
import sys

NOTICE = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Resume Job Matcher — dashboard server has moved
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  The dashboard is now served by the FastAPI backend.
  Start it with:

      cd backend
      source venv/bin/activate
      uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

  Then open http://127.0.0.1:8000

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

if __name__ == "__main__":
    print(NOTICE)
    sys.exit(0)
