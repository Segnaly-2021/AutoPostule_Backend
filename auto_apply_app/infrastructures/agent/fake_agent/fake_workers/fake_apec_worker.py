# auto_apply_app/infrastructures/agent/fake_workers/fake_apec_worker.py

from typing import List, Optional
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright, Locator

from auto_apply_app.domain.entities.job_snippet import JobSnippet
from auto_apply_app.domain.value_objects import JobBoard


class FakeApecWorker:
    """Search-only worker for APEC."""
    
    def __init__(self):
        self.base_url = "https://www.apec.fr/"
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
    
    async def _init_browser(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
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

            # Check if disabled — APEC hides/empties the anchor when on last page
            butt_child = next_button.locator("a")
            if butt_child.count() == 0:
                print("🔚 [APEC] Next button inactive. Reached last page.")
                return False            

            print(f"➡️ [APEC] Moving to page {page_number + 1}...")
            await next_button.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_timeout(2000)
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
    
    async def _get_text_safe(self, selector: str, default: str = "") -> str:
        try:
            await self.page.wait_for_selector(selector, state='attached', timeout=3000)
            text = await self.page.locator(selector).first.inner_text()
            return text.strip()
        except Exception:
            return default
    
    async def _get_card_attribute(self, card, selector: str, default: str = "") -> str:
        """Extract text from a locator within a card."""
        try:
            text = await card.locator(selector).inner_text()
            return text.strip()
        except Exception:
            return default
        
        
    async def get_raw_job_data(self, card: Locator):
        try:
            raw_title = await card.locator('h2[class="card-title"]').inner_text()

            raw_company = await card.locator('p[class="card-offer__company"]').inner_text()

            raw_location = await card.locator('li:has(img[alt="localisation"])').inner_text() 

            return raw_company.strip(), raw_title.strip(), raw_location.strip()

        except Exception:
            print("    ⚠️ Offer details not found, skipping card.")
            return None, None, None
        

    
    async def search_jobs(self, query: str, max_results: int = 10) -> List[JobSnippet]:
        print(f"[Fake APEC] Searching for '{query}'...")
        results = []
        
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
                await self.page.wait_for_selector(
                    'div[class="card card-offer mb-20 card--clickable card-offer--qualified"]',
                    timeout=5000
                )
            except Exception:
                print("[Fake APEC] No results found")
                return results
            
            await self._handle_cookies()
            
            # Pagination loop
            page_number = 1
            max_pages = 10  # Safety limit
            
            while len(results) < max_results and page_number <= max_pages:
                print(f"[Fake APEC] Processing page {page_number}...")
                
                # Get cards on current page
                card_selector = 'div[class="card card-offer mb-20 card--clickable card-offer--qualified"]'
                cards = self.page.locator(card_selector)
                count = await cards.count()
                print(f"[Fake APEC] Found {count} cards on page {page_number}")
                
                # Save current page URL
                result_url = self.page.url
                
                for i in range(count):
                    # Stop if we've collected enough
                    if len(results) >= max_results:
                        print(f"[Fake APEC] Target of {max_results} jobs reached")
                        break
                    
                    try:
                        # Re-locate to avoid stale elements
                        cards = self.page.locator(card_selector)
                        card = cards.nth(i)
                        
                        # Extract basic info before clicking
                        company, title, location = await self.get_raw_job_data(card)
                        
                        if not company or not title:
                            continue

                        # Click to get full details
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
                        
                        # Navigate back
                        await self.page.goto(result_url, wait_until="domcontentloaded")
                        await self.page.wait_for_timeout(1500)
                        
                    except Exception as e:
                        print(f"[Fake APEC] Error on card {i}: {e}")
                        # Try to recover
                        try:
                            await self.page.goto(result_url, wait_until="domcontentloaded")
                            await self.page.wait_for_timeout(1000)
                        except Exception:
                            pass
                        continue
                
                # Check if we need more results and can paginate
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