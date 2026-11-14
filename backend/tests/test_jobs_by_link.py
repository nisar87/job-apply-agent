"""A job added by link has no company field of its own: the name comes from the posting text, and a draft
for a posting whose company stays unknown is not failed for not naming it."""

from sqlalchemy import select

from app.connectors.sources import RawPosting
from app.models import Company, Posting
from app.services import postings as posting_service
from app.services.campaigns import CampaignConfig
from app.services.postings import UNKNOWN_COMPANY, _company_in_text
from app.services.writing import run_checks
from tests.helpers import always_ok, make_world

PAGE = ("Senior .NET Backend Developer (Remote)\nCompany: Practice Employer GmbH\nRequirements: C#, SQL Server\n"
        "Send your CV to careers@practice-employer.example\n")


def test_company_name_must_be_in_the_text():
    assert _company_in_text("Practice Employer GmbH", PAGE) == "Practice Employer GmbH"
    assert _company_in_text("Invented Corp", PAGE) is None          # the model cannot make one up
    assert _company_in_text("Unknown", PAGE) is None and _company_in_text("", PAGE) is None


def test_a_job_added_by_link_gets_its_company_from_the_text(client, llm):
    w = make_world(client, llm)
    base = llm.handlers["posting_extract"]
    llm.on("posting_extract", lambda p, s: {**base(p, s), "company": "Practice Employer GmbH"})
    raw = RawPosting(source="user_link", source_id="u1", url="https://example.com/job", title="Senior .NET Backend Developer",
                     company="", description=PAGE)
    with w.session() as s:
        posting, created = posting_service.ingest(s, llm, raw, mail_check=always_ok)
        assert created and s.get(Company, posting.company_id).name == "Practice Employer GmbH"
    llm.on("posting_extract", lambda p, s: {**base(p, s), "company": "Somebody Else AG"})
    raw2 = RawPosting(source="user_link", source_id="u2", url="https://example.com/job2", title="Other role",
                      company="", description=PAGE.replace("Senior", "Junior"))
    with w.session() as s:
        posting, _ = posting_service.ingest(s, llm, raw2, mail_check=always_ok)
        assert s.get(Company, posting.company_id).name == UNKNOWN_COMPANY
        assert len(s.execute(select(Company).where(Company.name == "Somebody Else AG")).all()) == 0


def test_an_unknown_company_does_not_fail_the_draft(llm):
    llm.on("fact_check", lambda p, s: {"claims": []})
    words = " ".join(["I have built data platforms with C# and SQL Server for years."] * 12)
    body = f"Dear hiring team,\n\n{words}\n\nKind regards,\nAlex"
    kw = dict(posting=Posting(title="Engineer", description="", fields={"language": "en"}), profile={}, fact_sheet={},
              expected_language="en", previous_bodies=[], config=CampaignConfig())
    unknown = run_checks(llm, {"subject": "Engineer - Alex", "body": body}, company_name=UNKNOWN_COMPANY, **kw)
    assert not any("name the company" in p for p in unknown["problems"])
    named = run_checks(llm, {"subject": "Engineer - Alex", "body": body}, company_name="Acme Systems", **kw)
    assert any("name the company" in p for p in named["problems"])
