"""Browser automation for web application forms (master document, section 14, phase 3).

`PlaywrightDriver` drives a real browser. `FakeDriver` replays a scripted page for tests.
Neither logs in, solves a CAPTCHA or enters payment details: those are stop conditions.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Protocol

from app.config import get_settings


@dataclass
class FormField:
    id: str            # stable key for this page visit
    label: str
    type: str          # text | email | tel | url | number | textarea | select | radio | checkbox | file | date
    required: bool = False
    options: list[str] = field(default_factory=list)
    name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Blocker:
    kind: str          # login | captcha | payment | no_form
    detail: str = ""


class BrowserDriver(Protocol):
    def open(self, url: str) -> None: ...
    def blockers(self) -> list[Blocker]: ...
    def read_fields(self) -> list[FormField]: ...
    def fill(self, field: FormField, value) -> None: ...
    def upload(self, field: FormField, file_name: str, data: bytes) -> None: ...
    def screenshot(self) -> bytes: ...
    def submit(self) -> None: ...
    def page_text(self) -> str: ...
    def follow_apply_link(self) -> bool: ...
    def current_url(self) -> str: ...
    def close(self) -> None: ...


# Collects every visible form control with its human label. Runs inside the page.
_READ_FIELDS_JS = r"""
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return (r.width > 0 || el.type === 'file') && s.visibility !== 'hidden' && s.display !== 'none'; };
  const labelOf = (el) => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    const wrap = el.closest('label'); if (wrap) return wrap.innerText;
    const fs = el.closest('fieldset'); if (fs && fs.querySelector('legend') && (el.type === 'radio' || el.type === 'checkbox')) return fs.querySelector('legend').innerText;
    return el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
  };
  const out = []; const groups = {}; let n = 0;
  // Only the page's biggest form: a header search box or newsletter sign-up is not the application.
  const controls = (f) => [...f.querySelectorAll('input, select, textarea')].filter(el =>
    !['hidden', 'submit', 'button', 'image', 'reset'].includes(el.type) && visible(el)).length;
  const forms = [...document.querySelectorAll('form')].sort((a, b) => controls(b) - controls(a));
  if (!forms.length) return '[]';
  document.querySelectorAll('[data-jaa-form]').forEach(f => f.removeAttribute('data-jaa-form'));
  forms[0].setAttribute('data-jaa-form', '1');
  for (const el of forms[0].querySelectorAll('input, select, textarea')) {
    const type = (el.tagName === 'SELECT') ? 'select' : (el.tagName === 'TEXTAREA') ? 'textarea' : (el.type || 'text');
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type) || !visible(el)) continue;
    if (type === 'radio') {
      const key = el.name || labelOf(el);
      if (!groups[key]) { groups[key] = { id: 'f' + (n++), label: (el.closest('fieldset')?.querySelector('legend')?.innerText) || key, type: 'radio',
        required: el.required, options: [], name: el.name }; out.push(groups[key]); }
      const optLabel = (el.closest('label')?.innerText) || (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.innerText) || el.value;
      groups[key].options.push(optLabel.trim()); el.setAttribute('data-jaa-id', groups[key].id + ':' + (groups[key].options.length - 1));
      continue;
    }
    const id = 'f' + (n++); el.setAttribute('data-jaa-id', id);
    out.push({ id, label: labelOf(el).replace(/\s+/g, ' ').trim(), type, required: el.required || el.getAttribute('aria-required') === 'true',
      options: type === 'select' ? [...el.options].map(o => o.text.trim()).filter(Boolean) : [], name: el.name || '' });
  }
  return JSON.stringify(out);
}
"""

_BLOCKERS_JS = r"""
() => {
  const found = [];
  if (document.querySelector('input[type="password"]')) found.push(['login', 'the page asks for a password']);
  if (document.querySelector('.g-recaptcha, [data-sitekey], .h-captcha, .cf-turnstile, iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"]'))
    found.push(['captcha', 'the page shows a CAPTCHA']);
  if (/^(just a moment|attention required|verify you are human|access denied)/i.test(document.title.trim()) ||
      location.href.includes('__cf_chl') || document.querySelector('#challenge-form, #cf-challenge-running, #px-captcha'))
    found.push(['captcha', 'the site shows a bot check (CAPTCHA)']);
  if (document.querySelector('input[name*="card" i], input[autocomplete="cc-number"]')) found.push(['payment', 'the page asks for payment details']);
  if (!document.querySelector('form')) found.push(['no_form', 'no application form was found on the page']);
  return JSON.stringify(found);
}
"""


# Job boards show the posting with an "Apply" button; the form is one click further, often on the
# employer's own hiring system. Marks the best visible apply control and returns its link, if any.
_APPLY_LINK_JS = r"""
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const words = /\b(apply|bewerben|bewerbung|postuler|solliciteer)\b/i;
  const never = /filter|sign in|log ?in|anmelden|einloggen|similar|save|merken|alert|newsletter|info|tips|how to|more about|mehr zu/i;
  let best = null, bestScore = -1;
  for (const el of document.querySelectorAll('a, button, [role="button"], input[type="button"], input[type="submit"]')) {
    if (el.closest('form') && el.closest('form').querySelector('input[type="search"], input[name*="search" i], input[name="q"]')) continue;
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!text || text.length > 60 || !words.test(text) || never.test(text) || !visible(el)) continue;
    const href = el.tagName === 'A' ? el.href : '';
    if (href && /^(javascript:|mailto:|tel:)/i.test(href)) continue;
    const score = (/^(apply|apply now|jetzt bewerben|bewerben|apply for this (job|position|role))\b/i.test(text) ? 2 : 1) + (href ? 1 : 0);
    if (score > bestScore) { best = el; bestScore = score; }
  }
  if (!best) return JSON.stringify(null);
  document.querySelectorAll('[data-jaa-apply]').forEach(e => e.removeAttribute('data-jaa-apply'));
  best.setAttribute('data-jaa-apply', '1');
  const href = best.tagName === 'A' ? best.href : '';
  return JSON.stringify({ href: href && !href.endsWith('#') ? href : '' });
}
"""


# A cookie banner covers the page and swallows clicks. The most private choice is taken: reject / necessary only.
_DECLINE_COOKIES_JS = r"""
() => {
  const words = /^(reject all|reject|decline all|decline|only necessary|necessary only|use necessary cookies only|refuse all|deny|alle ablehnen|ablehnen|nur notwendige|nur erforderliche|nur essenzielle|tout refuser|continue without accepting)/i;
  for (const el of document.querySelectorAll('button, [role="button"], a')) {
    const text = (el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    const r = el.getBoundingClientRect();
    if (text.length < 50 && words.test(text) && r.width > 0 && r.height > 0) { el.click(); return true; }
  }
  return false;
}
"""


class PlaywrightDriver:
    def __init__(self, headless: bool = True):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        s = get_settings()
        launch = {"headless": headless, "args": ["--no-sandbox"]}
        if s.browser_executable:
            launch["executable_path"] = s.browser_executable
        self._browser = self._pw.chromium.launch(**launch)
        self._page = self._browser.new_page()
        self._page.set_default_timeout(20000)

    def open(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")
        self._settle()

    def _settle(self) -> None:
        """Most job boards draw the ad and its Apply button with JavaScript after the page loads."""
        try:
            self._page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001 - pages with endless background requests never go idle
            pass
        try:
            if self._page.evaluate(_DECLINE_COOKIES_JS):
                self._page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001 - a banner that cannot be closed is not a reason to stop
            pass

    def blockers(self) -> list[Blocker]:
        return [Blocker(kind=k, detail=d) for k, d in json.loads(self._page.evaluate(_BLOCKERS_JS))]

    def read_fields(self) -> list[FormField]:
        return [FormField(**f) for f in json.loads(self._page.evaluate(_READ_FIELDS_JS))]

    def _locator(self, field: FormField, option_index: int | None = None):
        key = field.id if option_index is None else f"{field.id}:{option_index}"
        return self._page.locator(f'[data-jaa-id="{key}"]')

    def fill(self, field: FormField, value) -> None:
        if field.type == "radio":
            self._locator(field, field.options.index(str(value))).check()
        elif field.type == "checkbox":
            self._locator(field).set_checked(bool(value))
        elif field.type == "select":
            self._locator(field).select_option(label=str(value))
        else:
            self._locator(field).fill(str(value))

    def upload(self, field: FormField, file_name: str, data: bytes) -> None:
        self._locator(field).set_input_files({"name": file_name, "mimeType": "application/octet-stream", "buffer": data})

    def screenshot(self) -> bytes:
        return self._page.screenshot(full_page=True)

    def submit(self) -> None:
        # The submit button of the form that was read and filled, not a search box elsewhere on the page.
        scope = 'form[data-jaa-form]' if self._page.locator('form[data-jaa-form]').count() else 'form'
        button = self._page.locator(f'{scope} button[type="submit"], {scope} input[type="submit"], '
                                    f'{scope} button:not([type])').first
        button.click()
        self._page.wait_for_load_state("domcontentloaded")

    def page_text(self) -> str:
        return self._page.inner_text("body")

    def current_url(self) -> str:
        return self._page.url

    def follow_apply_link(self) -> bool:
        """Open the posting's Apply link or button. True if the browser went somewhere new."""
        target = json.loads(self._page.evaluate(_APPLY_LINK_JS))
        if target is None:
            return False
        before = self._page.url
        if target["href"]:
            self.open(target["href"])
            return self._page.url != before or bool(self._page.query_selector("form"))
        pages = len(self._page.context.pages)
        button = self._page.locator('[data-jaa-apply="1"]').first
        try:
            button.click(timeout=5000)
        except Exception:  # noqa: BLE001 - something still covers the button: click it from inside the page
            try:
                button.evaluate("el => el.click()")
            except Exception:  # noqa: BLE001 - the button went away; nothing was opened
                return False
        try:
            self._page.wait_for_timeout(1500)       # a button often opens the form in place or in a new tab
            if len(self._page.context.pages) > pages:
                self._page = self._page.context.pages[-1]
            self._page.wait_for_load_state("domcontentloaded")
            self._settle()
        except Exception:  # noqa: BLE001 - the page may still be loading; it is read as it is
            pass
        return True

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._pw.stop()


class FakeDriver:
    """A scripted page for tests."""

    def __init__(self, fields: list[FormField] | None = None, blockers: list[Blocker] | None = None,
                 success_text: str = "Thank you for applying", apply_page: dict | None = None):
        """`apply_page` scripts the page behind the posting's Apply link: {url, fields, blockers}."""
        self.fields, self._blockers, self.success_text = fields or [], blockers or [], success_text
        self.apply_page = apply_page
        self._start = (list(self.fields), list(self._blockers))
        self.filled: dict[str, object] = {}
        self.uploads: dict[str, str] = {}
        self.submitted = False
        self.url = ""
        self.visited: list[str] = []
        self.closed = False

    def open(self, url: str) -> None:
        self.url = url
        self.visited.append(url)
        if self.apply_page and url == self.apply_page.get("url"):
            self.fields, self._blockers = list(self.apply_page.get("fields", [])), list(self.apply_page.get("blockers", []))
        else:
            self.fields, self._blockers = list(self._start[0]), list(self._start[1])

    def current_url(self) -> str:
        return self.url

    def follow_apply_link(self) -> bool:
        if not self.apply_page or self.url == self.apply_page.get("url"):
            return False
        self.open(self.apply_page.get("url", "https://apply.example/form"))
        return True

    def blockers(self) -> list[Blocker]:
        return list(self._blockers)

    def read_fields(self) -> list[FormField]:
        return list(self.fields)

    def fill(self, field: FormField, value) -> None:
        self.filled[field.id] = value

    def upload(self, field: FormField, file_name: str, data: bytes) -> None:
        self.uploads[field.id] = file_name

    def screenshot(self) -> bytes:
        return b"\x89PNG fake"

    def submit(self) -> None:
        self.submitted = True

    def page_text(self) -> str:
        return self.success_text if self.submitted else "Application form"

    def close(self) -> None:
        self.closed = True


_factory = None


def set_driver_factory(factory) -> None:
    global _factory
    _factory = factory


def uses_real_browser() -> bool:
    """True unless tests replaced the browser with a fake one."""
    return _factory is None


def new_driver() -> BrowserDriver:
    if _factory is not None:
        return _factory()
    return PlaywrightDriver()
