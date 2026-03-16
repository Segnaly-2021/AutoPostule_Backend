# auto_apply_app/infrastructure/agent/workers/wttj_worker.py
import hashlib
import os
import json
import asyncio
import pdfplumber
from typing import Optional
from langgraph.graph import StateGraph, END
from playwright.async_api import Locator, async_playwright, Page, Browser, BrowserContext, Playwright
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage


# 1. imports from Domain
from auto_apply_app.domain.value_objects import ApplicationStatus, JobBoard, ClientType, ContractType
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.entities.user_preferences import UserPreferences

# 2. Imports from Infrastructure
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort 
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort 
from auto_apply_app.application.use_cases.agent_use_cases import GetIgnoredHashesUseCase




class WelcomeToTheJungleWorker:
    # 1. INJECTION: Only what the "Hands" need.
    def __init__(self, 
                 get_ignored_hashes: GetIgnoredHashesUseCase, 
                 encryption_service: EncryptionServicePort,
                 file_storage: FileStoragePort
                ):
        
        # Static Dependencies
        self.get_ignored_hashes = get_ignored_hashes
        self.encryption_service = encryption_service
        self.base_url = "https://www.welcometothejungle.com/fr"
        self.file_storage = file_storage
        
        # Runtime State (Lazy Initialization)
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        


    # --- HELPER: Dynamic Brain ---
    def _get_llm(self, preferences: UserPreferences) -> BaseChatModel:
        """Creates LLM instance based on runtime user preferences."""
        return ChatGoogleGenerativeAI(
                api_key=self.api_key,
                model="gemini-3-pro-preview",
                temperature=preferences.llm_temperature
            )
    

    # --- HELPER: Fast Hash Generation ---
    def _generate_fast_hash(self, company_name: str, job_title: str, user_id: str) -> str:
        """
        Mimics the JobOffer._generate_fingerprint domain logic for memory-efficient deduplication.
        Bypasses the need to instantiate a full JobOffer entity.
        """
        raw_string = f"""
            {str(company_name).lower()}_{str(job_title).lower()}_
            {JobBoard.APEC.name}_{str(user_id)}

        """
        return hashlib.md5(raw_string.encode()).hexdigest()

        
    # --- HELPER: Session Management ---
    def _get_session_file_path(self, user_id: str) -> str:
        """Generates a local file path for the user's WTTJ session."""
        directory = os.path.join(os.getcwd(), "tmp", "sessions")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{user_id}_wttj_session.json")

    async def _save_auth_state(self, user_id: str):
        """Extracts cookies/local storage and saves to JSON."""
        if self.context:
            path = self._get_session_file_path(user_id)
            await self.context.storage_state(path=path)
            print(f"🔒 [WTTJ] Session saved securely for user {user_id}")

    def _get_auth_state_path(self, user_id: str) -> str | None:
        """Checks if a session file exists and returns the path."""
        path = self._get_session_file_path(user_id)
        if os.path.exists(path):
            return path
        return None

    # --- HELPER: Cookie Handling ---
    async def _handle_cookies(self):
        print("Checking for Axeptio cookies...")
        try:
            await self.page.wait_for_selector('#axeptio_overlay', state='attached', timeout=5000)
            count = await self.page.evaluate("""() => {
                const overlays = document.querySelectorAll('#axeptio_overlay, .axeptio_mount');
                let removed = 0;
                overlays.forEach(el => {
                    el.remove();
                    removed++;
                });
                return removed;
            }""")
            print(f" Nuked {count} Axeptio overlay(s) from the DOM.")
        except Exception:
            # If we get here, it means the ID was never found after 5s.
            # That's GOOD! It means no popup appeared. We continue safely.
            print("info: No cookie popup detected (or already gone).")
    
    async def _handle_wttj_application_modal(self):
        try:
            modal = self.page.locator('[data-testid="modals"]')
            
            if not await modal.is_visible():
                return
            
            # Click "Peut-être plus tard" by its exact text content
            later_button = modal.get_by_text("Peut-être plus tard", exact=True)
            
            if await later_button.is_visible():
                await later_button.click()
                await self.page.wait_for_timeout(500)
                print("✅ [WTTJ] Dismissed application modal.")
            else:
                # Fallback: remove the overlay and dialog from the DOM entirely
                await self.page.evaluate("""
                    const portal = document.getElementById('portal/:rcm:');
                    if (portal) portal.remove();
                """)
                print("✅ [WTTJ] Removed application modal via DOM.")
                
        except Exception as e:
            print(f"⚠️ [WTTJ] Could not dismiss application modal: {e}")


    # --- HELPER: Force Cleanup ---
    async def force_cleanup(self):
        """
        Emergency cleanup called by kill button.
        Forcefully closes all browser resources.
        """
        print("🛑 Force cleanup initiated")
        try:
            if self.page:
                await self.page.close()
                print("  ✓ Page closed")
        except Exception as e:
            print(f"  ⚠️ Page close error: {e}")
        
        try:
            if self.context:
                await self.context.close()
                print("  ✓ Context closed")
        except Exception as e:
            print(f"  ⚠️ Context close error: {e}")
        
        try:
            if self.browser:
                await self.browser.close()
                print("  ✓ Browser closed")
        except Exception as e:
            print(f"  ⚠️ Browser close error: {e}")
        
        try:
            if self.playwright:
                await self.playwright.stop()
                print("  ✓ Playwright stopped")
        except Exception as e:
            print(f"  ⚠️ Playwright stop error: {e}")
        
        print("✅ Force cleanup complete")


    # --- HELPER: Get Job attributes ---
    async def _get_job_attribute(self, selector: str, default_value: str = None):
        try:
            # 1. Wait for attachment rather than full visibility
            # 2. Add a specific timeout so it doesn't hang for 30s
            await self.page.wait_for_selector(selector, state='attached', timeout=5000)
            
            # 3. Use .first in case the selector matches multiple items
            text = await self.page.locator(selector).first.inner_text()
            
            return text.strip() 
        except Exception:
            # Debug: Uncomment this to see why it's actually failing
            # print(f"Log: Failed to get {selector}: {e}")
            return default_value


    # --- HELPER: Resume Extraction ---
    def _extract_resume(self, resume_path: str) -> str:
        text = ""
        try:
            with pdfplumber.open(resume_path) as pdf:
                for p in pdf.pages:
                    text += p.extract_text() + "\n"
        except Exception as e:
            print(f"Error reading resume: {e}")
        return text
    

    async def _handle_wttj_close_modal(self):
        try:
            modal = self.page.locator('[data-testid="apply-form-modal"]')
            
            if not await modal.is_visible():
                return
            
            close_button = modal.locator('[data-dialog-dismiss][title="Close"]')
            
            if await close_button.is_visible():
                await close_button.click()
                await self.page.wait_for_timeout(500)
                print("✅ [WTTJ] Closed apply form modal.")
            else:
                await self.page.evaluate("""
                    const modal = document.querySelector('[data-testid="apply-form-modal"]');
                    if (modal) modal.closest('[role="dialog"]').remove();
                """)
                print("✅ [WTTJ] Removed apply form modal via DOM.")
                
        except Exception as e:
            print(f"⚠️ [WTTJ] Could not close apply form modal: {e}")


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
    
    # --- HELPER: Apply Search Filters ---
    async def _apply_filters(self, contract_types: list[ContractType], min_salary: int):
        """
        Interacts with the filter modal to set contract types and salary minimums.
        """
        try:
            # 1. Open the Filter Modal
            await self.page.locator('button[id="jobs-search-filter-all"]').click()
            await self.page.wait_for_selector('div[data-testid="filter-modal"]', state="visible")

            # 2. Handle Contract Types (Checkboxes)
            # WTTJ usually lists these as labels or spans containing text like 'CDI', 'Freelance', etc.
            for contract in contract_types:
                try:
                    # We look for a label that contains the specific contract text    
                    checkbox = self.page.locator(f"input[id='jobs-search-all-modal-contract-{str(contract.name).lower()}']")
                    print(f"{checkbox}")
                    if await checkbox.count() > 0:
                        print(f"  Found checkbox for contract type: {str(contract.name)}")
                        if await checkbox.get_attribute("aria-checked") == 'false':
                            await checkbox.click()
                            print(f"  ✓ Checked: {str(contract.value)}")
                except Exception as e:
                    print(f"  ⚠️  Could not select contract type '{str(contract.value)}': {e}")
            
            

            # 3. Handle Salary (Radio Buttons)
            # We map the integer input to the specific IDs provided in your HTML snippet
            salary_id = f"jobs-search-search-all-modal-salary-{min_salary}+"
            salary_radio = self.page.locator(f"div[id='{salary_id}']")
            
            if await salary_radio.count() > 0:
                # We click the parent label or the radio itself to ensure interaction
                #await self.page.locator('input[data-testid="include-unknown-checkbox"]').first.click()
                await salary_radio.click(force=True)
                print(f"  ✓ Selected Salary: ≥ {min_salary}€")
            else:
                print(f"  ⚠️ Salary option '{min_salary}+' not found in modal.")

            # 4. Close the Modal
            # Using the specific title selector you requested
            search_button = self.page.locator('button[id="jobs-search-modal-search-button"]')
            if await search_button.count() > 0:
                await search_button.click()
            
            # Brief wait for the modal to vanish and search to refresh
            await self.page.wait_for_timeout(1500)

        except Exception as e:
            print(f"❌ Error applying filters: {e}")



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
        



    # --- NODE 1: Start Session ---
    async def start_session(self, state: JobApplicationState):
        print(f"--- [WTTJ] Starting session for {state['user'].firstname} ---")
        
        # 1. Read Runtime Config
        preferences = state["preferences"]
        
        # 2. Initialize Browser
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless= not preferences.browser_headless, # ✅ Runtime Value
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        
        return {"status": "session_started"}
    
    # --- NODE 1 Bis: Boot & Inject Session (Submit Track) ---
    async def start_session_with_auth(self, state: JobApplicationState):
        """Used by the SUBMIT track to boot directly into an authenticated browser."""
        print("--- [WTTJ] Booting Browser (Session Injection) ---")
        user_id = str(state["user"].id)
        
        try:
    
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless= not state["preferences"].browser_headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            session_path = self._get_auth_state_path(user_id)
            
            if session_path:
                print(f"🔓 Found saved session for user {user_id}. Injecting cookies...")
                self.context = await self.browser.new_context(storage_state=session_path)
            else:
                print("⚠ No session found. Booting fresh context...")
                self.context = await self.browser.new_context()

            self.page = await self.context.new_page()
            
            # WTTJ usually needs you to hit the base URL to hydrate the cookies properly
            await self.page.goto(self.base_url, wait_until="domcontentloaded")
            await self._handle_cookies()
            
            return {
                "current_url": self.page.url, 
                "is_logged_in": True if session_path else False
            }
            
        except Exception as e:
            print(f"Browser Auth Initialization Error: {e}")
            return {"error": f"Failed to initialize WTTJ browser with session: {str(e)}"}
    
    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        print("--- [WTTJ] Navigating ---")
        try:
            await self.page.goto(self.base_url)
            await self._handle_cookies()           

        except Exception as e:
            print(f"Nav Error: {e}")
            
        return {"status": "on_homepage"}
    

    # --- NODE 3: Login (UPDATED FOR V2) ---
    async def request_login(self, state: JobApplicationState):
        if state.get("is_logged_in"):
            return {"status": "already_logged_in"}

        prefs = state["preferences"]
        creds = state.get("credentials")
        user_id = str(state["user"].id) # 🚨 Get User ID
        
        print("--- [WTTJ] Requesting Login ---")

        await self._handle_cookies() 
        
        # STRATEGY 1: Full Automation
        if prefs.is_full_automation and creds["wttj"]:
            print("🔐 Full Automation: Attempting auto-login...")
            try:
                await self.page.get_by_test_id("not-logged-visible-login-button").click()
                await self.page.wait_for_selector('input[id="email_login"]', state="visible", timeout=5000)

                login_plain = await self.encryption_service.decrypt(creds["wttj"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["wttj"].password_encrypted)

                await self.page.locator('input[id="email_login"]').fill(login_plain)
                await self.page.locator('input[id="password"]').fill(pass_plain)

                submit_btn = self.page.locator('[data-testid="login-button-submit"]') 
                if await submit_btn.count() == 0:
                     submit_btn = self.page.locator('button[type="submit"]')

                await submit_btn.click()                
                
                # Verify Success
                await self.page.wait_for_selector('button[data-testid="header-user-link-signout"]', state="attached", timeout=10000)
                await self.page.get_by_test_id("menu-jobs").click(timeout=5000)

                print("✅ Auto-login successful")
                
                # 🚨 SAVE SESSION COOKIES
                await self._save_auth_state(user_id)
                
                return {"is_logged_in": True, "status": "login_complete"}

            except Exception as e:
                print(f"❌ Auto-login failed: {e}")
                # 🚨 RETURN ERROR DICT (Don't raise exception)
                return {"error": "Failed to log into Welcome to the Jungle. Please check your credentials."}

        # STRATEGY 2: Semi-Automation (Manual)
        else:
            try:
                await self.page.get_by_test_id("not-logged-visible-login-button").click()
                print("⚠ ACTION REQUIRED: Manual login required (waiting 90s)...")
                await asyncio.sleep(90)
                    
                await self.page.get_by_test_id("menu-jobs").click(timeout=5000)
                
                # 🚨 SAVE SESSION COOKIES
                await self._save_auth_state(user_id)
                
                return {"is_logged_in": True, "status": "login_step_complete"}
            except Exception as e:
                print(f"Login Error: {e}")
                return {"error": "Manual login timed out. We didn't detect a successful login."}




    # --- NODE 4: Search (Interaction Based) ---
    async def search_jobs(self, state: JobApplicationState):
        search_entity = state["job_search"]
        job_title = search_entity.job_title
        
        # Extract filter preferences from state
        contract_types = getattr(search_entity, 'contract_types', [])
        min_salary = getattr(search_entity, 'min_salary', 0)

        print(f"--- [WTTJ] Searching for: {job_title} ---")
        
        try:
            # 1. Fill the job title (Using your EXACT original selector)
            await self.page.get_by_test_id("jobs-home-search-field-query").fill(job_title)
            
            # 2. APPLY FILTERS 
            if contract_types or min_salary > 0:
                await self._apply_filters(contract_types, min_salary)

            # 3. Finalize search (Uncommented!)
            #await self.page.keyboard.press("Enter")
            
            # 4. Wait for results
            await self.page.wait_for_timeout(3000)
            await self._handle_cookies()
            
            return {
                "status": "on search page",
                "current_url": self.page.url
            }
            
        except Exception as e:
            print(f"Search Error: {e}")
            # 🚨 CIRCUIT BREAKER: Safely tell the Master the search failed
            return {"error": f"Failed to execute search for '{job_title}' on Welcome to the Jungle."}


    # --- NODE 5: Scrape Jobs (WTTJ Integrated & Paginated) ---
    async def get_matched_jobs(self, state: JobApplicationState):
        print("--- [WTTJ] Scraping Jobs ---")
        
        user_id = state["user"].id
        search_id = state["job_search"].id
        found_job_entities = []
        
        # 🚨 V2 REQUIREMENT: Get the target limit from the Master
        worker_job_limit = state.get("worker_job_limit", 10) 
        
        # 🚨 V2 REQUIREMENT: Fetch Ignored Hashes
        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=14)
        if not hash_result.is_success:
            print(f"⚠️ Warning: Could not fetch ignored hashes: {hash_result.error.message}")
            ignored_hashes = set()
        else:
            ignored_hashes = hash_result.value
            
        print(f"🛡️ Loaded {len(ignored_hashes)} ignored job hashes to prevent duplicates.")
        print(f"🎯 Target: Scraping up to {worker_job_limit} new WTTJ jobs.")

        # Pagination Setup
        page_number = 1
        max_pages = 20  
        
        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:
                print(f"📄 [WTTJ] Processing Page {page_number}...")
                
                # Wait for the cards list to populate on the current page
                try:
                    await self.page.wait_for_selector('li[data-testid="search-results-list-item-wrapper"]', timeout=5000)
                except Exception:
                    print(f"⚠️  No results found on page {page_number}.")
                    if page_number == 1:
                        return {"error": "No job postings appeared for your search on Welcome to the Jungle."}
                    break

                cards = self.page.get_by_test_id("search-results-list-item-wrapper")
                count = await cards.count()
                
                for i in range(count):
                    # Hard stop if limit reached
                    if len(found_job_entities) >= worker_job_limit:
                        break
                    
                    print(f"  -> Processing card {i+1}/{count} (Page {page_number})")
                    
                    try:
                        # 1. Re-locate to avoid stale elements after coming back
                        cards = self.page.get_by_test_id("search-results-list-item-wrapper")
                        card = cards.nth(i)


                        raw_company, raw_title, raw_location = await self.get_raw_job_data(card)
                        print(f"---[WTTJ WORKER] RAW DATA---\nCompany: {raw_company},\nTitle: {raw_title},\nLocation: {raw_location}")
                        if not raw_company or not raw_title:
                            print("    ⚠️  Missing title or company, skipping card.")
                            continue

                        # 🚨 V2 REQUIREMENT: Hash Gate (Post-Click, Pre-Scrape)
                        fast_hash = self._generate_fast_hash(raw_company, raw_title, str(user_id))
                        if fast_hash in ignored_hashes:
                            print(f"     ⏩ Skipping duplicate: {raw_title} at {raw_company}")
                            continue
                        
                        # 2. Click to load details (WTTJ requires this for reliable data)
                        await card.click()
                        await self.page.wait_for_load_state("domcontentloaded")
                        await self.page.wait_for_timeout(1000)
                        
                        # 3. Extract Basic Data
                        current_url = self.page.url           

                        # 4. Extract Job Description
                        try:
                            desc_el = self.page.locator("div#the-position-section")
                            if await desc_el.count() == 0: 
                                desc_el = self.page.locator("main")
                            job_desc = await desc_el.inner_text()
                        except Exception:
                            job_desc = ""

                        # 5. Check Apply Button (Internal vs External Popup Check)
                        apply_btn = self.page.locator('[data-testid="job_bottom-button-apply"]').first
                        
                        if await apply_btn.count() > 0:
                            try:
                                try:
                                    await self.page.wait_for_selector('a[data-testid="job_bottom-button-apply"] svg[alt="ExternalLink"]', timeout=3000)
                                    print(f"     ❌ External form detected (Ignoring): {current_url}")
                                    await self.page.go_back(wait_until="domcontentloaded")
                                    await self._handle_cookies()
                                    continue                                    
                        
                                except Exception:
                                    raise    

                                # # CASE A: Popup Opened -> External Site (Workday, Greenhouse, etc.)
                                # async with self.page.expect_popup(timeout=7000) as popup_info:
                                #     await apply_btn.click()
                                
                                # new_page = await popup_info.value
                                # print(f"     ❌ External form detected (Ignoring): {new_page.url}")
                                # await new_page.close()
                                # await self._handle_wttj_application_modal()
                                
                            except Exception:
                                # CASE B: No Popup -> Internal WTTJ Form!
                                print(f"     ✅ Internal form confirmed: {raw_title}")
                                
                                # Create Domain Entity
                                offer = JobOffer(
                                    url=current_url,
                                    form_url=current_url, # For WTTJ, the apply form is usually on the same page
                                    search_id=search_id,
                                    user_id=state["user"].id,
                                    company_name=raw_company,
                                    job_title=raw_title,
                                    location=raw_location,
                                    job_board=JobBoard.WTTJ,
                                    status=ApplicationStatus.FOUND,                                
                                    job_desc=job_desc
                                )                                
                                found_job_entities.append(offer)
                                await self._handle_wttj_close_modal()
                                print(f"     📦 Current batch size: {len(found_job_entities)}/{worker_job_limit}")

                        # 6. Navigate back to search results
                        await self.page.go_back(wait_until="domcontentloaded")
                        await self._handle_cookies()
                        
                    except Exception as e:
                        print(f"     ⚠️ Error on card {i}: {e}")
                        # Attempt recovery
                        try:
                            await self.page.go_back(wait_until="domcontentloaded")
                            await self._handle_cookies()
                        except Exception:
                            pass
                        continue
                
                # --- END OF PAGE LOGIC ---
                
                if len(found_job_entities) >= worker_job_limit:
                    break
                    
                # Handle Pagination
                if not await self._handle_wttj_pagination(page_number):
                    break
                page_number += 1

                
        
        except Exception as e:
            print(f"Fatal Scraping Error: {e}")
            return {"error": "A critical error occurred while scanning Welcome to the Jungle."}
        
        if not found_job_entities:
            print("⚠️ Scanned jobs, but none were valid internal applications.")
            return {"error": "We couldn't find any new internal WTTJ jobs for your criteria today."}

        print(f"🎉 WTTJ Scraping Complete! Handing {len(found_job_entities)} jobs back to Master.")
        return {"found_raw_offers": found_job_entities}


    # --- NODE 6: Analyze (Optimized Flow) ---
    async def analyze_jobs(self, state: JobApplicationState):
        print("--- [WTTJ] Analyzing & Ranking Jobs ---")
        
        user_id = state["user"].id
        search_id = state["job_search"].id
        raw_offers = state["found_raw_offers"]

        # 1. OPTIMIZATION: Filter & Persist Raw Jobs FIRST
        # We call the tool immediately. It saves new jobs as "FOUND" and ignores existing ones.
        # It returns the list of jobs that were actually processed (the new ones).
        print("🔍 Checking DB for duplicates...")
        pre_process_result = await self.results_processor.execute(user_id, search_id, raw_offers)

        if not pre_process_result.is_success:
            print(f"DB Error during pre-check: {pre_process_result.error.message}")
            return {"found_raw_offers": []}

        # The tool returns ONLY the new/valid jobs we should work on
        jobs_to_analyze = pre_process_result.value
        
        if not jobs_to_analyze:
            print("All jobs were duplicates. Skipping LLM.")
            return {"found_raw_offers": []}

        print(f"Optimization: Analyzing {len(jobs_to_analyze)} new jobs (filtered from {len(raw_offers)})")

        # 2. Prepare Resume
        resume_path = state["user"].resume_path
        resume_text = await asyncio.to_thread(self._extract_resume, resume_path) 

        # ✅ Get User-Specific LLM
        llm = self._get_llm(state["preferences"])

        processed_offers = []

        # 3. LLM Loop (Only on new jobs)
        for offer in jobs_to_analyze:
            print(f"🤖 Analyzing: {offer.job_title}")
            try:
                # A. Navigation & Scrape
                await self.page.goto(offer.url, wait_until="domcontentloaded")
                await self._handle_cookies()
                
                try:
                    # Optimized selector
                    desc_el = self.page.locator("div#the-position-section")
                    print(f"[Debug] job desc within div#the-position-section: {await desc_el.count()}")
                    if await desc_el.count() == 0: 
                        desc_el = self.page.locator("main")
                    job_desc = await desc_el.inner_text()
                except Exception:
                    print("[Debug] Error when fetching job desc within div#the-position-section")
                    job_desc = ""

                # B. Validation Check
                if len(job_desc) < 50:
                    print("⏩ Description too short, skipping.")
                    continue

                # C. LLM Call
                system_message = SystemMessage(
                """
                    You're an excellent AI assistant that take a job description and a resume as input and 
                    generate a custom cover letter(in french, you should write the cover letter in 
                    french) and a ranking number from 1 to 10 describing how well the job matches 
                    the resume, with 1 meaning low matching and 10 the highest rank.

                    Task for a job application assistant:
                
                    Given a job description and resume, generate:
                    1. A cover letter in French (max tokens: 350)
                    2. A ranking (1-10) indicating job fit
                    
                    CRITICAL: Return ONLY valid JSON with no markdown formatting, no code fences, no explanation.
                    Your response must start with { and end with }.
                    
                    Format:
                    {
                    "cover_letter": "your cover letter text here",
                    "ranking": 3
                    }
                    
                    Do NOT wrap the JSON in ```json or ``` markers.
                """
                )

                prompt = HumanMessage(content=f"""
                Job Description: {job_desc}
                Resume: {resume_text}        
                
                """)
                print("[Debug] Sending LLM request...")
                response = await llm.ainvoke([system_message, prompt])

                clean_json = response.content[0]["text"]
                data = json.loads(clean_json)   
                cover_letter = data.get("cover_letter", "")
                ranking = int(data.get("ranking", 5))
                print(f"LLM Response - Ranking: {ranking} - Cover Letter Length: {len(cover_letter)}")
                
                # E. Update Entity
                offer.cover_letter = cover_letter
                offer.ranking = int(ranking)
                
                # [CRITICAL UPDATE] Set status to GENERATED (Draft)
                offer.status = ApplicationStatus.GENERATED
                processed_offers.append(offer)

            except Exception as e:
                print(f"Analysis Error {offer.url}: {e}")
                continue

        if processed_offers:
            print(f"💾 Saving {len(processed_offers)} drafts for review...")
            save_result = await self.results_saver.execute(processed_offers)
            if not save_result.is_success:
                print(f"⚠ Error saving drafts: {save_result.error.message}")

        # Return the processed offers to the State
        return {
            "processed_offers": processed_offers, 
            "phase": "review_pending" # [NEW] Signal the phase
        }
    
    # --- [NEW] ROUTER LOGIC ---
    def check_review_requirements(self, state: JobApplicationState):
        """
        The Gatekeeper Function.
        Decides if we proceed to submission or pause for review.
        """
        subscription = state.get("subscription") # We added this to State in Phase 1
        
        # Safety check
        if not subscription:
            print("⚠ No subscription found in state, defaulting to BASIC (Auto-Submit)")
            return  "submit"

        print(f"--- Router Checking: User is {subscription.account_type} ---")

        if subscription.account_type == ClientType.PREMIUM:
            # Premium users pause here to review drafts
            return "wait_for_review"
        
        # Basic/Free users go straight to submission
        return "submit"
    

   # --- NODE 7: Submit (Stateless) ---
    async def submit_applications(self, state: JobApplicationState):
        print("--- [WTTJ] Submitting Applications ---")
        
        # 1. Get Inputs from State's "Inbox"
        jobs_to_process = state.get("processed_offers", [])
        user = state["user"] 

        # 🚨 V2 REQUIREMENT: Filter to make sure this worker ONLY applies to WTTJ jobs
        # that have been explicitly APPROVED by the user (or auto-approved for basic).
        wttj_jobs = [job for job in jobs_to_process if job.job_board == JobBoard.WTTJ and job.status == ApplicationStatus.APPROVED]

        if not wttj_jobs:
            print("No approved WTTJ jobs in submission queue.")
            return {"status": "no_wttj_jobs_to_submit"}

        successful_submissions = []
        i = 0
        for offer in wttj_jobs:
            print(f"📝 Applying to: {offer.job_title} ({i+1}/{len(wttj_jobs)})")
            try:
                # A. Navigate to Job
                await self.page.goto(offer.url, wait_until="domcontentloaded")
                await self.page.wait_for_timeout(2000)
                await self._handle_cookies()
                
                # B. Open Form Drawer
                await self.page.wait_for_selector('[data-testid="job_bottom-button-apply"]', state="attached") 
                apply_btn = self.page.locator('[data-testid="job_bottom-button-apply"]').first
                
                if await apply_btn.count() == 0:
                    print(f"❌ Apply button not found for {offer.url}")
                    continue
                    
                await apply_btn.click()
                await self.page.wait_for_timeout(1000)
                await self._handle_cookies()
                
                # C. Fill Form
                await self.page.get_by_test_id("apply-form-field-firstname").fill(user.firstname)
                await self.page.get_by_test_id("apply-form-field-lastname").fill(user.lastname)
                
                if user.phone_number: 
                    await self.page.get_by_test_id("apply-form-field-phone").fill(user.phone_number)
                
                # Handle potentially missing attributes safely
                current_pos = getattr(user, 'current_position', "")
                if current_pos:
                    await self.page.get_by_test_id("apply-form-field-subtitle").fill(current_pos) 

                # D. File Upload
                if user.resume_path:
                    print("⬇️ Downloading resume from cloud to RAM...")
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    
                    # Fallback if the user entity doesn't have the new human name yet
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"

                    # 🚨 Playwright uploads securely from RAM! No temp files!
                    await self.page.get_by_test_id("apply-form-field-resume").set_input_files({
                        "name": human_name,
                        "mimeType": "application/pdf",
                        "buffer": resume_bytes
                    })
                
                # E. Cover Letter (From the JobOffer Entity, generated by Master's LLM)
                if offer.cover_letter:
                    await self.page.get_by_test_id("apply-form-field-cover_letter").fill(offer.cover_letter)
                
                # F. Consent Checkbox
                checkbox = self.page.locator('input[id="consent"]')
                if await checkbox.count() > 0 and not await checkbox.is_checked():
                    # Playwright sometimes struggles with styled checkboxes, clicking the label is often safer
                    await self.page.locator('label[for="consent"]').click()
                
                # G. Human verification
                print("⏳ Sleeping 30s for manual verification...")
                await asyncio.sleep(30) 
                
                # H. Submit 
                await self.page.wait_for_selector('[data-testid="apply-form-submit"]', state="attached")
                submit_btn = self.page.locator('[data-testid="apply-form-submit"]')
                
                if await submit_btn.is_visible():
                    await submit_btn.click() 
                    await self.page.wait_for_timeout(3000) # Wait for network confirmation
                    print(f"✅ Application submitted for {offer.job_title}")
                    
                    # Update domain entity state
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                else:
                    print(f"❌ Submit button not visible for {offer.job_title}.")

            except Exception as e:
                print(f"❌ Submission failed for {offer.url}: {e}")
            
            i += 1 

        if not successful_submissions:
            return {"error": "All WTTJ application attempts failed. Forms may have changed."}

        # 🚨 V2 REQUIREMENT: Return to Master's "Outbox"
        print(f"✅ Successfully submitted {len(successful_submissions)} WTTJ applications. Handing back to Master...")
        
        return {
            "submitted_offers": successful_submissions, 
            "status": "batch_complete"
        }
    

    # --- NODE 8: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        print("--- [APEC] Cleanup ---")
        # Reuse force_cleanup logic but as a step
        await self.force_cleanup()
        return {"status": "finished"}
    

    # --- HELPER: Error Router ---
    def check_for_errors(self, state: JobApplicationState) -> str:
        if state.get("error"):
            print(f"🛑 [WTTJ] Circuit Breaker Tripped: {state['error']}")
            return "error"
        return "continue"

    # --- ROUTING HELPER ---
    def route_action_intent(self, state: JobApplicationState):
        """The traffic cop that decides which track the Worker runs."""
        intent = state.get("action_intent", "SCRAPE") 
        
        if intent == "SUBMIT":
            print("🛤️ [WTTJ] Routing to SUBMIT track...")
            return "start_with_session"
        
        print("🛤️ [WTTJ] Routing to SCRAPE track...")
        return "start"

    def get_graph(self):
        workflow = StateGraph(JobApplicationState)
        
        # --- SCRAPE TRACK NODES ---
        workflow.add_node("start", self.start_session)
        workflow.add_node("nav", self.go_to_job_board)
        workflow.add_node("login", self.request_login)
        workflow.add_node("search", self.search_jobs)
        workflow.add_node("scrape", self.get_matched_jobs)
        
        # --- SUBMIT TRACK NODES ---
        workflow.add_node("start_with_session", self.start_session_with_auth) 
        workflow.add_node("submit", self.submit_applications)
        
        # --- SHARED CLEANUP NODE ---
        workflow.add_node("cleanup", self.cleanup)

        # 🚨 ENTRY POINT: Conditional Routing
        workflow.set_conditional_entry_point(
            self.route_action_intent,
            {
                "start": "start",                           # Track A: Scrape
                "start_with_session": "start_with_session"  # Track B: Submit
            }
        )
        
        # --- TRACK A EDGES (SCRAPE) ---
        workflow.add_conditional_edges("start", self.check_for_errors, {"error": "cleanup", "continue": "nav"})
        workflow.add_conditional_edges("nav", self.check_for_errors, {"error": "cleanup", "continue": "login"})
        workflow.add_conditional_edges("login", self.check_for_errors, {"error": "cleanup", "continue": "search"})
        workflow.add_conditional_edges("search", self.check_for_errors, {"error": "cleanup", "continue": "scrape"})
        workflow.add_conditional_edges("scrape", self.check_for_errors, {"error": "cleanup", "continue": "cleanup"}) 

        # --- TRACK B EDGES (SUBMIT) ---
        workflow.add_conditional_edges("start_with_session", self.check_for_errors, {"error": "cleanup", "continue": "submit"})
        workflow.add_conditional_edges("submit", self.check_for_errors, {"error": "cleanup", "continue": "cleanup"})
        
        # --- FINAL EXIT ---
        workflow.add_edge("cleanup", END)
        
        return workflow.compile()