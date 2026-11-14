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
            # 1. Sign up lands on the setup chat.
            page.goto(f"{WEB}/signup")
            page.get_by_label("Your name").fill("Alex Example")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("correct horse battery")
            page.get_by_role("button", name="Create account").click()
            page.wait_for_url("**/setup")

            # 2. The overview lists what is missing.
            page.goto(WEB)
            expect(page.get_by_text("Finish setting up before the agent can apply")).to_be_visible()

            # 3. Upload a CV and save the fact sheet.
            page.goto(f"{WEB}/cvs")
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
                f.write(CV)
            page.set_input_files('input[type="file"]', f.name)
            page.locator('input[name="label"]').fill("Backend")
            page.locator('input[name="keywords"]').fill("backend, python")
            page.get_by_role("button", name="Upload CV").click()
            expect(page.get_by_text("Default", exact=True)).to_be_visible()
            page.get_by_label("Notice period").fill("1 month")
            page.get_by_role("button", name="Save facts").click()
            expect(page.get_by_text("Saved")).to_be_visible()

            # 4. Connect the practice mailbox and set the urgent-reply address.
            page.goto(f"{WEB}/account")
            page.get_by_role("button", name="Connect Gmail").click()
            page.wait_for_url("**/account?mail=connected")
            expect(page.get_by_text("Connected", exact=True)).to_be_visible()
            page.get_by_label("Send urgent replies").fill("me@example.com")
            page.get_by_role("button", name="Save", exact=True).click()
            expect(page.get_by_text("Saved")).to_be_visible()

            # 5. Describe the search in the setup chat and start it.
            page.goto(f"{WEB}/setup")
            page.get_by_label("Your instruction").fill("Apply to 5 remote python backend jobs a day in Austria, not older than 1 week")
            page.get_by_role("button", name="Send").click()
            expect(page.get_by_text("Here is what I understood")).to_be_visible()
            expect(page.locator("dl")).to_contain_text("backend")
            page.get_by_role("button", name="Start this search").click()
            page.wait_for_url("**/campaigns")

            # 6. Open the send window for the whole day (the test may run at any hour), then find jobs and draft.
            token = page.evaluate("localStorage.getItem('jaa_token')")
            campaign = page.request.get(f"{API}/api/campaigns", headers={"Authorization": f"Bearer {token}"}).json()[0]
            config = {**campaign["config"], "send_window": {"start": "00:00", "end": "23:59", "days": [0, 1, 2, 3, 4, 5, 6]}}
            r = page.request.patch(f"{API}/api/campaigns/{campaign['id']}", headers={"Authorization": f"Bearer {token}"}, data={"config": config})
            assert r.ok, r.text()
            page.reload()
            page.get_by_role("button", name="Look for jobs now").click()
            expect(page.get_by_role("button", name="Look for jobs now")).to_be_enabled(timeout=30000)
            page.get_by_role("button", name="Write drafts now").click()
            expect(page.get_by_role("button", name="Write drafts now")).to_be_enabled(timeout=30000)

            # 7. Approve a draft.
            page.goto(f"{WEB}/drafts")
            expect(page.get_by_role("button", name="Approve").first).to_be_visible()
            drafts_before = page.get_by_role("button", name="Approve").count()
            assert drafts_before >= 2, f"expected at least 2 drafts, saw {drafts_before}"
            page.get_by_role("button", name="Approve").first.click()
            expect(page.get_by_role("button", name="Approve")).to_have_count(drafts_before - 1)

            # 8. The scheduler's dispatch sends it; the log and the detail page show it.
            deadline, sent = time.time() + 240, False
            while time.time() < deadline and not sent:
                out = page.request.post(f"{API}/api/campaigns/{campaign['id']}/run?kind=dispatch", headers={"Authorization": f"Bearer {token}"}).json()
                sent = bool(out.get("sent"))
                if not sent:
                    time.sleep(10)
            assert sent, "the approved draft was not sent within four minutes"
            page.goto(f"{WEB}/applications?status=sent")
            expect(page.locator("table.ledger tbody tr")).to_have_count(1)
            page.locator("table.ledger tbody tr a").first.click()
            expect(page.get_by_text("What happened, and why")).to_be_visible()
            expect(page.get_by_text("send application")).to_be_visible()

            # 9. Skipped and manual tabs have content; inbox and overview load.
            page.goto(f"{WEB}/applications?tab=skipped")
            expect(page.get_by_text("below your threshold").first).to_be_visible()
            page.goto(f"{WEB}/applications?tab=manual")
            expect(page.get_by_text("Open form").first).to_be_visible()
            page.goto(f"{WEB}/inbox")
            expect(page.get_by_text("No reply is waiting for you")).to_be_visible()
            page.goto(WEB)
            expect(page.get_by_text("1 of 5")).to_be_visible()
            shots = os.environ.get("SHOTS")
            if shots:
                page.set_viewport_size({"width": 1200, "height": 800})
                page.screenshot(path=f"{shots}/today.png")
                page.goto(f"{WEB}/drafts")
                page.wait_for_timeout(800)
                page.screenshot(path=f"{shots}/drafts.png")
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
