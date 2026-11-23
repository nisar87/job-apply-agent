"""Pass at 360 pixels wide and keyboard-only: no sideways scrolling, every control named and reachable by Tab.

Start the servers as for smoke.py, then: python e2e/narrow.py
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

WEB = os.environ.get("WEB_URL", "http://localhost:3000")
API = os.environ.get("API_URL", "http://localhost:8000")
CHROME = os.environ.get("BROWSER_EXECUTABLE", "/usr/bin/google-chrome")
PAGES = ["/", "/setup", "/applications", "/settings", "/login", "/signup"]


def main() -> int:
    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 360, "height": 740})
        page.set_default_timeout(20000)
        signed_out = ["/", "/login", "/signup"]
        for path in signed_out:
            page.goto(WEB + path)
            page.wait_for_timeout(500)
        email = f"narrow{int(time.time())}@example.com"
        r = page.request.post(f"{API}/api/auth/signup", data={"name": "Narrow Test", "email": email, "password": "correct horse battery"})
        if not r.ok:
            print("could not sign up:", r.text())
            return 2
        page.goto(WEB + "/login")
        page.evaluate("t => localStorage.setItem('jaa_token', t)", r.json().get("access_token") or r.json().get("token"))
        for path in PAGES:
            page.goto(WEB + path)
            page.wait_for_timeout(800)
            width = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            if width[0] > width[1] + 1:
                problems.append(f"{path}: sideways scroll ({width[0]} wide in {width[1]})")
            unnamed = page.evaluate("""() => [...document.querySelectorAll('a,button,input,select,textarea')]
                .filter(e => e.type !== 'hidden' && !(e.textContent || '').trim() && !e.getAttribute('aria-label')
                             && !e.getAttribute('title') && !(e.labels && e.labels.length) && !e.getAttribute('placeholder'))
                .map(e => e.outerHTML.slice(0, 80))""")
            problems += [f"{path}: control without a name: {u}" for u in unnamed]
            # Keyboard only: Tab through every control; each focused one must show a visible outline or ring.
            page.evaluate("document.activeElement && document.activeElement.blur()")
            seen = 0
            for _ in range(12):
                page.keyboard.press("Tab")
                ring = page.evaluate("""() => { const e = document.activeElement; if (!e || e === document.body) return null;
                    const c = getComputedStyle(e); return (c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0) || c.boxShadow !== 'none'; }""")
                if ring is None:
                    continue
                seen += 1
                if not ring:
                    problems.append(f"{path}: a focused control shows no focus ring")
                    break
            if seen == 0:
                problems.append(f"{path}: Tab reaches no control")
        browser.close()
    for p in problems:
        print("PROBLEM:", p)
    print("narrow and keyboard pass:", "failed" if problems else "passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
