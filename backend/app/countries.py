"""Countries a search can be limited to, and which job sources cover each one.

Codes are ISO 3166-1 alpha-2, lower case. A country without a covering source can still be chosen
(postings from company feeds or pasted links may be there), and the app says that no source searches it.
"""

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
           "uae": "ae", "dubai": "ae", "ksa": "sa", "holland": "nl", "el": "gr"}

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
    """Job sources that search this country now. Adzuna counts only when its key is configured."""
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
