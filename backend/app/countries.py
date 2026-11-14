"""Countries a search can be limited to, and which job sources cover each one.

Codes are ISO 3166-1 alpha-2, lower case. A country without a covering source can still be chosen
(postings from company feeds or pasted links may be there), and the app says that no source searches it.
"""

import re

from app.config import get_settings

COUNTRIES: dict[str, str] = {
    # Europe
    "at": "Austria", "be": "Belgium", "bg": "Bulgaria", "ch": "Switzerland", "cy": "Cyprus", "cz": "Czechia",
    "de": "Germany", "dk": "Denmark", "ee": "Estonia", "es": "Spain", "fi": "Finland", "fr": "France",
    "gb": "United Kingdom", "gr": "Greece", "hr": "Croatia", "hu": "Hungary", "ie": "Ireland", "is": "Iceland",
    "it": "Italy", "lt": "Lithuania", "lu": "Luxembourg", "lv": "Latvia", "mt": "Malta", "nl": "Netherlands",
    "no": "Norway", "pl": "Poland", "pt": "Portugal", "ro": "Romania", "rs": "Serbia", "se": "Sweden",
    "si": "Slovenia", "sk": "Slovakia", "tr": "Turkey", "ua": "Ukraine",
    # Americas
    "ar": "Argentina", "br": "Brazil", "ca": "Canada", "cl": "Chile", "co": "Colombia", "mx": "Mexico",
    "us": "United States",
    # Middle East and Africa
    "ae": "United Arab Emirates", "bh": "Bahrain", "eg": "Egypt", "il": "Israel", "jo": "Jordan", "ke": "Kenya",
    "kw": "Kuwait", "ma": "Morocco", "ng": "Nigeria", "om": "Oman", "qa": "Qatar", "sa": "Saudi Arabia",
    "za": "South Africa",
    # Asia and Oceania
    "au": "Australia", "bd": "Bangladesh", "cn": "China", "hk": "Hong Kong", "id": "Indonesia", "in": "India",
    "jp": "Japan", "kr": "South Korea", "lk": "Sri Lanka", "my": "Malaysia", "nz": "New Zealand",
    "ph": "Philippines", "pk": "Pakistan", "sg": "Singapore", "th": "Thailand", "tw": "Taiwan", "vn": "Vietnam",
}

# Names people use that are not the ISO code.
ALIASES = {"uk": "gb", "england": "gb", "britain": "gb", "great britain": "gb", "usa": "us", "america": "us",
           "us": "us", "u.s.": "us", "united states of america": "us", "uae": "ae", "dubai": "ae", "ksa": "sa",
           "holland": "nl", "el": "gr", "czech republic": "cz", "korea": "kr", "türkiye": "tr", "turkiye": "tr"}

# Sources that search one country per request.
ADZUNA_COUNTRIES = {"at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb", "in", "it", "mx", "nl", "nz", "pl",
                    "sg", "us", "za"}
# Arbeitnow is one Europe-wide board; it is searched once, whatever countries are chosen.
ARBEITNOW_COUNTRIES = {"at", "be", "ch", "cz", "de", "dk", "es", "fi", "fr", "gb", "ie", "it", "lu", "nl", "no",
                       "pl", "pt", "se"}


def normalize(value: str) -> str | None:
    """A country code from a code, a name or a common alias; None if it is not one we know."""
    v = " ".join(str(value).strip().lower().split())
    if v in COUNTRIES:
        return v
    if v in ALIASES:
        return ALIASES[v]
    for code, name in COUNTRIES.items():
        if name.lower() == v:
            return code
    return None


def sources_for(code: str) -> list[str]:
    """Job sources that search this country now. Adzuna counts only when its key is configured. The remote
    boards (Remotive, Jobicy, Himalayas) are not tied to a country and are left out here."""
    s = get_settings()
    out = []
    if code in ADZUNA_COUNTRIES and s.adzuna_app_id and s.adzuna_app_key:
        out.append("adzuna")
    if code in ARBEITNOW_COUNTRIES:
        out.append("arbeitnow")
    return out


def catalogue() -> list[dict]:
    """Every country with its name and covering sources, sorted by name, for the country picker."""
    return sorted(
        ({"code": code, "name": name, "sources": sources_for(code),
          "adzuna_possible": code in ADZUNA_COUNTRIES} for code, name in COUNTRIES.items()),
        key=lambda c: c["name"],
    )


def name_of(code: str) -> str:
    return COUNTRIES.get(code, code.upper())


# ---- Where a remote job can hire from ----
# Remote boards say who may apply ("USA only", "Europe", "Worldwide", a list of countries). These groups turn
# region names into countries so the agent never applies from a country the employer cannot hire in.
_EUROPE = {"at", "be", "bg", "ch", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr", "gb", "gr", "hr", "hu", "ie", "is",
           "it", "lt", "lu", "lv", "mt", "nl", "no", "pl", "pt", "ro", "rs", "se", "si", "sk", "ua", "tr"}
_EU = _EUROPE - {"gb", "ch", "no", "is", "rs", "ua", "tr"}
_MIDDLE_EAST = {"ae", "bh", "il", "jo", "kw", "om", "qa", "sa", "eg"}
_AFRICA = {"eg", "ke", "ma", "ng", "za"}
_SOUTH_ASIA = {"pk", "in", "bd", "lk"}
_ASIA = _SOUTH_ASIA | {"cn", "hk", "id", "jp", "kr", "my", "ph", "sg", "th", "tw", "vn"}
_NORTH_AMERICA = {"us", "ca", "mx"}
_LATAM = {"ar", "br", "cl", "co", "mx"}
REGIONS: dict[str, set[str]] = {
    "europe": _EUROPE, "eu": _EU, "european union": _EU, "eea": _EU | {"no", "is"}, "uk/eu": _EU | {"gb"},
    "emea": _EUROPE | _MIDDLE_EAST | _AFRICA, "middle east": _MIDDLE_EAST, "mena": _MIDDLE_EAST | {"ma"},
    "africa": _AFRICA, "south asia": _SOUTH_ASIA, "asia": _ASIA, "apac": _ASIA | {"au", "nz"},
    "asia pacific": _ASIA | {"au", "nz"}, "asia-pacific": _ASIA | {"au", "nz"}, "oceania": {"au", "nz"},
    "north america": _NORTH_AMERICA, "americas": _NORTH_AMERICA | _LATAM, "latam": _LATAM,
    "latin america": _LATAM, "south america": {"ar", "br", "cl", "co"},
}
_WORLDWIDE = {"worldwide", "anywhere", "global", "world", "remote", "anywhere in the world", "work from anywhere"}


def split_hiring_labels(text: str | list | None) -> list[str]:
    """'Denmark,  Portugal | UK' or ['USA Only'] → ['Denmark', 'Portugal', 'UK'] / ['USA']."""
    items = text if isinstance(text, list) else re.split(r"[,;|/]| or ", str(text or ""))
    out = []
    for item in items:
        label = re.sub(r"\(.*?\)", " ", str(item))
        label = re.sub(r"\b(only|remote|based|residents?|timezones?)\b", " ", label, flags=re.IGNORECASE)
        label = " ".join(label.split()).strip(" .-")
        if label and label not in out:
            out.append(label)
    return out or ([] if not text else ["Worldwide"])


def hiring_allows(labels: list[str], home: str) -> bool | None:
    """Whether a job that hires from these places can hire someone living in `home` (an ISO code).
    True when it can, False when the labels clearly leave that country out, None when it is unclear."""
    if not labels:
        return True
    home = (home or "").lower()
    unclear = False
    for label in labels:
        key = " ".join(label.lower().split())
        if key in _WORLDWIDE:
            return True
        code = normalize(key)
        group = {code} if code else REGIONS.get(key)
        if group is None:
            unclear = True
        elif home in group:
            return True
    return None if unclear else False
