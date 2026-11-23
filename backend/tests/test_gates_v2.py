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


def test_the_users_own_country_can_be_excluded():
    assert gates({"visa_sponsorship": "yes"}, country="pk")
    assert not gates({"visa_sponsorship": "yes"}, country="pk", mode="remote")
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
