# auto_apply_app/infrastructures/agent/fake_workers/fake_wttj_worker.py
#
# ⚠️ NOT CURRENTLY IN USE. The FakeMasterAgent routes the entire workload to
# APEC + HelloWork and does not launch this worker (its quota is fixed at 0).
# It is kept updated and import-safe so it can be re-enabled without a rewrite.
#
# Selectors here are synced with the production WelcomeToTheJungleWorker. The
# previous version of this file used selectors that matched NO elements in the
# real worker (wrong card testid, wrong title/company/location selectors, wrong
# pagination); those have been replaced wholesale.
#
# Design note: the production worker uses identity-based href bookkeeping because
# the logged-in "Nouveaux matchs" feed reshuffles as cards are opened. This fake
# worker is anonymous and sees a STATIC public results list, so it deliberately
# keeps simple index iteration instead of importing that complexity.

from typing import List, Optional
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright, Locator

from auto_apply_app.domain.entities.job_snippet import JobSnippet
from auto_apply_app.domain.value_objects import JobBoard


class FakeWTTJWorker:
    """Search-only worker for Welcome to the Jungle. No login, no LLM, no applications."""

    # Synced with production WelcomeToTheJungleWorker.
    CARD_SELECTOR = 'div[data-testid="job-list"] > div[data-testid^="job-card-"]'
    CARD_LINK = 'a[href*="/fr/companies/"][href*="/jobs/"]'

    def __init__(self):
        self.base_url = "https://www.welcometothejungle.com/fr"
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
            print(f"[Fake WTTJ] Cleanup warning: {e}")

    async def _handle_cookies(self):
        """Remove the Axeptio cookie overlay (same approach as the real worker)."""
        try:
            await self.page.wait_for_selector('#axeptio_overlay', state='attached', timeout=3000)
            await self.page.evaluate("""() => {
                document.querySelectorAll('#axeptio_overlay, .axeptio_mount').forEach(el => el.remove());
            }""")
        except Exception:
            pass

    async def _handle_wttj_pagination(self, page_number: int) -> bool:
        """
        Navigate to the next page. Mirrors the production pagination control:
        button[data-testid="job-list-pagination-arrow-next"] with is_disabled().
        """
        try:
            next_button = self.page.locator(
                'button[data-testid="job-list-pagination-arrow-next"]'
            )

            if await next_button.count() == 0:
                print("🔚 [WTTJ] No next button found. Reached last page.")
                return False

            if await next_button.is_disabled():
                print("🔚 [WTTJ] Next button disabled. Reached last page.")
                return False

            print(f"➡️ [WTTJ] Moving to page {page_number + 1}...")
            await next_button.click()
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
            await self._handle_cookies()
            return True

        except Exception as e:
            print(f"⚠️ [WTTJ] Pagination error: {e}")
            return False

    async def get_raw_job_data(self, card: Locator):
        """
        Extraction mirrors the production worker:
        - the desktop block (hidden at <lg) holds the visible title + company
        - title:   a[class*="heading-md-strong"]
        - company: p[class*="body-lg-strong"]
        - location lives in the tag row, scoped to the whole card
        """
        try:
            desktop_block = card.locator('div.hidden.lg\\:flex')

            raw_title = await desktop_block.locator('a[class*="heading-md-strong"]').first.inner_text()
            raw_company = await desktop_block.locator('p[class*="body-lg-strong"]').first.inner_text()
            raw_location = await card.locator(
                'div[data-testid="job-card-tag-location"] span'
            ).first.inner_text()

            return raw_company.strip(), raw_title.strip(), raw_location.strip()

        except Exception:
            print("    ⚠️ Offer details not found, skipping card.")
            return None, None, None

    async def search_jobs(self, query: str, max_results: int = 10) -> List[JobSnippet]:
        print(f"[Fake WTTJ] Searching for '{query}' (target: {max_results})...")
        results: List[JobSnippet] = []

        # Guard: the master passes a quota of 0 for WTTJ, so this returns immediately
        # without launching a browser. This is the intended path in the current setup.
        if max_results <= 0:
            return results

        try:
            await self._init_browser()

            await self.page.goto(self.base_url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(3000)
            await self._handle_cookies()

            # Perform search from the homepage field.
            await self.page.get_by_test_id("homepage-search-field-query").fill(query)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(3000)
            await self._handle_cookies()

            # Wait for results.
            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, timeout=5000)
            except Exception:
                print("[Fake WTTJ] No results found")
                return results

            page_number = 1
            max_pages = 5

            while len(results) < max_results and page_number <= max_pages:
                print(f"[Fake WTTJ] Processing page {page_number}...")

                cards = self.page.locator(self.CARD_SELECTOR)
                count = await cards.count()
                print(f"[Fake WTTJ] Found {count} cards on page {page_number}")

                result_url = self.page.url

                for i in range(count):
                    if len(results) >= max_results:
                        break

                    try:
                        # Re-locate each iteration to avoid stale element handles.
                        cards = self.page.locator(self.CARD_SELECTOR)
                        card = cards.nth(i)

                        company, title, location = await self.get_raw_job_data(card)
                        if not company or not title:
                            continue

                        # Read the offer URL from the card's anchor href. Unlike APEC/HW,
                        # WTTJ cards expose a usable job href (CARD_LINK), so we capture it
                        # directly and avoid a fragile click-in / nav-back round trip.
                        try:
                            href = await card.locator(self.CARD_LINK).first.get_attribute("href")
                        except Exception:
                            href = None

                        if not href:
                            continue

                        url = href if href.startswith("http") else f"https://www.welcometothejungle.com{href}"

                        job = JobSnippet(
                            job_title=title,
                            company_name=company,
                            location=location,
                            description_snippet='',
                            job_board=JobBoard.WTTJ,
                            url=url
                        )

                        results.append(job)
                        print(f"[Fake WTTJ] ✓ Scraped ({len(results)}/{max_results}): {title}")

                    except Exception as e:
                        print(f"[Fake WTTJ] Error on card {i}: {e}")
                        continue

                # Paginate only if we still need more results.
                if len(results) < max_results:
                    if not await self._handle_wttj_pagination(page_number):
                        break
                    page_number += 1
                else:
                    print("[Fake WTTJ] Target reached, stopping pagination")
                    break

        except Exception as e:
            print(f"[Fake WTTJ] Fatal error: {e}")

        finally:
            await self._cleanup_browser()

        return results