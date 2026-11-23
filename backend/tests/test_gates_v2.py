"""Version 2 gates: required languages, role family, level, own country, right to work and sponsorship."""

from app.services import cvs
from app.services.matching import v2_gates

OWNER = {
    "home_country": "pk", "level": "senior", "role_families": ["backend", "fullstack"],
    "work_languages": ["en", "ur"], "right_to_work_countries": [], "exclude_home_country": True,
}


def gates(fields, country="de", mode="onsite", facts=OWNER):
    return v2_gates(fields, country, mode, facts)


def test_a_german_required_job_is_stopped_even_when_written_in_english():
    assert gates({"languages_required": ["de"], "visa_sponsorship": "yes"})
    assert not gates({"languages_required": ["en"], "visa_sponsorship": "yes"})
    assert not gates({"languages_required": [], "visa_sponsorship": "yes"})          # unknown passes


def test_role_family_and_level():
    assert gates({"role_family": "sales", "visa_sponsorship": "yes"})
    assert not gates({"role_family": "backend", "visa_sponsorship": "yes"})
    assert not gates({"role_family": "unknown", "visa_sponsorship": "yes"})
    assert gates({"seniority": "junior", "visa_sponsorship": "yes"})
    assert gates({"seniority": "executive", "visa_sponsorship": "yes"})
    assert not gates({"seniority": "mid", "visa_sponsorship": "yes"})
    assert not gates({"seniority": "lead", "visa_sponsorship": "yes"})
    assert not gates({"seniority": "unknown", "visa_sponsorship": "yes"})


def test_no_right_to_work_needs_sponsorship_unless_the_job_is_remote():
    assert gates({"visa_sponsorship": "unknown"})
    assert gates({"visa_sponsorship": "no"})
    assert not gates({"visa_sponsorship": "yes"})
    assert not gates({"visa_sponsorship": "no"}, mode="remote")
    assert not gates({}, country=None, mode="remote")
    assert gates({"visa_sponsorship": "yes"}, country=None)                  # unknown country, not remote


def test_a_right_to_work_removes_the_sponsorship_need():
    assert not gates({"visa_sponsorship": "unknown"}, facts={**OWNER, "right_to_work_countries": ["de"]})


    assert gates({"visa_sponsorship": "yes"}, country="pk", mode="remote")      # never your own country, remote or not
    assert gates({"visa_sponsorship": "yes"}, country="pk")
    assert gates({"visa_sponsorship": "yes"}, country="pk", mode="remote")
    assert not gates({"visa_sponsorship": "yes"}, country="pk", facts={**OWNER, "exclude_home_country": False})


def test_an_account_without_these_facts_behaves_as_before():
    assert gates({"languages_required": ["de"], "seniority": "junior", "visa_sponsorship": "no"}, facts={}) == []


def test_languages_are_read_from_the_cv_at_working_level_only():
    profile = {"languages": [{"name": "English", "level": "Fluent"}, {"name": "Urdu", "level": "Native"},
                             {"name": "German", "level": "A1"}, {"name": "Arabic"}]}
    assert cvs.work_language_codes(profile) == ["en", "ur", "ar"]


def test_free_movement_inside_the_eu_needs_no_sponsorship():
    facts = {**OWNER, "home_country": "de", "right_to_work_countries": ["de"], "exclude_home_country": False}
    assert not gates({"visa_sponsorship": "unknown"}, country="at", facts=facts)
    assert gates({"visa_sponsorship": "unknown"}, country="gb", facts=facts)


def test_the_same_job_under_another_link_is_not_applied_to_twice(client, llm):
    from app.models import Application
    from app.services import postings as posting_service
    from app.services.campaigns import get_config
    from tests.helpers import always_ok, campaign_of, make_world, raw

    w = make_world(client, llm)
    with w.session() as s:
        campaign = campaign_of(s, w)
        cfg = get_config(campaign)
        first = posting_service.ingest(s, llm, raw(title="Backend Engineer", company="Acme GmbH", location="Berlin"), mail_check=always_ok)[0]
        second = posting_service.ingest(s, llm, raw(title="Backend  Engineer", company="Acme GmbH", location="Munich", source="other"), mail_check=always_ok)[0]
        assert first.id != second.id and first.company_id == second.company_id
        s.add(Application(user_id=w.user_id, campaign_id=campaign.id, posting_id=first.id, company_id=first.company_id, status="sent", route="email", idempotency_key="test-dup-1"))
        s.flush()
        from app.services.matching import hard_filters
        ok, fails, _ = hard_filters(s, w.user_id, cfg, second, {})
        assert not ok and any("under another link" in f for f in fails)


def test_a_user_who_declines_sponsorship_only_applies_where_they_may_work():
    facts = {**OWNER, "right_to_work_countries": ["de"], "accept_sponsorship": False, "exclude_home_country": False}
    assert gates({"visa_sponsorship": "yes"}, country="gb", facts=facts)
    assert not gates({"visa_sponsorship": "unknown"}, country="de", facts=facts)
    assert not gates({"visa_sponsorship": "no"}, country="gb", mode="remote", facts=facts)


def test_europe_canada_and_the_us_can_be_chosen_as_a_search_area():
    from app.services.campaigns import CampaignConfig
    cfg = CampaignConfig(countries=["Europe", "Canada", "usa"], work_modes=["remote"])
    assert {"de", "fr", "gb", "pl", "ca", "us"} <= set(cfg.countries) and "pk" not in cfg.countries
