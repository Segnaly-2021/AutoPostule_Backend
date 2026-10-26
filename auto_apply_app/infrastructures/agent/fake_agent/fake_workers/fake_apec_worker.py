# auto_apply_app/infrastructures/agent/fake_workers/fake_apec_worker.py

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright, Locator

from auto_apply_app.domain.entities.job_snippet import JobSnippet
from auto_apply_app.domain.value_objects import JobBoard
from auto_apply_app.infrastructures.agent.input.display import XvfbDisplay
from auto_apply_app.infrastructures.agent.pacing import (
    ASSET_ALLOW_HOSTS,
    BLOCKED_TYPES,
    _TRANSPARENT_GIF,
)

# Framebuffer for the private Xvfb on display-less hosts (Cloud Run).
_SCREEN_W, _SCREEN_H = 1920, 1080

# Debug snapshots (opt-in). Cloud Run's filesystem dies with the instance, so
# screenshots are pushed to GCS mid-run and pulled down afterwards with gcloud.
_SNAPSHOT_PREFIX = "debug/free-search/apec"


def _snapshots_enabled() -> bool:
    return os.getenv("FREE_SEARCH_DEBUG_SNAPSHOTS", "").strip().lower() in ("1", "true", "yes", "on")


class FakeApecWorker:
    """
    Search-only worker for APEC.

    Selectors are kept in sync with the production ApecWorker. Notably the card
    selector uses a *contains* match (class*=) rather than exact class-string
    equality, so it survives APEC appending/reordering utility classes such as
    the trailing 'card-offer--qualified'.
    """

    # Single source of truth for the job card selector — mirrors the real worker.
    CARD_SELECTOR = 'div[class*="card card-offer mb-20 card--clickable"]'

    def __init__(self, proxy_service=None):
        self.base_url = "https://www.apec.fr/"
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._x_display: Optional[XvfbDisplay] = None

        # ProxyServicePort (TwoCaptcha / NoProxy). None -> direct connection.
        self._proxy_service = proxy_service
        self._blocked_assets: Optional[dict] = None

        # Snapshot state, reset per search_jobs call.
        self._snap_bucket = None
        self._snap_run_id: Optional[str] = None
        self._snap_seq = 0

    def _init_snapshots(self) -> None:
        """Set up the GCS bucket for this run's snapshots, or leave it off.

        Bucket: DEBUG_SNAPSHOT_BUCKET, else GCP_RESUME_BUCKET (already set and
        writable on the free-search Service). Credential handling mirrors
        BrowserSessionStore. Any failure just disables snapshots.
        """
        self._snap_bucket = None
        self._snap_seq = 0
        if not _snapshots_enabled():
            return

        bucket_name = os.getenv("DEBUG_SNAPSHOT_BUCKET") or os.getenv("GCP_RESUME_BUCKET")
        if not bucket_name:
            print("[Fake APEC] Snapshots enabled but no bucket configured, skipping")
            return

        try:
            from google.cloud import storage

            creds_json = os.getenv("GCP_CREDENTIALS")
            if creds_json:
                client = storage.Client.from_service_account_info(json.loads(creds_json))
            else:
                client = storage.Client()
            self._snap_bucket = client.bucket(bucket_name)
            self._snap_run_id = (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
            )
            print(f"[Fake APEC] Snapshots -> gs://{bucket_name}/{_SNAPSHOT_PREFIX}/{self._snap_run_id}/")
        except Exception as e:
            print(f"[Fake APEC] Snapshot setup failed ({e}), continuing without")
            self._snap_bucket = None

    async def _debug_snapshot(self, step: str) -> None:
        """Screenshot + HTML + meta of the current page to GCS. Never raises."""
        if self._snap_bucket is None or self.page is None:
            return

        self._snap_seq += 1
        base = f"{_SNAPSHOT_PREFIX}/{self._snap_run_id}/{self._snap_seq:02d}_{step}"

        def _upload(name: str, data, content_type: str) -> None:
            self._snap_bucket.blob(name).upload_from_string(data, content_type=content_type)

        try:
            png = await self.page.screenshot(full_page=True, timeout=10000)
            await asyncio.to_thread(_upload, f"{base}.png", png, "image/png")

            html = await self.page.content()
            await asyncio.to_thread(_upload, f"{base}.html", html, "text/html; charset=utf-8")

            meta = {
                "step": step,
                "url": self.page.url,
                "title": await self.page.title(),
                "at": datetime.now(timezone.utc).isoformat(),
            }
            await asyncio.to_thread(_upload, f"{base}.json", json.dumps(meta, ensure_ascii=False), "application/json")

            print(f"[Fake APEC] snapshot -> gs://{self._snap_bucket.name}/{base}.png ({meta['title']!r} @ {meta['url']})")
        except Exception as e:
            print(f"[Fake APEC] snapshot '{step}' failed: {e}")

    def _launch_kwargs(self) -> dict:
        """
        APEC needs a headful browser. Where a screen already exists (Windows,
        macOS, a Linux desktop / WSLg with $DISPLAY) we just launch headful on it.
        On a display-less Linux host (the free-search Cloud Run Service) we start
        a private Xvfb for this browser — the image already ships xvfb for the
        paid agent's x11 backend.

        XvfbDisplay is used directly rather than open_display(): the latter
        mutates module globals that guard python-xlib for the real agent, which
        shares this process in local dev.

        Fails soft: no Xvfb -> headless, so APEC degrades instead of returning nothing.
        """
        kwargs = {
            "headless": False,
            "args": ['--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage'],
        }

        if not sys.platform.startswith("linux") or os.environ.get("DISPLAY"):
            return kwargs

        try:
            self._x_display = XvfbDisplay(_SCREEN_W, _SCREEN_H).start()
        except Exception as e:
            print(f"[Fake APEC] No X display available ({e}), falling back to headless")
            self._x_display = None
            kwargs["headless"] = True
            return kwargs

        kwargs["env"] = {**os.environ, "DISPLAY": self._x_display.display}
        kwargs["args"].append(f"--window-size={_SCREEN_W},{_SCREEN_H}")
        print(f"[Fake APEC] Headful on Xvfb {self._x_display.display}")
        return kwargs

    def _resolve_proxy(self, user_id: str) -> Optional[dict]:
        """
        A fresh residential exit IP per free search: the session key is random,
        so the sticky session never carries over between runs. Fails soft —
        a proxy problem degrades to a direct connection, not a lost search.
        """
        if self._proxy_service is None:
            return None
        try:
            proxy = self._proxy_service.get_proxy_for_run(
                user_id, f"free-search-{uuid.uuid4().hex}"
            )
        except Exception as e:
            print(f"[Fake APEC] Proxy resolution failed ({e}), going direct")
            return None
        if not proxy:
            return None
        # Never log the credentials — server only.
        print(f"[Fake APEC] Routing through proxy {proxy['server']}")
        return {
            "server": proxy["server"],
            "username": proxy["username"],
            "password": proxy["password"],
        }

    async def _block_heavy_assets(self) -> None:
        """
        "Light" mode for proxied runs: images and video/audio never hit the
        residential proxy. Mirrors PacingMixin._conserve_bandwidth, hard-wired to
        BLOCKED_TYPES["on"] instead of reading AGENT_BLOCK_HEAVY_ASSETS.

        Images are fulfilled with a local transparent GIF rather than aborted so
        the page still sees a loaded image; captcha/challenge hosts are never
        blocked. This worker registers no other route, so order is unconstrained.
        """
        blocked = BLOCKED_TYPES["on"]
        saved = {kind: 0 for kind in blocked}

        async def _handler(route):
            request = route.request
            url = request.url
            try:
                if request.resource_type in blocked and not any(
                    host in url for host in ASSET_ALLOW_HOSTS
                ):
                    saved[request.resource_type] += 1
                    if request.resource_type == "image":
                        await route.fulfill(
                            status=200,
                            content_type="image/gif",
                            body=_TRANSPARENT_GIF,
                        )
                    else:
                        await route.abort()
                    return
                await route.fallback()
            except Exception:
                # A route that raises leaves the request hanging until timeout.
                try:
                    await route.fallback()
                except Exception:
                    pass

        try:
            await self.context.route("**/*", _handler)
            self._blocked_assets = saved
            print(f"[Fake APEC] Light mode: blocking {', '.join(sorted(blocked))}")
        except Exception as e:
            print(f"[Fake APEC] Could not install the asset blocker: {e}")

    async def _init_browser(self, user_id: str = "unknown"):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(**self._launch_kwargs())

        context_kwargs = {}
        proxy = self._resolve_proxy(user_id)
        if proxy:
            context_kwargs["proxy"] = proxy
        self.context = await self.browser.new_context(**context_kwargs)

        # Only worth it when the bytes are billed; the direct path stays as before.
        if proxy:
            await self._block_heavy_assets()

        self.page = await self.context.new_page()

    async def _cleanup_browser(self):
        if self._blocked_assets and any(self._blocked_assets.values()):
            print(
                "[Fake APEC] Assets blocked: "
                + ", ".join(f"{n} {k}" for k, n in self._blocked_assets.items() if n)
            )
        self._blocked_assets = None

        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            print(f"[Fake APEC] Cleanup warning: {e}")

        # After the browser is gone, and in its own try: a leaked Xvfb per
        # request would pile up on a long-lived Service instance.
        if self._x_display is not None:
            try:
                self._x_display.stop()
            except Exception as e:
                print(f"[Fake APEC] Xvfb stop warning: {e}")
            self._x_display = None

    async def _handle_apec_pagination(self, page_number: int) -> bool:
        """
        Attempts to navigate to the next page.
        Returns True if navigation succeeded, False if last page reached.
        """
        try:
            next_button = self.page.locator(
                'nav[aria-label="Page navigation"] li[class="page-item"]'
            )

            if await next_button.count() == 0:
                print("🔚 [APEC] No next button found. Reached last page.")
                return False

            # APEC empties/hides the anchor when on the last page.
            # NOTE: this MUST be awaited — `locator.count()` is a coroutine, and the
            # old code's `if butt_child.count() == 0` was always falsy (a coroutine
            # object is never == 0), so last-page detection via this branch never fired.
            butt_child = next_button.locator("a")
            if await butt_child.count() == 0:
                print("🔚 [APEC] Next button inactive. Reached last page.")
                return False

            print(f"➡️ [APEC] Moving to page {page_number + 1}...")
            await next_button.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
            await self._handle_cookies()
            return True

        except Exception as e:
            print(f"⚠️ [APEC] Pagination error: {e}")
            return False

    async def _handle_cookies(self):
        try:
            await self.page.wait_for_selector(
                'button:has-text("Refuser tous les cookies")',
                state='attached',
                timeout=10000
            )
            btn = self.page.locator('button:has-text("Refuser tous les cookies")')
            if await btn.count() > 0:
                await btn.click()
        except Exception:
            pass

    async def get_raw_job_data(self, card: Locator):
            """
            Extraction selectors mirror the production worker exactly:
            title  -> h2.card-title
            company -> p.card-offer__company
            location -> the <li> containing the localisation icon
            """
            try:
                raw_title = await card.locator('h2[class="card-title"]').inner_text()
                raw_company = await card.locator('p[class="card-offer__company"]').first.inner_text()
                raw_location = await card.locator('li:has(img[alt="localisation"])').inner_text()

                return raw_company.strip(), raw_title.strip(), raw_location.strip()

            except Exception:
                print("[Fake APEC]: Offer details not found, skipping card.")
                return None, None, None

    async def search_jobs(
        self, query: str, max_results: int = 10, user_id: str = "unknown"
    ) -> List[JobSnippet]:
        print(f"[Fake APEC] Searching for '{query}' (target: {max_results})...")
        results: List[JobSnippet] = []

        # Guard: the master may pass a quota of 0 for a disabled board.
        if max_results <= 0:
            return results

        try:
            self._init_snapshots()
            await self._init_browser(user_id)

            await self.page.goto(self.base_url, wait_until="domcontentloaded")
            await self._debug_snapshot("after_goto")

            for i in range(3):
                try:
                    await self._handle_cookies()
                    await self.page.wait_for_selector('a[title="Candidats"]', timeout=15000)
                    break
                except Exception as e:
                    await self._debug_snapshot(f"candidats_not_found_try{i+1}")
                    if i == 2:
                        print(f"[Fake APEC] Retry {i+1}/3: 'Candidats' link not found, refreshing...")
                        print(f"\nError message: {e}")
                        
           
            
            for i in range(3):
                try:
                    await self.page.locator('a[title="Candidats"]').click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.page.wait_for_selector('input[id="keywords"]', timeout=15000)
                    break
                except Exception as e:
                    await self._debug_snapshot(f"candidats_click_failed_try{i+1}")
                    if i == 2:
                        print(f"""[Fake APEC] Retry {i+1}/3: 'Candidats' link click failed, 
                                refreshing...\nError message: {e}""")     
                        

            # Search
            await self._debug_snapshot("before_keywords_fill")
            await self.page.locator('input[id="keywords"]').fill(query)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(3000)

            # Wait for results
            for i in range(3):
                try:
                    await self.page.wait_for_selector(self.CARD_SELECTOR, timeout=20000)
                    break
                except Exception as e:
                    if i == 2:
                        print(f"[Fake APEC] Retry {i+1}/3: No results found\nError message: {e}")
                        await self._debug_snapshot("results_timeout")
                        return results

            await self._handle_cookies()

            page_number = 1
            max_pages = 10  # Safety limit

            while len(results) < max_results and page_number <= max_pages:
                print(f"[Fake APEC] Processing page {page_number}...")

                cards = self.page.locator(self.CARD_SELECTOR)
                count = await cards.count()
                print(f"[Fake APEC] Found {count} cards on page {page_number}")

                # Save current results-page URL so we can navigate back after each detail view.
                result_url = self.page.url

                for i in range(count):
                    if len(results) >= max_results:
                        print(f"[Fake APEC] Target of {max_results} jobs reached")
                        break

                    try:
                        # Re-locate each iteration to avoid stale element handles.
                        cards = self.page.locator(self.CARD_SELECTOR)
                        card = cards.nth(i)

                        # Extract basic info before clicking in.
                        company, title, location = await self.get_raw_job_data(card)

                        if not company or not title:
                            continue

                        # Click into the offer to capture its canonical detail URL.
                        await card.click()
                        await self.page.wait_for_load_state("domcontentloaded")
                        await self.page.wait_for_timeout(1000)

                        url = self.page.url

                        job = JobSnippet(
                            job_title=title,
                            company_name=company,
                            location=location,
                            description_snippet='',
                            job_board=JobBoard.APEC,
                            url=url
                        )

                        results.append(job)
                        print(f"[Fake APEC] ✓ Scraped ({len(results)}/{max_results}): {title}")

                        # Navigate back to the results list.
                        await self.page.goto(result_url, wait_until="domcontentloaded")
                        await self.page.wait_for_timeout(1500)

                    except Exception as e:
                        print(f"[Fake APEC] Error on card {i}: {e}")
                        try:
                            await self.page.goto(result_url, wait_until="domcontentloaded")
                            await self.page.wait_for_timeout(1000)
                        except Exception:
                            pass
                        continue

                # Paginate only if we still need more results.
                if len(results) < max_results:
                    if not await self._handle_apec_pagination(page_number):
                        break
                    page_number += 1
                else:
                    print("[Fake APEC] Target reached, stopping pagination")
                    break

        except Exception as e:
            print(f"[Fake APEC] Fatal error: {e}")
            await self._debug_snapshot("fatal")

        finally:
            await self._cleanup_browser()

        return results