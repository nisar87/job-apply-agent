"""A rule-based stand-in for the model, for trying the whole application without an
API key (LLM_PROVIDER=fake). It is deliberately simple: it lets every screen and run
work end to end, and is no substitute for the real model's judgement."""

import ast
import re

from app.llm.fake import FakeLLM


def _block(prompt: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", prompt, re.S)
    return m.group(1) if m else ""


def demo_llm() -> FakeLLM:
    fake = FakeLLM()
    fake.model = "demo-rules"

    def cv_parse(prompt, schema):
        text = prompt.split("CV:\n", 1)[-1].strip()
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        skills = re.search(r"skills?\s*[:\-]\s*(.+)", text, re.I)
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        return {
            "name": lines[0] if lines else "", "email": email.group(0) if email else "",
            "summary": " ".join(lines[1:3])[:400],
            "skills": [{"name": s.strip()} for s in (skills.group(1).split(",") if skills else []) if s.strip()][:40],
            "roles": [{"title": line[:80], "company": "", "achievements": []} for line in lines if re.search(r"\b(19|20)\d\d\b", line)][:8],
            "links": re.findall(r"https?://\S+", text)[:5],
        }

    def posting_extract(prompt, schema):
        text = _block(prompt, "posting") or prompt
        low = text.lower()
        must = re.search(r"requirements?\s*[:\-]\s*(.+)", text, re.I)
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        salary = re.search(r"(?:salary|gehalt)\D{0,20}(\d[\d.,]{3,})", low)
        return {
            "work_mode": "remote" if "remote" in low else "hybrid" if "hybrid" in low else "unknown",
            "contract_type": "full_time" if re.search(r"full[- ]time|vollzeit", low) else "unknown", "seniority": "unknown",
            "language": "de" if re.search(r"\b(und|wir|bewerbung|aufgaben)\b", low) else "en",
            "requirements_must": [s.strip() for s in must.group(1).split(",")][:12] if must else [],
            "requirements_essential": [], "requirements_nice": [],
            "application_method": "email" if email else "form", "contact_email": email.group(0).rstrip(".") if email else "",
            "contact_name": "", "is_agency": "recruitment agency" in low, "visa_sponsorship": "unknown",
            "salary_min": float(re.sub(r"[.,]", "", salary.group(1))) if salary else 0,
            "salary_period": "year" if salary else "unknown",
        }

    def score_job(prompt, schema):
        profile = _block(prompt, "profile").lower()
        found = re.search(r"Must-have: (\[.*?\])\n", prompt)
        must = ast.literal_eval(found.group(1)) if found else []
        reqs = [{"requirement": r, "status": "met" if r.lower() in profile else "missing", "essential": False,
                 "evidence": f"The CV mentions {r}" if r.lower() in profile else ""} for r in must]
        pct = 100.0 * sum(r["status"] == "met" for r in reqs) / len(reqs) if reqs else 70.0
        dims = ("must_have_skills", "experience", "domain", "nice_to_have", "language", "preferences")
        return {"dimensions": dict.fromkeys(dims, pct), "requirements": reqs,
                "gaps": [r["requirement"] for r in reqs if r["status"] == "missing"], "summary": "Rule-based demo score"}

    def write_application(prompt, schema):
        company = re.search(r"Company: (.*)", prompt).group(1).strip()
        role = re.search(r"Role: (.*)", prompt).group(1).strip()
        name = re.search(r"email from (.*?)\.", prompt)
        name = name.group(1) if name else "the candidate"
        matches = re.findall(r"'requirement': '([^']+)'", _block(prompt, "strongest_matches"))[:3]
        strengths = ", ".join(matches) if matches else "the requirements you list"
        body = (
            f"Dear hiring team at {company},\n\nI am writing to apply for the {role} position. My background covers {strengths}, "
            "and my CV, attached, shows where I have used these in practice. I would welcome the chance to explain how this "
            "experience fits what you are looking for.\n\nI am glad to provide anything further you need and look forward to "
            f"hearing from you.\n\nKind regards,\n{name}\n\n(This text was produced by the built-in demo writer, not by Gemini.)"
        )
        return {"subject": f"Application: {role} - {name}", "body": body, "language": "en", "claims": []}

    def classify_reply(prompt, schema):
        email = prompt.split("<newest_email", 1)[-1].lower()
        for needle, category in [("do not contact", "do_not_contact"), ("received your application", "auto_ack"),
                                 ("out of office", "out_of_office"), ("other candidates", "rejection"), ("unfortunately", "rejection"),
                                 ("interview", "interview_invitation"), ("offer", "offer"), ("salary", "salary_question")]:
            if needle in email:
                return {"category": category, "confidence": 0.9, "summary": f"Mentions: {needle}"}
        return {"category": "unclear", "confidence": 0.9, "summary": "Needs your judgement"}

    def onboarding(prompt, schema):
        convo = prompt.split("Settings understood so far", 1)[0]
        said = " ".join(re.findall(r"User: (.*)", convo)).lower()
        config: dict = {}
        quota = re.search(r"(\d+)\b[^.\d]{0,40}?\b(?:a|per|each)\s+day|daily\s*(\d+)|(\d+)\s*(?:jobs|applications)\s+daily", said)
        if quota:
            config["daily_quota"] = int(next(g for g in quota.groups() if g))
        for word, code in [("austria", "at"), ("german", "de"), ("switzerland", "ch")]:
            if word in said:
                config.setdefault("countries", []).append(code)
        for mode in ("remote", "hybrid"):
            if mode in said:
                config.setdefault("work_modes", []).append(mode)
        days = re.search(r"(\d+)\s*days?\s*old|last\s*(\d+)\s*days|(\d+)\s*week", said)
        if days:
            n = next(g for g in days.groups() if g)
            config["max_posting_age_days"] = int(n) * (7 if "week" in days.group(0) else 1)
        elif "week" in said:
            config["max_posting_age_days"] = 7
        match = re.search(r"(\d{2,3})\s*%", said)
        if match:
            config["match_threshold"] = int(match.group(1))
        roles = re.findall(r"\b(backend|frontend|full[- ]stack|data|devops|python|java|engineer|developer|designer|manager|analyst)\b", said)
        if roles:
            config["keywords"] = sorted(set(roles))
        reply = ("Here is what I understood: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in config.items()) + "."
                 if config else "Tell me which roles, where, and how many applications a day.")
        return {"reply": reply + " (Demo mode: connect Gemini for a real conversation.)", "campaign_name": "My job search",
                "config": config, "preferences": [], "open_question": not config,
                # The demo reader works by pattern, so it vouches for its settings with the user's opening words.
                "evidence": [{"setting": k, "user_words": " ".join(said.split()[:2])} for k in config]}

    fake.on("cv_parse", cv_parse)
    fake.on("posting_extract", posting_extract)
    fake.on("score_job", score_job)
    fake.on("write_application", write_application)
    fake.on("fact_check", lambda p, s: {"claims": []})
    fake.on("classify_reply", classify_reply)
    fake.on("write_reply", lambda p, s: {"body": "Thank you for your message. I will reply shortly.", "fully_covered": False})
    fake.on("onboarding", onboarding)
    fake.on("form_answer", lambda p, s: {"answer": "", "fully_covered": False})
    fake.on("ping", lambda p, s: "pong")
    return fake
