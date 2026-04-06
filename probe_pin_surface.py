import asyncio
import json
import os

from playwright.async_api import async_playwright


SURFACE_ID = int(os.environ.get("SURFACE_ID", "4834"))


async def main():
    email = os.environ["LIVEBARN_EMAIL"]
    password = os.environ["LIVEBARN_PASSWORD"]

    captured_requests = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Intercept all network requests so we can see API calls + headers
        async def on_request(request):
            if any(kw in request.url for kw in ("watchapi", "livebarn", "m3u8", "token", "pin", "access")):
                captured_requests.append({
                    "method": request.method,
                    "url": request.url,
                    "headers": dict(request.headers),
                    "post_data": request.post_data,
                })

        async def on_response(response):
            if any(kw in response.url for kw in ("watchapi", "livebarn", "m3u8", "token", "pin", "access")):
                try:
                    body = await response.text()
                except Exception:
                    body = "<binary>"
                captured_requests.append({
                    "RESPONSE": True,
                    "status": response.status,
                    "url": response.url,
                    "body_preview": body[:2000],
                })

        page.on("request", on_request)
        page.on("response", on_response)

        # Login
        print("=== Logging in ===")
        await page.goto("https://watch.livebarn.com", wait_until="domcontentloaded")
        await page.fill('input[name="username"]', email)
        await page.fill('input[type="password"]', password)
        await page.click('button:has-text("LOG IN")')
        await page.wait_for_timeout(4000)

        # Navigate to pin-protected surface
        print(f"\n=== Loading surface {SURFACE_ID} ===")
        await page.goto(
            f"https://watch.livebarn.com/en/video/{SURFACE_ID}/live",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(6000)

        # Page text snapshot
        text = await page.evaluate("document.body.innerText")
        print("\n--- Page text ---")
        print(text[:3000])

        # DOM elements (inputs, buttons, forms)
        print("\n--- Form elements ---")
        selectors = ["input", "button", "form", '[type="password"]', '[type="tel"]', '[type="number"]']
        for sel in selectors:
            count = await page.locator(sel).count()
            if count == 0:
                continue
            print(f"\nSelector: {sel}  ({count} found)")
            for i in range(min(count, 10)):
                loc = page.locator(sel).nth(i)
                attrs = await loc.evaluate(
                    """el => ({
                        tag: el.tagName,
                        type: el.getAttribute('type'),
                        name: el.getAttribute('name'),
                        placeholder: el.getAttribute('placeholder'),
                        value: el.value || '',
                        text: (el.innerText || '').trim().slice(0, 80)
                    })"""
                )
                print(" ", json.dumps(attrs))

        # Network traffic
        print("\n--- Captured network traffic ---")
        for entry in captured_requests:
            print(json.dumps(entry, indent=2))

        await browser.close()


asyncio.run(main())
