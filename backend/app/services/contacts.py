"""Finding where to email an application when the posting itself shows only a web form.

Only addresses a company publishes itself are used: on its careers page, its contact page or its legal notice
(in Germany and Austria every company site must have an Impressum with an email address). Addresses are never
guessed: a guessed address bounces, and bounces hurt the sending mailbox. An address must belong to the
company's own domain, and an application address (bewerbung@, karriere@, jobs@, hr@ …) is preferred to a
general one (info@, kontakt@); support, sales, privacy and no-reply addresses are never used.

The company's site comes from the posting's own link when it is not a job board, from links in the posting
text, or by trying the company's name as a domain; a site is accepted only when its pages name the company.
Runs only for jobs that already passed scoring, so each search looks up a handful of companies a day, and the
answer (found or not) is remembered on the posting.
"""

import html
import logging
import re
from urllib.parse import urlparse

from app.services.postings import EMAIL_RE, domain_accepts_mail, normalize_company, registered_domain

log = logging.getLogger(__name__)

PAGES = ["/karriere", "/careers", "/jobs", "/kontakt", "/contact", "/impressum", "/imprint", "/"]
MAX_PAGES = 8
HR_WORDS = ("bewerbung", "karriere", "career", "jobs", "job", "recruit", "talent", "hr", "personal", "people",
            "apply", "hiring", "bewerber", "stellen")
GENERAL_WORDS = ("info", "kontakt", "contact", "office", "hello", "mail", "post", "zentrale", "team")
NEVER = ("noreply", "no-reply", "donotreply", "datenschutz", "privacy", "dsb", "gdpr", "dpo", "press", "presse",
         "sales", "vertrieb", "support", "service", "billing", "rechnung", "invoice", "buchhaltung", "accounting",
         "webmaster", "abuse", "postmaster", "newsletter", "marketing", "shop", "order", "bestellung")
# Job boards, hiring systems and search sites: a link there says nothing about the employer's own domain.
NOT_EMPLOYER = (
    "stepstone", "indeed", "linkedin", "xing", "glassdoor", "monster", "adzuna", "arbeitsagentur", "himalayas",
    "remotive", "jobicy", "weworkremotely", "remoteok", "workingnomads", "arbeitnow", "greenhouse.io", "lever.co",
    "ashbyhq", "workable", "smartrecruiters", "recruitee", "personio", "teamtailor", "join.com", "wearedevelopers",
    "germantechjobs", "rozee", "persy.jobs", "yourfirm", "jobexport", "helixjobs", "softgarden", "onlyfy",
    "jobvite", "myworkdayjobs", "workday", "successfactors", "icims", "bamboohr", "breezy", "google", "kununu",
    "instaffo", "jobware", "stellenanzeigen", "kimeta", "jooble", "careerjet", "efinancialcareers", "dice.com",
    "wellfound", "angel.co", "ycombinator", "commoncrawl", "youtube", "facebook", "instagram", "twitter", "x.com", "github.com", "wikipedia",
)

_fetch = None  # set_fetcher() in tests; None means the real, polite Fetcher (off in tests and demo mode)


def set_fetcher(fetch) -> None:
    """fetch(url) -> page markup. Tests pass a fake; None restores the default."""
    global _fetch
    _fetch = fetch


def _fetcher():
    if _fetch is not None:
        return _fetch
    from app.config import get_settings

    if get_settings().llm_provider == "fake":  # tests and demo: never reach the internet
        return None
    from app.connectors.fetcher import Fetcher

    return Fetcher().fetch_html


def _host(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").netloc.lower().split(":")[0]


def is_employer_site(host: str) -> bool:
    return bool(host) and not any(n in host for n in NOT_EMPLOYER)


def emails_in(markup: str) -> list[str]:
    """Addresses in a page, including mailto: links and the usual '(at)' / '[dot]' disguises."""
    text = html.unescape(markup or "")
    text = re.sub(r"\s*[\[(\{]\s*(?:at|@)\s*[\])\}]\s*", "@", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[\[(\{]\s*(?:dot|punkt)\s*[\])\}]\s*", ".", text, flags=re.IGNORECASE)
    found = []
    for e in EMAIL_RE.findall(text):
        e = e.strip(".").lower()
        if e not in found and not re.search(r"\.(png|jpe?g|gif|svg|webp)$", e):
            found.append(e)
    return found


def rank(email: str) -> int:
    """2 for an application address, 1 for a general one, 0 for anything else, -1 for one never to use."""
    local = email.split("@", 1)[0]
    if any(w in local for w in NEVER):
        return -1
    if any(re.search(rf"(^|[._\-]){w}($|[._\-\d])", local) or local.startswith(w) for w in HR_WORDS):
        return 2
    if any(local.startswith(w) for w in GENERAL_WORDS):
        return 1
    return 0


def candidate_sites(company: str, url: str, description: str) -> list[str]:
    """Hosts that may be the employer's own site, most likely first."""
    out = []
    host = _host(url) if url else ""
    if is_employer_site(host):
        out.append(host)
    for link in re.findall(r"https?://[^\s<>\"')\]]+", description or ""):
        h = _host(link)
        if is_employer_site(h) and h not in out:
            out.append(h)
    name = normalize_company(company).replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    words = [re.sub(r"[^a-z0-9]", "", w) for w in name.split()]
    slugs = []
    for slug in ("".join(words), words[0] if words else "", "-".join(words[:2])):
        if len(slug) >= 3 and slug not in slugs:
            slugs.append(slug)
    for slug in slugs:  # "PRODATA Datenbanken und Informationssysteme" is prodata.de, not the whole name
        for guess in (f"www.{slug}.de", f"www.{slug}.com"):
            if guess not in out:
                out.append(guess)
    return out[:6]


def _names_company(page: str, company: str) -> bool:
    words = [w for w in normalize_company(company).split() if len(w) >= 3]
    # The name must stand in the text itself, not only inside an address or a link (jobs@gamma.de proves nothing).
    low = re.sub(r"https?://\S+|\S+@\S+|www\.\S+", " ", html.unescape(page or "")).lower()
    return bool(words) and all(w in low for w in words[:2])


def find(company: str, url: str, description: str, fetch=None, mail_check=None) -> tuple[str, str] | None:
    """(address, page it was found on) or None. Visits at most a few pages of the employer's own site."""
    fetch = fetch or _fetcher()
    if fetch is None or not company:
        return None
    check = mail_check or domain_accepts_mail
    for host in candidate_sites(company, url, description):
        site = registered_domain(host)
        best: tuple[int, str, str] | None = None
        confirmed, visited = host == _host(url), 0
        for path in PAGES:
            if visited >= MAX_PAGES:
                break
            page_url = f"https://{host}{path}"
            try:
                markup = fetch(page_url)
            except Exception:  # noqa: BLE001 - missing page, refused by robots.txt, site down
                continue
            visited += 1
            confirmed = confirmed or _names_company(markup, company)
            for email in emails_in(markup):
                if registered_domain(email.rsplit("@", 1)[1]) != site:
                    continue  # someone else's address (a web agency, a job board)
                r = rank(email)
                if r >= 0 and (best is None or r > best[0]):
                    best = (r, email, page_url)
            if best and best[0] == 2 and confirmed:
                break  # an application address on the confirmed site: done
        if best and confirmed and check(site):
            return best[1], best[2]
    return None


def try_find(session, posting) -> bool:
    """Look once for a posting that has only a web form; on success it becomes an email posting.
    The outcome is kept in posting.fields, so the same posting is never looked up twice."""
    from app.services.postings import company_of

    fields = dict(posting.fields or {})
    if posting.contact_email or fields.get("contact_lookup"):
        return bool(posting.contact_email)
    company_row = company_of(session, posting)
    company = company_row.name if company_row else str(fields.get("company") or "")
    if company.strip().lower() in ("", "unknown", "unknown company"):
        company = ""
    try:
        hit = find(company, posting.url or "", posting.description or "")
    except Exception as exc:  # noqa: BLE001 - a lookup must never stop scoring
        log.warning("contact lookup failed for %s: %s", posting.url, type(exc).__name__)
        hit = None
    fields["contact_lookup"] = "found" if hit else "none"
    if hit:
        posting.contact_email, posting.apply_route = hit[0], "email"
        fields["contact_email"], fields["contact_source"] = hit[0], hit[1]
    posting.fields = fields
    return bool(hit)
