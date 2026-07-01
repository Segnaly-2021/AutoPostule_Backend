# auto_apply_app/infrastructures/agent/fake_workers/fake_apec_worker.py

from typing import List, Optional
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright, Locator

from auto_apply_app.domain.entities.job_snippet import JobSnippet
from auto_apply_app.domain.value_objects import JobBoard


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

    def __init__(self):
        self.base_url = "https://www.apec.fr/"
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

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
            print(f"[Fake APEC] Cleanup warning: {e}")

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
                timeout=3000
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
            print("    ⚠️ Offer details not found, skipping card.")
            return None, None, None

    async def search_jobs(self, query: str, max_results: int = 10) -> List[JobSnippet]:
        print(f"[Fake APEC] Searching for '{query}' (target: {max_results})...")
        results: List[JobSnippet] = []

        # Guard: the master may pass a quota of 0 for a disabled board.
        if max_results <= 0:
            return results

        try:
            await self._init_browser()

            await self.page.goto(self.base_url, wait_until="domcontentloaded")
            await self._handle_cookies()
            await self.page.locator('a[title="Candidats"]').click()
            await self.page.wait_for_timeout(2000)

            # Search
            await self.page.locator('input[id="keywords"]').fill(query)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(3000)

            # Wait for results
            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, timeout=5000)
            except Exception:
                print("[Fake APEC] No results found")
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

        finally:
            await self._cleanup_browser()

        return results