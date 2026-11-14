"""Polite page fetching (master document, section 7): identify ourselves, obey
robots.txt, rate-limit per domain, cache for 24 hours, never log in."""

import html
import re
import time
from html.parser import HTMLParser
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from app.tasks.locks import get_redis

USER_AGENT = "JobApplyAgent/0.1 (+contact: see site)"
MAX_BYTES = 2_000_000
CACHE_SECONDS = 24 * 3600
MIN_GAP_SECONDS = 3


class FetchRefused(Exception):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(markup)
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", markup)
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


class Fetcher:
    def __init__(self, http: httpx.Client | None = None, use_cache: bool = True, min_gap: float = MIN_GAP_SECONDS):
        self.http = http or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})
        self.use_cache = use_cache
        self.min_gap = min_gap
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}

    def _allowed(self, url: str) -> bool:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                resp = self.http.get(f"{origin}/robots.txt")
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                    self._robots[origin] = rp
                else:
                    self._robots[origin] = None  # no robots file: allowed
            except httpx.HTTPError:
                self._robots[origin] = None
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def _wait_turn(self, host: str) -> None:
        if not self.min_gap:
            return
        r = get_redis()
        key = f"fetch:last:{host}"
        last = r.get(key)
        if last:
            wait = self.min_gap - (time.time() - float(last))
            if wait > 0:
                time.sleep(wait)
        r.set(key, str(time.time()), ex=60)

    def fetch_text(self, url: str) -> str:
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise FetchRefused("Only http and https addresses can be fetched")
        cache_key = f"fetch:page:{url}"
        if self.use_cache:
            cached = get_redis().get(cache_key)
            if cached is not None:
                return cached
        if not self._allowed(url):
            raise FetchRefused("The site's robots.txt does not allow fetching this page")
        self._wait_turn(parts.netloc)
        resp = self.http.get(url)
        if resp.status_code in (401, 403, 429):
            raise FetchRefused(f"The site refused the request ({resp.status_code})")
        resp.raise_for_status()
        if len(resp.content) > MAX_BYTES:
            raise FetchRefused("The page is too large")
        text = html_to_text(resp.text)
        if self.use_cache:
            get_redis().set(cache_key, text, ex=CACHE_SECONDS)
        return text
