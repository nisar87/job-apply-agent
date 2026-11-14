"""Choosing the countries a search applies within."""

import pytest

from app.config import get_settings
from app.connectors.sources import FakeSource, set_sources
from app.countries import catalogue, normalize, sources_for
from app.services import discovery, matching
from app.services import postings as posting_service
from app.services.campaigns import CampaignConfig, get_config
from tests.helpers import always_ok, campaign_of, make_world, raw


def test_country_names_codes_and_aliases():
    assert normalize("DE") == "de" and normalize("Germany") == "de" and normalize(" united  kingdom ") == "gb"
    assert normalize("UK") == "gb" and normalize("UAE") == "ae" and normalize("Narnia") is None
    assert CampaignConfig(countries=["Germany", "AT", "de", "uk"]).countries == ["de", "at", "gb"]
    with pytest.raises(ValueError, match="Unknown country: Narnia"):
        CampaignConfig(countries=["Narnia"])
    assert CampaignConfig(countries=[]).countries == []


def test_catalogue_names_the_sources_for_each_country(client, user, monkeypatch):
    headers, _ = user
    names = [c["name"] for c in catalogue()]
    assert names == sorted(names) and "Pakistan" in names
    assert sources_for("de") == ["arbeitnow"] and sources_for("pk") == []
    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")
    get_settings.cache_clear()
    assert sources_for("de") == ["adzuna", "arbeitnow"] and sources_for("us") == ["adzuna"]
    out = client.get("/api/countries", headers=headers).json()
    us = next(c for c in out if c["code"] == "us")
    assert us == {"code": "us", "name": "United States", "sources": ["adzuna"], "adzuna_possible": True}
    assert client.get("/api/countries").status_code == 401


def _filter(s, w, config, **kw):
    posting = posting_service.ingest(s, w.llm, raw(**kw), mail_check=always_ok)[0]
    return matching.hard_filters(s, w.user_id, config, posting, {})


def test_jobs_outside_the_chosen_countries_are_skipped(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        cfg = CampaignConfig(keywords=["engineer"], countries=["de", "nl"])
        ok, fails, _ = _filter(s, w, cfg, title="Berlin Engineer", company="A", country="de", work_mode="onsite")
        assert ok
        ok, fails, _ = _filter(s, w, cfg, title="Paris Engineer", company="B", country="fr", work_mode="onsite")
        assert not ok and "country France is not in this search" in fails
        # Remote jobs based elsewhere pass by default, and are skipped when the user wants only their countries.
        ok, _, _ = _filter(s, w, cfg, title="Remote Engineer", company="C", country="gb", work_mode="remote")
        assert ok
        strict = cfg.model_copy(update={"remote_outside_countries": False})
        ok, fails, _ = _filter(s, w, strict, title="Remote Engineer 2", company="D", country="gb", work_mode="remote")
        assert not ok and "remote job based in United Kingdom" in fails[0]
        # No country chosen means anywhere; a posting without a country passes with a note.
        ok, _, _ = _filter(s, w, CampaignConfig(keywords=["engineer"], countries=[]), title="Lima Engineer",
                           company="E", country="pe", work_mode="onsite")
        assert ok
        ok, _, notes = _filter(s, w, cfg, title="Somewhere Engineer", company="F", country=None, work_mode="onsite")
        assert ok and "country not stated" in notes


def test_adzuna_is_asked_once_per_chosen_country_it_covers(client, llm):
    w = make_world(client, llm)
    asked: dict[str, list[str]] = {"adzuna": [], "board": []}

    class Recorder(FakeSource):
        def search(self, query):
            asked[self.name].append(query.country)
            return []

    adzuna, board = Recorder(), Recorder()
    adzuna.name, board.name = "adzuna", "board"
    set_sources({"adzuna": adzuna, "board": board})
    with w.session() as s:
        campaign = campaign_of(s, w)
        campaign.config = {**campaign.config, "countries": ["de", "pk", "us"], "sources": ["adzuna", "board"]}
        discovery.discover(s, llm, campaign, mail_check=always_ok)
        assert asked == {"adzuna": ["de", "us"], "board": ["de"]}   # Pakistan is not an Adzuna country
        asked["adzuna"].clear(), asked["board"].clear()
        campaign.config = {**campaign.config, "countries": []}
        assert get_config(campaign).countries == []
        discovery.discover(s, llm, campaign, mail_check=always_ok)
        assert asked == {"adzuna": [], "board": [""]}   # anywhere: whole boards, no per-country search


def test_a_search_saved_before_the_setting_shows_its_real_value(client, llm):
    w = make_world(client, llm)
    with w.session() as s:
        campaign = campaign_of(s, w)
        campaign.config = {k: v for k, v in campaign.config.items() if k != "remote_outside_countries"}
    shown = client.get("/api/campaigns", headers=w.headers).json()[0]["config"]
    assert shown["remote_outside_countries"] is True


def test_setup_chat_and_api_accept_country_names(client, llm):
    w = make_world(client, llm)
    llm.on("onboarding", lambda p, s: {
        "reply": "Got it.", "config": {"keywords": ["backend"], "countries": ["Germany", "uk"],
                                       "remote_outside_countries": False},
        "evidence": [{"setting": "keywords", "user_words": "backend"}, {"setting": "countries", "user_words": "Germany or UK"},
                     {"setting": "remote_outside_countries", "user_words": "only in Germany or UK"}],
        "open_question": False})
    out = client.post("/api/setup/turn", headers=w.headers,
                      json={"history": [{"role": "user", "text": "backend jobs only in Germany or UK"}]}).json()
    assert out["config"]["countries"] == ["de", "gb"] and out["config"]["remote_outside_countries"] is False
    assert "countries: Germany, United Kingdom" in out["reply"]
    r = client.patch(f"/api/campaigns/{w.campaign_id}", headers=w.headers,
                     json={"config": {**out["config"], "countries": ["Atlantis"]}})
    assert r.status_code == 422 and "Unknown country: Atlantis" in r.json()["detail"]
