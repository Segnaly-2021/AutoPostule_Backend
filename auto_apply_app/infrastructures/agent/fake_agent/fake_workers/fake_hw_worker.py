# auto_apply_app/infrastructures/agent/fake_workers/fake_hw_worker.py

from typing import List, Optional
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright, Locator

from auto_apply_app.domain.entities.job_snippet import JobSnippet
from auto_apply_app.domain.value_objects import JobBoard


class FakeHWWorker:
    """Search-only worker for HelloWork."""
    
    def __init__(self):
        self.base_url = "https://www.hellowork.com/fr-fr/"
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
            print(f"[Fake HW] Cleanup warning: {e}")
    
    async def _handle_cookies(self):
        try:
            await self.page.wait_for_selector(
                '[id="hw-cc-notice-continue-without-accepting-btn"]',
                state="attached",
                timeout=3000
            )
            btn = self.page.locator('[id="hw-cc-notice-continue-without-accepting-btn"]')
            if await btn.count() > 0:
                await btn.click()
        except Exception:
            pass
    
    async def get_raw_job_data(self, card: Locator):
        try:
            anchor = card.locator('a[data-cy="offerTitle"]')
            
            # Two <p> tags inside the anchor's <h3>: first = title, second = company
            paragraphs = anchor.locator('p')
            
            raw_title = await paragraphs.nth(0).inner_text()
            raw_company = await paragraphs.nth(1).inner_text()
            raw_location = await card.locator('div[data-cy="localisationCard"]').inner_text()

            return raw_company.strip(), raw_title.strip(), raw_location.strip()

        except Exception:
            print("    ⚠️ Offer details not found, skipping card.")
            return None, None, None
        

    async def _handle_hw_pagination(self, page_number: int) -> bool:
        """
        Attempts to navigate to the next page.
        Returns True if navigation succeeded, False if last page reached.
        """
        try:
            next_button = self.page.locator(
                'button[name="p"]:has(svg > use[href$="#right"])'
            ).first

            if await next_button.count() == 0:
                print("🔚 [HW] No next button found. Reached last page.")
                return False

            is_disabled = await next_button.get_attribute("aria-disabled")
            if is_disabled == "true":
                print("🔚 [HW] Next button disabled. Reached last page.")
                return False

            print(f"➡️ [HW] Moving to page {page_number + 1}...")            
            await next_button.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_timeout(3000)
            await self._handle_cookies()
            return True

        except Exception as e:
            print(f"⚠️ [HW] Pagination error: {e}")
            return False
        


    async def search_jobs(self, query: str, max_results: int = 10) -> List[JobSnippet]:
        print(f"[Fake HW] Searching for '{query}'...")
        results = []
        
        try:
            await self._init_browser()
            
            await self.page.goto(self.base_url, wait_until="domcontentloaded")
            await self._handle_cookies()
            
            # Search
            await self.page.locator('input[id="k"]').fill(query)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(3000)
            
            # Wait for results
            try:
                await self.page.wait_for_selector(
                    '[data-id-storage-target="item"]',
                    timeout=5000
                )
            except Exception:
                print("[Fake HW] No results found")
                return results
            
            # Pagination loop
            page_number = 1
            max_pages = 20  # Safety limit
            
            while len(results) < max_results and page_number <= max_pages:
                print(f"[Fake HW] Processing page {page_number}...")
                
                # Get cards on current page
                cards = self.page.locator('[data-id-storage-target="item"]')
                count = await cards.count()
                                
                # Save current page URL
                search_url = self.page.url
                
                for i in range(count):
                    # Stop if we've collected enough
                    if len(results) >= max_results:            
                        break
                    
                    try:
                        # Re-locate to avoid stale elements
                        cards = self.page.locator('[data-id-storage-target="item"]')
                        card = cards.nth(i)

                        company, title, location  = await self.get_raw_job_data(card)                        
                        if not company or not title:
                            continue
                        
                        await card.click()
                        await self.page.wait_for_load_state("domcontentloaded")
                        await self.page.wait_for_timeout(1000)                        
                        url = self.page.url
                        
                        
                        job = JobSnippet(
                            job_title=title,
                            company_name=company,
                            location=location,
                            description_snippet='',
                            job_board=JobBoard.HELLOWORK,
                            url=url
                        )
                        
                        results.append(job)
                        print(f"[Fake HW] ✓ Scraped ({len(results)}/{max_results}): {title}")
                        
                        # Navigate back to search results
                        await self.page.goto(search_url, wait_until="domcontentloaded")
                        await self.page.wait_for_timeout(1500)
                        
                    except Exception as e:
                        print(f"[Fake HW] Error on card {i}: {e}")
                        # Try to recover
                        try:
                            await self.page.goto(search_url, wait_until="domcontentloaded")
                            await self.page.wait_for_timeout(1000)
                        except Exception:
                            pass
                        continue
                
                # Check if we need more results and can paginate
                if len(results) < max_results:

                    # 🚨 PAGINATION (From Fake Agent)
                    if not await self._handle_hw_pagination(page_number):
                        break
                    page_number += 1
                    
                else:
                    print("[Fake HW] Target reached, stopping pagination")
                    break
        
        except Exception as e:
            print(f"[Fake HW] Fatal error: {e}")
        
        finally:
            await self._cleanup_browser()
        
        return results