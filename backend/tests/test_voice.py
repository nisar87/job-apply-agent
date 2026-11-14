"""Every email must read as written by the candidate: stock phrases and text pasted from the CV are caught,
rewritten once, and never sent automatically if they remain."""

from app.services.inbox import write_reply
from app.services.writing import voice_problems
from tests.test_phase2 import reply, sent_world, state

PROFILE = {"name": "Alex Example", "summary": "Backend engineer.",
           "roles": [{"title": "Engineer", "company": "Datawerk",
                      "achievements": ["Led a project to integrate OpenAI APIs, creating a method search engine for "
                                       "large codebases and enhancing developer productivity."]}]}


def test_stock_phrases_and_pasted_cv_text_are_caught():
    assert voice_problems("Hi Sarah,\n\nYes - I built an internal code search on top of OpenAI a while back.\n\n"
                          "Thanks,\nAlex", PROFILE) == []
    found = voice_problems("I hope this email finds you well. I am writing to apply. Furthermore, I'm passionate "
                           "about code.", PROFILE)
    assert any("hope this email finds you" in p for p in found) and any("i am writing to" in p for p in found)
    assert any("furthermore" in p for p in found) and any("passionate about" in p for p in found)
    assert voice_problems("I’m writing to ask.", PROFILE) == ["sounds automated: 'i'm writing to'"]  # curly apostrophe
    pasted = voice_problems("Yes. I led a project to integrate OpenAI APIs, creating a method search engine for "
                            "large codebases.", PROFILE)
    assert any(p.startswith("copies the CV word for word") for p in pasted)


def test_a_generated_sounding_reply_is_rewritten_once(llm):
    drafts = iter([
        {"body": "Hello,\n\nI hope this email finds you well. Furthermore, I have experience.\n\nBest,\nAlex",
         "fully_covered": True},
        {"body": "Hi,\n\nYes, I've built an AI code search tool at Datawerk.\n\nThanks,\nAlex", "fully_covered": True},
    ])
    prompts = []
    llm.on("write_reply", lambda p, s: (prompts.append(p), next(drafts))[1])
    out = write_reply(llm, incoming="Do you have AI experience?", thread=[], fact_sheet={}, profile=PROFILE,
                      user_name="Alex Example", language="en")
    assert out["voice_problems"] == [] and "built an AI code search" in out["body"]
    assert len(prompts) == 2 and "did not read like a person wrote it" in prompts[1]
    assert "first name only (Alex)" in prompts[0] and "contractions" in prompts[0]


def test_a_reply_that_still_sounds_generated_goes_to_the_user(client, llm):
    w = sent_world(client, llm)
    llm.on("write_reply", lambda p, s: {"body": "Hello,\n\nThank you for your time and consideration. My notice "
                                                "period is 1 month.\n\nBest,\nAlex", "fully_covered": True})
    reply(w, "Thanks. What is your notice period?")
    status, msgs, escs, app = state(w)
    assert app.agent_replies == 0 and escs == [("normal", "open")] and len(w.mail.sent) == 1   # nothing sent
    assert ("in", "routine_question", "none") in msgs
