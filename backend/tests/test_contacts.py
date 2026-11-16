"""Finding a published application address for jobs that show only a web form."""

from app.services import contacts, matching
from app.services import postings as posting_service
from app.services.campaigns import get_config
from tests.helpers import always_ok, campaign_of, make_world, raw


def site(pages: dict[str, str]):
    visited = []

    def fetch(url):
        visited.append(url)
        if url not in pages:
            raise RuntimeError("404")
        return pages[url]
    return fetch, visited


def test_addresses_are_read_from_links_and_disguises_and_ranked():
    markup = ('<a href="mailto:Bewerbung@Acme.de">Schreib uns</a> info (at) acme [dot] de '
              'datenschutz@acme.de <img src="logo@2x.png"> karriere&#64;acme.de')
    assert contacts.emails_in(markup) == ["bewerbung@acme.de", "info@acme.de", "datenschutz@acme.de", "karriere@acme.de"]
    assert [contacts.rank(e) for e in ("bewerbung@acme.de", "jobs@acme.de", "hr.team@acme.de", "info@acme.de",
                                       "max.mustermann@acme.de", "datenschutz@acme.de", "noreply@acme.de")] == [2, 2, 2, 1, 0, -1, -1]


def test_the_employers_own_site_is_searched_and_only_its_own_published_address_is_used():
    fetch, visited = site({
        "https://www.acme-software.de/karriere": "Karriere bei der Acme Software GmbH. Fragen: support@acme-software.de",
        "https://www.acme-software.de/kontakt": "Kontakt: info@acme-software.de, Webdesign: hello@agentur.example",
        "https://www.acme-software.de/impressum": "Acme Software GmbH, E-Mail: bewerbung@acme-software.de",
    })
    found = contacts.find("Acme Software GmbH", "https://www.stepstone.de/job/123",
                          "Mehr auf https://www.acme-software.de/karriere", fetch=fetch, mail_check=always_ok)
    assert found == ("bewerbung@acme-software.de", "https://www.acme-software.de/impressum")
    assert not any("stepstone" in u for u in visited)          # a job board says nothing about the employer

    # A general address is used when no application address is published.
    fetch, _ = site({"https://www.beta.de/impressum": "Beta GmbH · info@beta.de"})
    assert contacts.find("Beta GmbH", "https://www.beta.de/jobs/7", "", fetch=fetch, mail_check=always_ok) == \
        ("info@beta.de", "https://www.beta.de/impressum")

    # A guessed domain counts only when its pages name the company; nothing is ever guessed as an address.
    fetch, _ = site({"https://www.gamma.de/impressum": "Some other business · jobs@gamma.de"})
    assert contacts.find("Gamma GmbH", "https://www.stepstone.de/job/9", "", fetch=fetch, mail_check=always_ok) is None
    fetch, _ = site({"https://www.delta.de/impressum": "Delta GmbH, Musterstraße 1"})
    assert contacts.find("Delta GmbH", "https://www.stepstone.de/job/9", "", fetch=fetch, mail_check=always_ok) is None


def test_a_good_web_form_job_becomes_an_email_job_when_the_employer_publishes_an_address(client, llm):
    w = make_world(client, llm)
    fetch, visited = site({"https://www.formy.de/karriere": "Formy GmbH sucht dich! Bewerbung an jobs@formy.de"})
    contacts.set_fetcher(fetch)
    try:
        with w.session() as s:
            campaign = campaign_of(s, w)
            posting = posting_service.ingest(s, llm, raw(title="Platform Engineer", company="Formy GmbH", email="",
                                                         requirements="python"), mail_check=always_ok)[0]
            assert posting.apply_route == "form"
            match = matching.evaluate(s, llm, campaign, get_config(campaign), posting, {}, [])
            assert match.decision == "apply"
            assert (posting.contact_email, posting.apply_route) == ("jobs@formy.de", "email")
            assert posting.fields["contact_source"] == "https://www.formy.de/karriere"

            # No address published: it stays an on-site job, and the site is not searched again.
            other = posting_service.ingest(s, llm, raw(title="Data Engineer", company="Quiet AG", email="",
                                                       requirements="python"), mail_check=always_ok)[0]
            assert matching.evaluate(s, llm, campaign, get_config(campaign), other, {}, []).decision == "manual"
            asked = len(visited)
            assert contacts.try_find(s, other) is False and len(visited) == asked
            assert other.fields["contact_lookup"] == "none"
    finally:
        contacts.set_fetcher(None)
