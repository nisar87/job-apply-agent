"""Run the application's own writing chain against real Gemini, without a database.

    python ../scripts/try_gemini.py            (from the `backend` folder)
    python ../scripts/try_gemini.py my-cv.pdf  (use your own CV instead of the sample)

Steps: read a CV, read a job posting, score the fit, write the application email,
run the checks. It uses a sample CV and a sample posting unless you pass a CV file.
Nothing is sent and nothing is stored. Cost: about one cent.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import get_settings
from app.llm import get_llm
from app.models import CVVersion, Posting
from app.services import cvs as cv_service
from app.services import matching, writing
from app.services import postings as posting_service
from app.services.campaigns import CampaignConfig

SAMPLE_CV = """Maria Huber
Vienna, Austria | maria.huber@example.com

Summary
Backend developer with six years of experience building web services in Python.

Experience
Senior Backend Developer, Alpenpay GmbH, Vienna, 2022 to today
- Built the payments API in Python and FastAPI, serving 4 million requests a day
- Moved the reporting jobs from cron scripts to Celery and PostgreSQL, cutting run time from 3 hours to 20 minutes
- Mentored two junior developers

Backend Developer, Donau Logistics, Linz, 2019 to 2022
- Wrote REST services in Django for shipment tracking
- Introduced automated tests with pytest; coverage went from 30% to 85%

Education
BSc Computer Science, TU Wien, 2019

Skills
Python, FastAPI, Django, PostgreSQL, Redis, Celery, Docker, AWS, pytest

Languages
German (native), English (fluent)
"""

SAMPLE_COMPANY = "Bergblick Software GmbH"
SAMPLE_TITLE = "Senior Python Developer (m/f/d), remote in Austria"
SAMPLE_POSTING = """Bergblick Software GmbH builds booking software for alpine hotels.
We are looking for a Senior Python Developer to join our platform team. Reference: BB-2026-114.

Your tasks
- Design and build backend services in Python (FastAPI) on PostgreSQL
- Improve the reliability of our background jobs
- Review code and support less experienced colleagues

What you bring
- At least 5 years of professional Python experience (essential)
- Solid PostgreSQL knowledge
- Experience with background job systems such as Celery
- Fluent English; German is a plus
- Nice to have: Kubernetes, experience in the travel industry

We offer fully remote work within Austria and a gross annual salary from EUR 70,000.
Please send your application to Ms. Anna Leitner at jobs@bergblick-software.example.
"""


def main() -> int:
    s = get_settings()
    if s.llm_provider != "fake" and not s.gemini_api_key:
        print("GEMINI_API_KEY is empty. Put your key in the .env file in the project folder.")
        return 1
    print(f"Provider: {s.llm_provider}; models: fast={s.gemini_model_fast}, strong={s.gemini_model_strong}")
    llm = get_llm()
    config = CampaignConfig()

    cv_text, cv_name = SAMPLE_CV, "sample CV"
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        cv_text, cv_name = cv_service.extract_text(path.name, path.read_bytes()), path.name
    print(f"CV: {cv_name} ({len(cv_text)} characters)\n")

    def timed(name, fn):
        start = time.time()
        try:
            out = fn()
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {name}: {type(exc).__name__}: {str(exc)[:400]}")
            raise SystemExit(1) from None
        print(f"[ok]   {name} ({time.time() - start:.1f}s)")
        return out

    profile = timed("read the CV", lambda: cv_service.parse_profile(llm, cv_text))
    print("       name:", profile.get("name"), "| skills:", ", ".join((profile.get("skills") or [])[:10]))

    fields = timed("read the posting", lambda: posting_service.extract_fields(llm, SAMPLE_TITLE, SAMPLE_COMPANY, SAMPLE_POSTING))
    print("       " + json.dumps(fields, ensure_ascii=False)[:600])

    posting = Posting(title=SAMPLE_TITLE, description=SAMPLE_POSTING, fields=fields, source="sample", source_id="1", url="")
    version = CVVersion(profile=profile, extracted_text=cv_text)

    rubric = timed("score the fit", lambda: matching.score_cv(llm, posting, version, ["Remote work in Austria"]))
    total = matching.totals(rubric, config.weights)
    print(f"       total {total} (threshold {config.match_threshold}); dimensions: {rubric.get('dimensions')}")
    for r in (rubric.get("requirements") or [])[:8]:
        print(f"       - {r.get('status', '?'):8} {str(r.get('requirement', ''))[:70]} | {str(r.get('evidence', ''))[:70]}")

    draft, checks = timed("write and check the email", lambda: writing.write_checked(
        llm, posting=posting, company_name=SAMPLE_COMPANY, version=version, rubric=rubric, fact_sheet={},
        style_guide="", sample_emails=[], user_name=profile.get("name") or "Maria Huber", config=config,
        previous_bodies=[],
    ))
    print("\nSubject:", draft.get("subject"))
    print("-" * 60)
    print(draft.get("body"))
    print("-" * 60)
    print(f"Words: {len((draft.get('body') or '').split())}; checks passed: {checks['passed']}"
          + ("; rewritten once" if checks.get("rewritten") else ""))
    for p in checks["problems"]:
        print("  problem:", p)
    return 0 if checks["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
