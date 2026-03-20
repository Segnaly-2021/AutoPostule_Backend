# auto_apply_app/infrastructures/agent/fake_workers/fake_wttj_worker.py
# import asyncio
from typing import List, Optional
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


class FakeWTTJWorker:
    """
    Search-only worker for Welcome to the Jungle.
    No login, no LLM, no applications - just scraping.
    """
    
    def __init__(self):
        self.base_url = "https://www.welcometothejungle.com/fr"
        
        # Runtime browser instances (cleaned up after each search)
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
    
    async def _init_browser(self):
        """Initialize headless browser."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,  # Always headless for free tier
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
    
    async def _cleanup_browser(self):
        """Close browser resources."""
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
        """Remove cookie overlays."""
        try:
            await self.page.wait_for_selector('#axeptio_overlay', state='attached', timeout=3000)
            await self.page.evaluate("""() => {
                document.querySelectorAll('#axeptio_overlay, .axeptio_mount').forEach(el => el.remove());
            }""")
        except Exception:
            pass  # No cookies, continue


    async def _handle_wttj_pagination(self, page_number: int) -> bool:
        """
        Attempts to navigate to the next page.
        Returns True if navigation succeeded, False if last page reached.
        """
        try:
            next_button = self.page.locator(
                'nav[aria-label="Pagination"] a:has(svg[alt="Right"])'
            )

            if await next_button.count() == 0:
                print("🔚 [WTTJ] No next button found. Reached last page.")
                return False

            is_disabled = await next_button.get_attribute("aria-disabled")
            if is_disabled == "true":
                print("🔚 [WTTJ] Next button disabled. Reached last page.")
                return False

            print(f"➡️ [WTTJ] Moving to page {page_number + 1}...")
            
            await next_button.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_timeout(2000)
            await self._handle_cookies()
            return True

        except Exception as e:
            print(f"⚠️ [WTTJ] Pagination error: {e}")
            return False

    # --- HELPER: Get Job attributes ---
    async def _get_job_attribute(self, card: Locator, selector: str, default_value: str=None):
        try:           
            locator = card.locator(selector).first
            return await locator.inner_text(timeout=2000)
        except Exception:
            return default_value
    
    async def _get_text_safe(self, selector: str, default: str = "") -> str:
        """Safely extract text from selector."""    
        try:
            await self.page.wait_for_selector(selector, state='attached', timeout=3000)
            text = await self.page.locator(selector).first.inner_text()
            return text.strip()
        except Exception:
            return default
    
    async def get_raw_job_data(self, card: Locator):
        try:
            # Title — div[role="mark"] inside h2, inner_text() merges the <em> fragments
            raw_title = await card.locator('h2 div[role="mark"]').inner_text()

            # Company — the div wrapping the logo img is sibling of the company span
            # so we target: a[role="link"] + div > span (first span inside that div)
            raw_company = await card.locator('a[role="link"] + div > span').inner_text()

            # Location — the inner span of the span right after svg[alt="Location"]
            raw_location = await card.locator(
                'svg[alt="Location"] ~ span span'
            ).inner_text()

            return raw_company.strip(), raw_title.strip(), raw_location.strip()

        except Exception:
            print("    ⚠️ Offer details not found, skipping card.")
            return None, None, None
    
    async def search_jobs(self, query: str, max_results: int = 10) -> List[JobSnippet]:
        """
        Search WTTJ and return job snippets.
        
        Args:
            query: Job title search term
            max_results: Max number of jobs to scrape (default 10)
        
        Returns:
            List of JobSnippet objects
        """
        print(f"[Fake WTTJ] Searching for '{query}'...")
        results = []
        
        try:
            await self._init_browser()
            
            # Navigate to homepage
            await self.page.goto(self.base_url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(3000)
            await self._handle_cookies()
            
            # Perform search
            await self.page.get_by_test_id("homepage-search-field-query").fill(query)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(3000)
            await self._handle_cookies()
            
            # Wait for results
            try:
                await self.page.wait_for_selector(
                    '[data-testid="search-results-list-item-wrapper"]',
                    timeout=5000
                )
            except Exception:
                print("[Fake WTTJ] No results found")
                return results
            
            # Pagination loop
            page_number = 1
            max_pages = 5 
            
            while len(results) < max_results and page_number <= max_pages:
                print(f"[Fake WTTJ] Processing page {page_number}...")
                
                # Get cards on current page
                cards = self.page.locator('[data-testid="search-results-list-item-wrapper"]')
                count = await cards.count()
                
                # Save current page URL for navigation back
                #search_page_url = self.page.url
                
                for i in range(count):
                    # Stop if we've collected enough
                    if len(results) >= max_results:
                        break
                    
                    try:
                        # Re-locate to avoid stale elements
                        cards = self.page.locator('[data-testid="search-results-list-item-wrapper"]')
                        card = cards.nth(i)

                        # Extract data
                        url = self.page.url
                        company, title, location = await self.get_raw_job_data(card)

                        if not company or not title:
                            continue
                        
                        # Click to load details
                        await card.click()
                        await self.page.wait_for_load_state("domcontentloaded")
                        await self.page.wait_for_timeout(1000)
                        
                        
                        
                        # Create snippet object
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
                        
                        # Navigate back to search results
                        await self.page.go_back(wait_until="domcontentloaded")
                        await self._handle_cookies()
                        
                    except Exception as e:
                        print(f"[Fake WTTJ] Error on card {i}: {e}")
                        # Try to recover by going back to search page
                        try:
                            await self.page.go_back(wait_until="domcontentloaded")
                            await self._handle_cookies()
                        except Exception:
                            pass
                        continue
                
                # Check if we need more results and can paginate
                if len(results) < max_results:
                    if not await self._handle_wttj_pagination(page_number):
                        break
                    page_number += 1
                
        except Exception as e:
            print(f"[Fake WTTJ] Fatal error: {e}")
        
        finally:
            await self._cleanup_browser()
        
        return results






