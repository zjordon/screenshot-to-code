"""Capture test screenshots for the screenshot-to-code GLM experiment.

Uses the backend's Playwright install. Desktop viewport matches the
project's screenshot_preview conventions (1280x832).
"""

import asyncio
import os
from playwright.async_api import async_playwright

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "test-screenshots")

# (filename, url, full_page) — shadcn examples are clean 2-3 screen pages,
# HN/Bing are one screen so viewport-only; GitHub trending is a dense list.
# (shadcn examples/cards|music|forms currently render empty — removed.)
TARGETS = [
    ("01-shadcn-dashboard", "https://ui.shadcn.com/examples/dashboard", True),
    ("02-bing-home", "https://www.bing.com/", False),
    ("03-shadcn-auth", "https://ui.shadcn.com/examples/authentication", True),
    ("04-hackernews-top", "https://news.ycombinator.com/", False),
    ("05-shadcn-playground", "https://ui.shadcn.com/examples/playground", True),
    ("06-github-trending", "https://github.com/trending", True),
]


async def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": 1280, "height": 832},
            device_scale_factor=1,
        )
        page = await context.new_page()

        for name, url, full_page in TARGETS:
            out_path = os.path.join(OUT_DIR, f"{name}.png")
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)  # let CSS/JS settle
                await page.screenshot(path=out_path, full_page=full_page)
                size_kb = os.path.getsize(out_path) // 1024
                print(f"OK   {name}.png ({size_kb} KB) <- {url}")
            except Exception as exc:
                print(f"FAIL {name} <- {url}: {exc}")

        await browser.close()


asyncio.run(main())
