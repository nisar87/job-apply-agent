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
  for (const el of document.querySelectorAll('form input, form select, form textarea')) {
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
  if (document.querySelector('input[name*="card" i], input[autocomplete="cc-number"]')) found.push(['payment', 'the page asks for payment details']);
  if (!document.querySelector('form')) found.push(['no_form', 'no application form was found on the page']);
  return JSON.stringify(found);
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
        button = self._page.locator('form button[type="submit"], form input[type="submit"]').first
        button.click()
        self._page.wait_for_load_state("domcontentloaded")

    def page_text(self) -> str:
        return self._page.inner_text("body")

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._pw.stop()


class FakeDriver:
    """A scripted page for tests."""

    def __init__(self, fields: list[FormField] | None = None, blockers: list[Blocker] | None = None,
                 success_text: str = "Thank you for applying"):
        self.fields, self._blockers, self.success_text = fields or [], blockers or [], success_text
        self.filled: dict[str, object] = {}
        self.uploads: dict[str, str] = {}
        self.submitted = False
        self.url = ""
        self.closed = False

    def open(self, url: str) -> None:
        self.url = url

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
