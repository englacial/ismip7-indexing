"""Open gridlook.pages.dev on a dataset URL in headless Chromium; log console, network failures; screenshot.

Usage: python gridlook_headless.py <dataset_url_with_params> <screenshot.png> [seconds]
"""

import os
import sys
import time
from urllib.parse import quote

from playwright.sync_api import sync_playwright

dataset, shot = sys.argv[1], sys.argv[2]
wait_s = int(sys.argv[3]) if len(sys.argv) > 3 else 40
url = "https://gridlook.pages.dev/#" + dataset
proxy = os.environ.get("HTTPS_PROXY")
console, failures, requests = [], [], []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/opt/pw-browsers/chromium",
        args=["--no-sandbox", "--ignore-certificate-errors"],
        proxy={"server": proxy, "bypass": "127.0.0.1,localhost"} if proxy else None,
    )
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
    page.on("requestfailed", lambda r: failures.append(f"{r.method} {r.url} :: {r.failure}"))
    page.on("response", lambda r: requests.append(f"{r.status} {r.url[:140]}"))
    page.goto(url, wait_until="load", timeout=120_000)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        page.wait_for_timeout(2000)
    page.screenshot(path=shot)
    body = page.inner_text("body")[:3000]
    browser.close()

print("=== page text (head)")
print(body)
print("=== console (last 40)")
print("\n".join(console[-40:]))
print("=== request failures")
print("\n".join(failures[-20:]) or "none")
print("=== data requests (local + s3)")
print("\n".join(r for r in requests if "127.0.0.1" in r or "amazonaws" in r or "source.coop" in r)[-3000:])
