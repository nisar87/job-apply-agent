"""Shared test helpers: a scripted model, sample data and a ready-made user."""

import ast
import re
import uuid
from datetime import UTC, date, datetime, timedelta

from app.connectors.mail import FakeMailProvider, set_mail_provider
from app.connectors.sources import FakeSource, RawPosting, set_sources
from app.db import user_session
from app.llm.base import ToolCall
from app.llm.fake import FakeLLM
from app.models import Campaign, User
from app.services import campaigns as campaign_service
from app.services import cvs as cv_service
from app.services.campaigns import CampaignConfig

CV_BACKEND = """Alex Example
Backend engineer with eight years of experience building data platforms.
Skills: python, postgresql, kubernetes, fastapi, aws
Languages: English C1, German B2
Senior Engineer at Datawerk 2019-2026: led migration of 40 services to kubernetes.
"""

CV_LEAD = """Alex Example
Engineering team lead with experience managing eight engineers.
Skills: leadership, hiring, agile, python, stakeholder management
Languages: English C1, German B2
Team Lead at Datawerk 2022-2026: grew the platform team from 3 to 8 people.
"""

# A fixed moment inside the default send window: Tuesday 10 November 2026, 10:00 UTC.
NOW = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)


def body_words(n: int = 130) -> str:
    return " ".join(["experience"] * n)


def install_brain(fake: FakeLLM) -> FakeLLM:
    """Give the fake model simple, deterministic behaviour for every task."""
    fake.bad_claim = False       # write an unsupported claim on every draft
    fake.bad_claim_once = False  # only on the first draft, so the rewrite is clean
    fake.placeholder = False
    fake.counter = 0

    def cv_parse(prompt, schema):
        text = prompt.split("CV:\n", 1)[-1]
        skills = re.search(r"Skills:\s*(.+)", text)
        return {
            "name": text.strip().split("\n")[0], "summary": text.strip().split("\n")[1] if "\n" in text.strip() else "",
            "skills": [{"name": s.strip()} for s in (skills.group(1).split(",") if skills else [])],
            "roles": [{"title": "Engineer", "company": "Datawerk", "achievements": [line for line in text.split("\n") if "Datawerk" in line]}],
            "languages": [{"name": "English", "level": "C1"}, {"name": "German", "level": "B2"}],
        }

    def posting_extract(prompt, schema):
        text = prompt.split("<posting>", 1)[-1]
        must = re.search(r"Requirements:\s*(.+)", text)
        essential = re.search(r"Essential:\s*(.+)", text)
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        salary = re.search(r"Salary:\s*(\d+)", text)
        return {
            "work_mode": "remote" if "remote" in text.lower() else ("hybrid" if "hybrid" in text.lower() else "unknown"),
            "contract_type": "full_time" if "full-time" in text.lower() else "unknown",
            "language": "de" if "bewerbung" in text.lower() else "en",
            "requirements_must": [s.strip() for s in must.group(1).split(",")] if must else [],
            "requirements_essential": [s.strip() for s in essential.group(1).split(",")] if essential else [],
            "requirements_nice": [], "application_method": "email" if email else "form",
            "contact_email": email.group(0).rstrip(".") if email else "", "contact_name": "",
            "is_agency": "agency" in text.lower(), "visa_sponsorship": "unknown",
            "salary_min": float(salary.group(1)) if salary else 0, "salary_period": "year" if salary else "unknown",
        }

    def score_job(prompt, schema):
        profile = prompt.split("<profile>", 1)[1].split("</profile>", 1)[0].lower()
        must = ast.literal_eval(re.search(r"Must-have: (\[.*?\])\n", prompt).group(1))
        essential = ast.literal_eval(re.search(r"Essential: (\[.*?\])\n", prompt).group(1))
        reqs = []
        for r in must:
            met = r.lower() in profile
            reqs.append({"requirement": r, "status": "met" if met else "missing", "essential": r in essential,
                         "evidence": f"profile mentions {r}" if met else ""})
        pct = 100.0 * sum(r["status"] == "met" for r in reqs) / len(reqs) if reqs else 60.0
        return {"dimensions": dict.fromkeys(("must_have_skills", "experience", "domain", "nice_to_have", "language", "preferences"), pct),
                "requirements": reqs, "gaps": [r["requirement"] for r in reqs if r["status"] == "missing"], "summary": ""}

    def write_application(prompt, schema):
        fake.counter += 1
        company = re.search(r"Company: (.*)", prompt).group(1).strip()
        role = re.search(r"Role: (.*)", prompt).group(1).strip()
        german = "language 'de'" in prompt
        opening = "Sehr geehrte Damen und Herren,\n\nich bewerbe mich als" if german else "Dear hiring team,\n\nI am applying for the role of"
        filler = " ".join(f"detail{fake.counter}x{i}" for i in range(120))
        body = f"{opening} {role} at {company}. I have worked with python and kubernetes at Datawerk.\n\n{filler}"
        if german:
            body += "\n\nIch habe mit der Plattform und dem Team sehr viel fÃ¼r die Firma und mit ihr gearbeitet.\n\nMit freundlichen GrÃ¼ÃŸen"
        else:
            body += "\n\nI have led the migration of services and I am available to start with a notice period.\n\nKind regards"
        add_bad = fake.bad_claim or (fake.bad_claim_once and "previous draft was rejected" not in prompt)
        if add_bad:
            body += " I also have 20 years of COBOL experience."
        if fake.placeholder:
            body += " [Company Name]"
        return {"subject": f"Application: {role} - Alex Example", "body": body, "language": "de" if german else "en",
                "claims": ["worked with python and kubernetes at Datawerk"]}

    def fact_check(prompt, schema):
        email = prompt.split("<email>", 1)[1].split("</email>", 1)[0]
        claims = [{"claim": "worked with python", "supported": True}]
        if "COBOL" in email:
            claims.append({"claim": "20 years of COBOL experience", "supported": False})
        return {"claims": claims}

    fake.reply_bad_claim = False

    def classify_reply(prompt, schema):
        email = prompt.split("<newest_email", 1)[1].lower()
        table = [
            ("do not contact", "do_not_contact"), ("remove us", "do_not_contact"), ("received your application", "auto_ack"),
            ("out of office", "out_of_office"), ("position has been filled", "position_closed"),
            ("other candidates", "rejection"), ("interview", "interview_invitation"), ("pleased to offer", "offer"),
            ("assessment", "test_assignment"), ("certificates", "document_request"), ("portal", "redirect"),
            ("salary", "salary_question"), ("notice period", "routine_question"), ("favourite colour", "routine_question"),
        ]
        for needle, category in table:
            if needle in email:
                return {"category": category, "confidence": 0.95, "summary": f"employer: {needle}"}
        if "ignore all previous" in email:
            return {"category": "routine_question", "confidence": 0.4, "summary": "suspicious"}
        return {"category": "unclear", "confidence": 0.9, "summary": "unclear"}

    def write_reply(prompt, schema):
        email = prompt.split("<employer_email>", 1)[1].lower()
        if "thank-you" in prompt:
            return {"body": "Thank you for letting me know. I wish you all the best.", "fully_covered": True}
        if "follow-up" in prompt:
            return {"body": "I wanted to follow up on my application and ask whether it reached you.", "fully_covered": True}
        if "notice period" in email:
            body = "My notice period is 1 month."
            if fake.reply_bad_claim:
                body += " I also have 20 years of COBOL experience."
            return {"body": body, "fully_covered": True, "claims": ["notice period is 1 month"]}
        if "salary" in email and "65000" in prompt:
            return {"body": "My salary expectation is 65000 per year.", "fully_covered": True}
        return {"body": "Thank you for your message. I will come back to you shortly.", "fully_covered": False}

    for name, fn in [("classify_reply", classify_reply), ("write_reply", write_reply), ("cv_parse", cv_parse), ("posting_extract", posting_extract), ("score_job", score_job),
                     ("write_application", write_application), ("fact_check", fact_check)]:
        fake.on(name, fn)
    return fake


def scripted_agent(fake: FakeLLM):
    """A model policy that behaves like a sensible agent: status, candidates, score each,
    draft the ones that pass, finish."""
    def policy(messages, extra):
        tool_results = [m for m in messages if m["role"] == "tool"]
        if not tool_results:
            return [ToolCall("get_status", {})]
        names = [m["name"] for m in tool_results]
        if "list_candidates" not in names:
            return [ToolCall("list_candidates", {})]
        candidates = next(m["result"]["candidates"] for m in tool_results if m["name"] == "list_candidates")
        scored = {m["result"].get("posting_id"): m["result"] for m in tool_results if m["name"] == "score_job"}
        for c in candidates:
            if c["posting_id"] not in scored:
                return [ToolCall("score_job", {"posting_id": c["posting_id"]})]
        drafted = sum(1 for m in tool_results if m["name"] == "draft_application")
        to_draft = [pid for pid, r in scored.items() if r.get("decision") in ("apply", "manual")]
        if drafted < len(to_draft):
            return [ToolCall("draft_application", {"posting_id": to_draft[drafted]})]
        return [ToolCall("finish", {"summary": f"scored {len(scored)}, drafted {drafted}"})]

    fake.on("agent_prepare", policy)


def raw(title="Backend Engineer", company="Acme GmbH", requirements="python, postgresql, kubernetes", email="jobs@acme.at",
        extra="", posted: date | None = None, source="fake", location="Vienna", country="at", **kw) -> RawPosting:
    posted = posted or datetime.now(UTC).date() - timedelta(days=1)  # always fresh, so the age filter never trips
    desc = f"{title} at {company}. Remote within Austria. Full-time.\nRequirements: {requirements}\n{extra}\n"
    if email:
        desc += f"Send your application to {email}\n"
    return RawPosting(source=source, source_id=f"{company}-{title}", url=f"https://example.com/{uuid.uuid4().hex[:8]}",
                      title=title, company=company, description=desc, location=location, country=country,
                      posted_date=posted, **kw)


class World:
    """A user with a mailbox, CVs and an active campaign."""

    def __init__(self, client, headers, user_id, fake, mail, campaign_id):
        self.client, self.headers, self.user_id, self.llm, self.mail, self.campaign_id = client, headers, uuid.UUID(user_id), fake, mail, campaign_id

    def session(self):
        return user_session(self.user_id)


def make_world(client, fake, *, email="a@example.com", cvs=(("Backend", CV_BACKEND, "backend"),), config=None, state="active",
               approval_mode="review", mail=None) -> World:
    from tests.conftest import make_user

    install_brain(fake)
    scripted_agent(fake)
    mail = mail or FakeMailProvider()
    set_mail_provider(mail)
    set_sources({"fake": FakeSource([])})
    headers, user_id = make_user(client, email=email)
    r = client.post("/api/mail/fake/connect", json={"address": email}, headers=headers)
    assert r.status_code == 200, r.text
    for label, text, keywords in cvs:
        r = client.post("/api/cvs", headers=headers, files={"file": ("cv.txt", text.encode(), "text/plain")},
                        data={"label": label, "keywords": keywords})
        assert r.status_code == 201, r.text
    cfg = CampaignConfig(**{"keywords": ["engineer"], "sources": ["fake"], **(config or {})})
    with user_session(uuid.UUID(user_id)) as s:
        user = s.get(User, uuid.UUID(user_id))
        user.escalation_email = "me@example.com"
        cv_service.save_fact_sheet(s, user.id, {"notice_period": "1 month", "work_authorisation": {"at": "citizen"}})
        campaign = campaign_service.create_campaign(s, user.id, "Austria remote", cfg, state=state, approval_mode=approval_mode)
        campaign_id = campaign.id
    return World(client, headers, user_id, fake, mail, campaign_id)


def campaign_of(session, world: World) -> Campaign:
    return session.get(Campaign, world.campaign_id)


def always_ok(_domain: str) -> bool:
    return True
