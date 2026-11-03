# auto_apply_app/infrastructures/agent/fake_agent/fake_workers/fake_indeed_worker.py

from typing import List, Optional
from urllib.parse import quote_plus

from playwright.async_api import (
  async_playwright, 
  Page, 
  Browser, 
  BrowserContext, 
  Playwright, 
  Locator
)

from auto_apply_app.domain.entities.job_snippet import JobSnippet
from auto_apply_app.domain.value_objects import JobBoard


class FakeIndeedWorker:
    """
    Search-only worker for Indeed (fr.indeed.com).

    Indeed's CSS classes are generated hashes (e.g. `css-pygyny`) that change between
    deploys, so the card list (`card_wrapper`) and the card selector (`card_selector`)
    are resolved at runtime from the stable `#mosaic-provider-jobcards` id.

    Every card already holds title, company, location, snippet and the job key
    (`data-jk`), so nothing is clicked: the URL is built as /viewjob?jk=<jk>.

    Indeed plants hidden decoy cards (aria-hidden, tabindex=-1, rel=nofollow);
    those are skipped.
    """

    RESULTS_ROOT = "#mosaic-provider-jobcards"
    JOB_ANCHOR = "h3.jobTitle a[data-jk]"
    SNIPPET_MAX_LEN = 300

    def __init__(self):
        self.base_url = "https://fr.indeed.com/"
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Resolved at runtime (hashed class names change between deploys).
        self.card_wrapper: Optional[str] = None
        self.card_selector: Optional[str] = None

    # ------------------------------------------------------------------ browser

    async def _init_browser(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage']
        )
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

    async def _cleanup_browser(self):
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
            print(f"[Fake Indeed] Cleanup warning: {e}")

    async def _handle_cookies(self):
        """Indeed uses OneTrust. Prefer reject, fall back to accept. Fail-soft."""
        for selector in ("#onetrust-reject-all-handler", "#onetrust-accept-btn-handler"):
            try:
                btn = self.page.locator(selector)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click(timeout=3000)
                    return
            except Exception:
                pass

    async def _close_popups(self):
        """Indeed sometimes opens a modal (e.g. job alerts) after paginating. Fail-soft."""
        for selector in ('button[aria-label="close"]', 'button[aria-label="Fermer"]', '[class*="icl-CloseButton"]'):
            try:
                btn = self.page.locator(selector)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click(timeout=2000)
                    return
            except Exception:
                pass

    # ---------------------------------------------------------- dynamic selectors

    async def _resolve_card_wrapper(self) -> Optional[str]:
        """
        Find the <ul> under #mosaic-provider-jobcards whose direct <li> children hold
        job anchors, and return a selector for it. Ignores the nested <ul>s inside
        cards (metadata, snippet bullets, "more links").
        """
        try:
            ul_class = await self.page.evaluate(
                """([root, anchor]) => {
                    const rootEl = document.querySelector(root);
                    if (!rootEl) return null;
                    for (const ul of rootEl.querySelectorAll('ul')) {
                        const hasJobLi = Array.from(ul.children).some(
                            li => li.tagName === 'LI' && li.querySelector(anchor)
                        );
                        if (hasJobLi) return ul.classList.length ? ul.classList[0] : '';
                    }
                    return null;
                }""",
                [self.RESULTS_ROOT, self.JOB_ANCHOR],
            )
        except Exception as e:
            print(f"[Fake Indeed] Could not resolve card wrapper: {e}")
            ul_class = None

        if ul_class is None:
            self.card_wrapper = None
        elif ul_class:
            self.card_wrapper = f"{self.RESULTS_ROOT} ul.{ul_class}"
        else:
            self.card_wrapper = f"{self.RESULTS_ROOT} ul:has(> li {self.JOB_ANCHOR})"

        print(f"[Fake Indeed] card_wrapper = {self.card_wrapper}")
        return self.card_wrapper

    async def _resolve_card_selector(self) -> Optional[str]:
        """Job <li>s only: the ad / empty-zone <li>s share the class but have no job anchor."""
        if not self.card_wrapper:
            self.card_selector = None
        else:
            self.card_selector = f"{self.card_wrapper} > li:has({self.JOB_ANCHOR})"
        print(f"[Fake Indeed] card_selector = {self.card_selector}")
        return self.card_selector

    async def _resolve_selectors(self) -> bool:
        await self._resolve_card_wrapper()
        await self._resolve_card_selector()
        return self.card_selector is not None

    # --------------------------------------------------------------- extraction

    async def _decoy_reason(self, card: Locator) -> Optional[str]:
        """
        Return why a card looks like a hidden honeypot, or None for a real card.
        Strong signals only: an aria-hidden card, or an anchor that is BOTH
        unfocusable (tabindex=-1) and nofollow, like the decoy seen in the wild.
        """
        try:
            return await card.evaluate(
                """(li, anchor) => {
                    const outline = li.querySelector('.cardOutline');
                    if (outline && outline.getAttribute('aria-hidden') === 'true') return 'aria-hidden card';
                    const a = li.querySelector(anchor);
                    if (!a) return 'no job anchor';
                    const unfocusable = a.getAttribute('tabindex') === '-1';
                    const nofollow = (a.getAttribute('rel') || '').includes('nofollow');
                    if (unfocusable && nofollow) return 'anchor tabindex=-1 + rel=nofollow';
                    return null;
                }""",
                self.JOB_ANCHOR,
            )
        except Exception as e:
            return f"evaluate failed: {e}"

    async def get_raw_job_data(self, card: Locator) -> Optional[dict]:
        """Read jk, title, company, location and snippet from one card in a single call."""
        try:
            data = await card.evaluate(
                """(li, anchor) => {
                    const text = (el) => el ? el.innerText.replace(/\\s+/g, ' ').trim() : '';
                    const a = li.querySelector(anchor);
                    if (!a) return null;
                    const titleSpan = a.querySelector('span[title]');
                    return {
                        jk: a.getAttribute('data-jk'),
                        title: titleSpan ? titleSpan.getAttribute('title').trim() : text(a),
                        company: text(li.querySelector('[data-testid="company-name"]')),
                        location: text(li.querySelector('[data-testid="text-location"]')),
                        snippet: text(li.querySelector('[data-testid="belowJobSnippet"]')),
                    };
                }""",
                self.JOB_ANCHOR,
            )
        except Exception as e:
            print(f"[Fake Indeed]   ⚠️   Offer details not found, skipping card:\n{e}")
            return None

        if not data or not data.get("jk") or not data.get("title"):
            return None

        snippet = data.get("snippet") or ""
        if len(snippet) > self.SNIPPET_MAX_LEN:
            data["snippet"] = snippet[: self.SNIPPET_MAX_LEN].rstrip() + "…"
        return data

    # --------------------------------------------------------------- pagination

    async def _handle_indeed_pagination(self, page_number: int) -> bool:
        """
        Attempts to navigate to the next page.
        Returns True if navigation succeeded, False if last page reached.
        """
        try:
            next_button = self.page.locator('a[data-testid="pagination-page-next"]').first

            if await next_button.count() == 0:
                print("🔚 [Fake Indeed] No next button found. Reached last page.")
                return False

            print(f"➡️ [Fake Indeed] Moving to page {page_number + 1}...")
            old_url = self.page.url
            await next_button.click()
            await self.page.wait_for_url(lambda u: u != old_url, timeout=15000)
            await self.page.wait_for_load_state("domcontentloaded")

            # Anonymous users are redirected to a sign-in page from page 2 on
            # (secure.indeed.com/auth?...branding=page-two-signin).
            if "secure.indeed.com/auth" in self.page.url or "signin" in self.page.url:
                print("🔒 [Fake Indeed] Page 2+ requires sign-in. Stopping pagination.")
                return False

            await self._close_popups()
            await self.page.wait_for_selector(
                f"{self.RESULTS_ROOT} {self.JOB_ANCHOR}", state="attached", timeout=15000
            )
            await self._close_popups()
            await self._handle_cookies()

            # Hashed classes can differ between pages: resolve again.
            return await self._resolve_selectors()

        except Exception as e:
            print(f"⚠️ [Fake Indeed] Pagination error: {e}")
            return False

    # ------------------------------------------------------------------- search

    async def search_jobs(self, query: str, max_results: int = 10) -> List[JobSnippet]:
        print(f"[Fake Indeed] Searching for '{query}' (target: {max_results})...")
        results: List[JobSnippet] = []

        # Guard: the master may pass a quota of 0 for a disabled board.
        if max_results <= 0:
            return results

        try:
            await self._init_browser()
            await self.page.goto(self.base_url, wait_until="domcontentloaded")

            # Search via the home page input; fall back to the /jobs URL.
            search_input_found = False
            for attempt in range(3):
                try:
                    await self._handle_cookies()
                    await self.page.wait_for_selector('#text-input-what', timeout=5000)
                    search_input_found = True
                    break
                except Exception as e:
                    print(f"[Fake Indeed] Search input not found, retrying ({attempt + 1}/3)...")
                    print(f"\nError message: {e}")
                    await self.page.goto(self.base_url, wait_until="domcontentloaded")

            submitted = False
            if search_input_found:
                await self.page.locator('#text-input-what').fill(query)
                # Indeed pre-fills "Où" from the IP's geolocation, which narrows the
                # results to that city and pads them with unrelated jobs. Clear it.
                where = self.page.locator('#text-input-where')
                if await where.count() > 0:
                    await where.fill("")
                await self.page.locator('#text-input-what').press("Enter")
                try:
                    await self.page.wait_for_url("**/jobs?**", timeout=15000)
                    submitted = True
                except Exception:
                    print("[Fake Indeed] Search did not navigate to /jobs")

            if not submitted:
                print("[Fake Indeed] Falling back to direct search URL")
                await self.page.goto(
                    f"{self.base_url}jobs?q={quote_plus(query)}", wait_until="domcontentloaded"
                )

            # Wait for results
            for attempt in range(3):
                try:
                    await self.page.wait_for_selector(
                        f"{self.RESULTS_ROOT} {self.JOB_ANCHOR}", state="attached", timeout=15000
                    )
                    break
                except Exception as e:
                    if attempt == 2:
                        print("[Fake Indeed] No results found")
                        print(f"\nError message: {e}")
                        return results

            await self._handle_cookies()
            await self._close_popups()

            if not await self._resolve_selectors():
                print("[Fake Indeed] Could not locate the job card list")
                return results

            seen_jk: set = set()
            page_number = 1
            max_pages = 20  # Safety limit

            while len(results) < max_results and page_number <= max_pages:
                print(f"[Fake Indeed] Processing page {page_number}...")

                cards = self.page.locator(self.card_selector)
                count = await cards.count()
                print(f"[Fake Indeed] Found {count} cards on page {page_number}")

                for i in range(count):
                    if len(results) >= max_results:
                        break

                    try:
                        card = cards.nth(i)

                        reason = await self._decoy_reason(card)
                        if reason:
                            print(f"[Fake Indeed] Skipping decoy card {i}: {reason}")
                            continue

                        data = await self.get_raw_job_data(card)
                        if not data or data["jk"] in seen_jk:
                            continue
                        seen_jk.add(data["jk"])

                        job = JobSnippet(
                            job_title=data["title"],
                            company_name=data["company"],
                            location=data["location"],
                            description_snippet=data["snippet"],
                            job_board=JobBoard.INDEED,
                            url=f"{self.base_url}viewjob?jk={data['jk']}",
                        )

                        results.append(job)
                        print(f"[Fake Indeed] ✓ Scraped ({len(results)}/{max_results}): {data['title']}")

                    except Exception as e:
                        print(f"[Fake Indeed] Error on card {i}: {e}")
                        continue

                # Paginate only if we still need more results.
                if len(results) < max_results:
                    if not await self._handle_indeed_pagination(page_number):
                        break
                    page_number += 1
                else:
                    print("[Fake Indeed] Target reached, stopping pagination")
                    break

        except Exception as e:
            print(f"[Fake Indeed] Fatal error: {e}")

        finally:
            await self._cleanup_browser()

        return results
