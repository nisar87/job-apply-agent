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
# Every other country, so a user can search wherever they want to work (ISO 3166-1).
COUNTRIES.update({
    "ad": "Andorra", "af": "Afghanistan", "ag": "Antigua and Barbuda", "al": "Albania", "am": "Armenia",
    "ao": "Angola", "az": "Azerbaijan", "ba": "Bosnia and Herzegovina", "bb": "Barbados", "bf": "Burkina Faso",
    "bi": "Burundi", "bj": "Benin", "bn": "Brunei", "bo": "Bolivia", "bs": "Bahamas", "bt": "Bhutan",
    "bw": "Botswana", "by": "Belarus", "bz": "Belize", "cd": "Congo (DRC)", "cf": "Central African Republic",
    "cg": "Congo", "ci": "Côte d'Ivoire", "cm": "Cameroon", "cr": "Costa Rica", "cu": "Cuba", "cv": "Cape Verde",
    "dj": "Djibouti", "dm": "Dominica", "do": "Dominican Republic", "dz": "Algeria", "ec": "Ecuador",
    "er": "Eritrea", "et": "Ethiopia", "fj": "Fiji", "ga": "Gabon", "gd": "Grenada", "ge": "Georgia", "gh": "Ghana",
    "gm": "Gambia", "gn": "Guinea", "gq": "Equatorial Guinea", "gt": "Guatemala", "gw": "Guinea-Bissau",
    "gy": "Guyana", "hn": "Honduras", "ht": "Haiti", "iq": "Iraq", "ir": "Iran", "jm": "Jamaica", "kg": "Kyrgyzstan",
    "kh": "Cambodia", "km": "Comoros", "kn": "Saint Kitts and Nevis", "kz": "Kazakhstan", "la": "Laos",
    "lb": "Lebanon", "lc": "Saint Lucia", "li": "Liechtenstein", "lr": "Liberia", "ls": "Lesotho", "ly": "Libya",
    "mc": "Monaco", "md": "Moldova", "me": "Montenegro", "mg": "Madagascar", "mk": "North Macedonia", "ml": "Mali",
    "mm": "Myanmar", "mn": "Mongolia", "mo": "Macao", "mr": "Mauritania", "mu": "Mauritius", "mv": "Maldives",
    "mw": "Malawi", "mz": "Mozambique", "na": "Namibia", "ne": "Niger", "ni": "Nicaragua", "np": "Nepal",
    "pa": "Panama", "pe": "Peru", "pg": "Papua New Guinea", "pr": "Puerto Rico", "ps": "Palestine", "py": "Paraguay",
    "rw": "Rwanda", "sb": "Solomon Islands", "sc": "Seychelles", "sd": "Sudan", "sl": "Sierra Leone",
    "sm": "San Marino", "sn": "Senegal", "so": "Somalia", "sr": "Suriname", "ss": "South Sudan",
    "sv": "El Salvador", "sy": "Syria", "sz": "Eswatini", "td": "Chad", "tg": "Togo", "tj": "Tajikistan",
    "tl": "Timor-Leste", "tm": "Turkmenistan", "tn": "Tunisia", "to": "Tonga", "tt": "Trinidad and Tobago",
    "tz": "Tanzania", "ug": "Uganda", "uy": "Uruguay", "uz": "Uzbekistan", "va": "Vatican City",
    "vc": "Saint Vincent and the Grenadines", "ve": "Venezuela", "ws": "Samoa", "xk": "Kosovo", "ye": "Yemen",
    "zm": "Zambia", "zw": "Zimbabwe",
})

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
           "it", "lt", "lu", "lv", "mt", "nl", "no", "pl", "pt", "ro", "rs", "se", "si", "sk", "ua", "tr",
           "ad", "al", "ba", "by", "li", "mc", "md", "me", "mk", "sm", "va", "xk"}
_EU = {"at", "be", "bg", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr", "gr", "hr", "hu", "ie", "it", "lt", "lu", "lv",
       "mt", "nl", "pl", "pt", "ro", "se", "si", "sk"}
_MIDDLE_EAST = {"ae", "bh", "il", "jo", "kw", "om", "qa", "sa", "eg", "iq", "ir", "lb", "ps", "sy", "ye"}
_AFRICA = {"eg", "ke", "ma", "ng", "za", "ao", "bf", "bi", "bj", "bw", "cd", "cf", "cg", "ci", "cm", "cv", "dj", "dz",
           "er", "et", "ga", "gh", "gm", "gn", "gq", "gw", "km", "lr", "ls", "ly", "mg", "ml", "mr", "mu", "mw", "mz",
           "na", "ne", "rw", "sc", "sd", "sl", "sn", "so", "ss", "sz", "td", "tg", "tn", "tz", "ug", "zm", "zw"}
_SOUTH_ASIA = {"pk", "in", "bd", "lk", "af", "bt", "mv", "np"}
_ASIA = _SOUTH_ASIA | {"cn", "hk", "id", "jp", "kr", "my", "ph", "sg", "th", "tw", "vn", "bn", "kh", "la", "mm", "mn",
                       "mo", "tl", "kz", "kg", "tj", "tm", "uz", "am", "az", "ge"}
_NORTH_AMERICA = {"us", "ca", "mx"}
_LATAM = {"ar", "br", "cl", "co", "mx", "bo", "cr", "cu", "do", "ec", "gt", "hn", "ni", "pa", "pe", "pr", "py", "sv",
          "uy", "ve"}
REGIONS: dict[str, set[str]] = {
    "europe": _EUROPE, "eu": _EU, "european union": _EU, "eea": _EU | {"no", "is"}, "uk/eu": _EU | {"gb"},
    "emea": _EUROPE | _MIDDLE_EAST | _AFRICA, "middle east": _MIDDLE_EAST, "mena": _MIDDLE_EAST | {"ma"},
    "africa": _AFRICA, "south asia": _SOUTH_ASIA, "asia": _ASIA, "apac": _ASIA | {"au", "nz"},
    "asia pacific": _ASIA | {"au", "nz"}, "asia-pacific": _ASIA | {"au", "nz"}, "oceania": {"au", "nz"},
    "north america": _NORTH_AMERICA, "americas": _NORTH_AMERICA | _LATAM, "latam": _LATAM,
    "latin america": _LATAM, "south america": {"ar", "br", "cl", "co", "bo", "ec", "gy", "pe", "py", "sr", "uy", "ve"},
    "central asia": {"kz", "kg", "tj", "tm", "uz"}, "caribbean": {"ag", "bb", "bs", "cu", "dm", "do", "gd", "ht", "jm",
                                                                 "kn", "lc", "pr", "tt", "vc"},
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
