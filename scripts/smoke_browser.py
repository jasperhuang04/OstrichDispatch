"""Optional local browser regression test against synthetic demo data.

Install playwright and its Chromium browser separately; start the local server,
set DEMO_PASSWORD to the same value used by `manage.py demo`, then run this file.
Screenshots contain only synthetic data and are written to ignored test-results/.
"""
import os
import atexit
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

base = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000")
password = os.environ["DEMO_PASSWORD"]
folder = Path(__file__).resolve().parent.parent / "test-results"
folder.mkdir(exist_ok=True)
if os.getenv("START_SMOKE_SERVER") == "true":
    process = subprocess.Popen([sys.executable, "manage.py", "runserver", "127.0.0.1:8000", "--noreload"], cwd=folder.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(process.terminate)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with opener.open(base+"/healthz/", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(.25)
    else:
        raise RuntimeError("Local test server did not start")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width":1440, "height":1050})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base+"/login/")
    page.get_by_label("Email address").fill("demo@example.com")
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in →").click()
    page.wait_for_url(base+"/")
    page.get_by_role("heading", name="Every order, together.").wait_for()
    page.screenshot(path=str(folder/"dashboard-desktop.png"), full_page=True)
    for path in ["/inboxes/", "/insights/", "/calendar/", "/inventory/", "/settings/", "/notifications/", "/orders/new/"]:
        response = page.goto(base+path)
        assert response.status == 200, (path, response.status)
        assert page.locator("h1").count() == 1, path
    page.goto(base+"/insights/")
    assert page.locator("svg.spending-chart").count() > 0
    page.screenshot(path=str(folder/"insights.png"), full_page=True)
    page.get_by_role("button", name="Toggle color theme").click()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    page.get_by_role("button", name="Toggle color theme").click()
    page.set_viewport_size({"width":390, "height":844})
    page.goto(base+"/")
    page.screenshot(path=str(folder/"dashboard-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile layout overflows"
    page.get_by_role("link", name="＋ Add an order").click()
    page.locator("#id_title").fill("Browser test order")
    page.locator("#id_merchant").fill("Test shop")
    page.locator("#id_number").fill("BROWSER-1234")
    page.locator("#id_total").fill("26.00")
    page.locator("#id_tracking_number").fill("1Z879E930346834440")
    page.get_by_role("button", name="Save order").click()
    page.get_by_role("heading", name="Browser test order", exact=True).wait_for()
    page.get_by_role("button", name="Archive order").click()
    page.wait_for_url(base+"/")
    assert not errors, errors
    browser.close()
print("Browser smoke passed: login, pages, chart, theme, mobile layout, create and archive.")
