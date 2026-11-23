from app.services import home
from tests.helpers import make_world


def test_skip_reasons_are_grouped_in_plain_words():
    assert home.skip_reason_bucket("requires German, which you do not work in") == "needs a language you do not work in"
    assert home.skip_reason_bucket("level is junior, yours is senior") == "not your level"
    assert home.skip_reason_bucket("you have no right to work in Germany and the ad does not offer visa sponsorship") == "country or visa does not fit"
    assert home.skip_reason_bucket("already handled") == "already applied or blocked"
    assert home.skip_reason_bucket("something odd") == "other reasons"


def test_home_answers_the_five_questions(client, llm):
    w = make_world(client, llm)
    body = client.get("/api/home", headers=w.headers).json()
    assert {"status", "needs_you", "week", "latest", "skipped", "try_it_first"} <= set(body)
    assert body["status"]["state"] in ("working", "paused", "cannot_work", "stopped")
    assert set(body["week"]) == {"applied", "replies", "interviews", "checked"}
    assert isinstance(body["needs_you"], list) and body["skipped"]["total"] >= 0
