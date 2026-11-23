"""End-to-end smoke test of the web app in a real browser, in demo mode.

Start the two servers first (see e2e/README.md), then: python e2e/smoke.py
"""

import os
import sys
import tempfile
import time

from playwright.sync_api import expect, sync_playwright

WEB = os.environ.get("WEB_URL", "http://localhost:3000")
API = os.environ.get("API_URL", "http://localhost:8000")
CHROME = os.environ.get("BROWSER_EXECUTABLE", "/usr/bin/google-chrome")
CV = """Alex Example
Backend engineer with eight years of experience building data platforms.
Skills: python, postgresql, docker, fastapi, kubernetes, sql, airflow
alex@example.com https://linkedin.com/in/alex-example
Senior Engineer at Datawerk 2019-2026: led migration of 40 services to kubernetes.
"""


def main() -> int:
    email = f"e2e{int(time.time())}@example.com"
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_default_timeout(30000)
        expect.set_options(timeout=30000)   # a slow database (Docker on Windows) needs more than the default 5 s
        page.on("pageerror", lambda e: errors.append(f"page error: {e}"))
        page.on("console", lambda m: errors.append(f"console error: {m.text}") if m.type == "error" and "401" not in m.text else None)

        try:
            # 0. A signed-out visitor sees the front door, not an internal page.
            page.goto(WEB)
            expect(page.get_by_role("link", name="Create your account")).to_be_visible()
            page.goto(f"{WEB}/applications")
            page.wait_for_url("**/login**")

            # 1. Sign up lands on the four-step setup.
            page.goto(f"{WEB}/signup")
            page.get_by_label("Your name").fill("Alex Example")
            page.get_by_label("Email").fill(email)
            page.locator('input[type="password"]').nth(0).fill("correct horse battery")
            page.locator('input[type="password"]').nth(1).fill("correct horse battery")
            page.get_by_role("button", name="Create account").click()
            page.wait_for_url("**/setup")
            expect(page.get_by_text("Step 1 of 3")).to_be_visible()

            # 2. Step 1: the CV, and what was understood.
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
                f.write(CV)
            page.set_input_files('input[type="file"]', f.name)
            page.get_by_role("button", name="Read my CV").click()
            expect(page.get_by_text("Step 2 of 3")).to_be_visible()

            # 3. Step 2: connect the practice mailbox (the demo has no real Gmail), then the report address.
            expect(page.get_by_text("Step 2 of 3")).to_be_visible()
            token = page.evaluate("localStorage.getItem('jaa_token')")
            r = page.request.post(f"{API}/api/mail/fake/connect", headers={"Authorization": f"Bearer {token}"},
                                  data={"address": "alex.jobs@example.com"})
            assert r.ok, r.text()
            page.reload()
            expect(page.get_by_text("Step 2 of 3")).to_be_visible()      # mail is connected but no report address yet
            page.get_by_label("Main email").fill("me@example.com")
            page.get_by_role("button", name="Save", exact=True).first.click()
            expect(page.get_by_role("button", name="Continue")).to_be_enabled()
            page.get_by_role("button", name="Continue").click()

            # 4. Step 3: the plan is made from the CV; change it in words, switch "Try it first" off and start.
            expect(page.get_by_text("Step 3 of 3")).to_be_visible()
            page.get_by_label("I planned the search").fill("Apply to 5 remote python backend jobs a day in Austria, not older than 1 week")
            page.get_by_role("button", name="Change the plan").click()
            expect(page.get_by_text("backend").first).to_be_visible()
            page.get_by_label("Try it first").uncheck()
            page.get_by_role("button", name="Start my agent").click()
            page.wait_for_url(WEB + "/")
            expect(page.get_by_text("Your agent is working.")).to_be_visible()

            # 6. Open the send window for the whole day (the test may run at any hour), then find jobs and draft.
            token = page.evaluate("localStorage.getItem('jaa_token')")
            campaign = page.request.get(f"{API}/api/campaigns", headers={"Authorization": f"Bearer {token}"}).json()[0]
            config = {**campaign["config"], "send_window": {"start": "00:00", "end": "23:59", "days": [0, 1, 2, 3, 4, 5, 6]}}
            r = page.request.patch(f"{API}/api/campaigns/{campaign['id']}", headers={"Authorization": f"Bearer {token}"}, data={"config": config})
            assert r.ok, r.text()
            page.goto(f"{WEB}/campaigns")
            page.get_by_role("button", name="Look for jobs now").click()
            expect(page.get_by_role("button", name="Look for jobs now")).to_be_enabled(timeout=30000)
            page.get_by_role("button", name="Write drafts now").click()
            expect(page.get_by_role("button", name="Write drafts now")).to_be_enabled(timeout=30000)

            # 7. In full-auto mode nothing waits for approval: the written applications are queued.
            page.goto(f"{WEB}/applications?tab=applied")
            expect(page.locator("table.ledger tbody tr").first).to_be_visible()

            # 8. The scheduler's dispatch sends it; the log and the detail page show it.
            deadline, sent = time.time() + 240, False
            while time.time() < deadline and not sent:
                out = page.request.post(f"{API}/api/campaigns/{campaign['id']}/run?kind=dispatch", headers={"Authorization": f"Bearer {token}"}).json()
                sent = bool(out.get("sent"))
                if not sent:
                    time.sleep(10)
            assert sent, "the approved draft was not sent within four minutes"
            page.goto(f"{WEB}/applications?tab=applied")
            expect(page.locator("table.ledger tbody tr").first).to_be_visible()
            page.locator("table.ledger tbody tr a").first.click()
            expect(page.get_by_text("What happened, and why")).to_be_visible()
            expect(page.get_by_text("send application")).to_be_visible()

            # 9. Skipped and manual tabs have content; inbox and overview load.
            page.goto(f"{WEB}/applications?tab=skipped")
            expect(page.get_by_text("below your threshold").first).to_be_visible()
            page.goto(f"{WEB}/applications?tab=manual")
            expect(page.get_by_text("Open form").first).to_be_visible()
            page.goto(WEB)
            expect(page.get_by_text("Nothing needs you.").or_(page.get_by_text("Needs you (")).first).to_be_visible()
            expect(page.get_by_text("applied").first).to_be_visible()
            page.goto(f"{WEB}/settings")
            expect(page.get_by_text("What you want").first).to_be_visible()
            shots = os.environ.get("SHOTS")
            if shots:
                page.set_viewport_size({"width": 1200, "height": 800})
                page.screenshot(path=f"{shots}/today.png")
                page.goto(f"{WEB}/settings")
                page.wait_for_timeout(800)
                page.screenshot(path=f"{shots}/settings.png")
        except Exception:
            # Say where it stopped: the page, what it showed, and anything the browser complained about.
            print(f"Stopped at {page.url}")
            print("Page text: " + " | ".join(page.inner_text("body").split("\n"))[:1500])
            print("Browser errors: " + ("; ".join(errors) or "none"))
            raise
        browser.close()
    if errors:
        print("Browser errors:\n  " + "\n  ".join(errors))
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
