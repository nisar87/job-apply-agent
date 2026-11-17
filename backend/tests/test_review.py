"""The hiring-manager review every draft passes before it is sent without the user seeing it."""

from app.llm.fake import FakeLLM
from app.services import writing


class P:
    title, description = "Backend Engineer", "We build payment systems in Go."


def test_the_reviewer_stops_an_unprofessional_or_wrong_email():
    llm = FakeLLM()
    llm.on("review_application", lambda prompt, _: {"verdict": "send", "issues": []})
    assert writing.review(llm, "Backend Engineer", "Dear team, ...", posting=P, company_name="Acme") == []
    llm.on("review_application", lambda prompt, _: {"verdict": "fix", "issues": ["names the wrong company"]})
    assert writing.review(llm, "s", "b", posting=P, company_name="Acme") == ["names the wrong company"]
    llm.on("review_application", lambda prompt, _: {"verdict": "fix", "issues": []})
    assert writing.review(llm, "s", "b", posting=P, company_name="Acme") == ["the reviewer would not send it as it is"]
    prompt = llm.calls[-1]["prompt"]
    assert "hiring manager" in prompt and "Company: Acme" in prompt and "Role: Backend Engineer" in prompt
