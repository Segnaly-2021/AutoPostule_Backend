import asyncio
import hashlib
import json
from typing import Optional
from langgraph.graph import StateGraph, END
# from uuid import UUID
# from datetime import datetime
from langchain_core.messages import SystemMessage, HumanMessage
from playwright_stealth import Stealth
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright, Locator
import pdfplumber
import os


# --- DOMAIN IMPORTS ---
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import ContractType, JobBoard, ApplicationStatus

# --- INFRA & APP IMPORTS ---
from auto_apply_app.application.use_cases.agent_state_use_cases import GetAgentStateUseCase
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.application.use_cases.agent_use_cases import (
  GetIgnoredHashesUseCase
)
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort






class ApecWorker():
    # 1. INJECTION: Dependencies come from the Container
    def __init__(self, 
                 get_ignored_hashes: GetIgnoredHashesUseCase,
                 encryption_service: EncryptionServicePort,
                 file_storage: FileStoragePort,
                 get_agent_state: GetAgentStateUseCase
                ):
        
        # Static Dependencies
        self.get_ignored_hashes = get_ignored_hashes       
        self.encryption_service = encryption_service
        self.base_url = "https://www.apec.fr/"
        self.file_storage = file_storage
        self.get_agent_state = get_agent_state 
        

        # Runtime State (Lazy Initialization)
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Progress callback (set per-run by master)
        self._progress_callback = None
        self._source_name = "APEC"  # each worker defines its own name
        


    # --- HELPER: Universal Exit Router ---
    async def route_node_exit(self, state: JobApplicationState) -> str:
        """
        Generic router. Checks internal state for errors, AND checks the DB 
        for a user-triggered kill switch. Routes to cleanup if either is true.
        """
        # 1. Internal Error Check
        if state.get("error"):
            print(f"🛑 [APEC Worker] Circuit Breaker Tripped: {state['error']}")
            return "error"

        # 2. External Kill Switch Check
        try:
            user_id = state["user"].id
            state_result = await self.get_agent_state.execute(user_id)
            
            if state_result.is_success and state_result.value.is_shutdown:
                print("🛑 [APEC Worker] User Kill Switch Detected! Aborting gracefully...")
                return "error"
        except Exception as e:
            print(f"⚠️ [APEC] Failed to check DB for agent state: {e}")
            pass # Failsafe: Continue if the DB check fails so we don't randomly crash

        return "continue"
    

    # --- HELPER: Unified Explicit Emit (Workers) ---
    async def _emit(self, state: JobApplicationState, stage: str, status: str = "in_progress", error: str = None):
        """Emit progress to the frontend matching the universal schema."""
        if not self._progress_callback:
            return
        try:
            # Safely extract search_id from the state
            search_id = str(state["job_search"].id) if "job_search" in state else ""
            
            await self._progress_callback({
                "source": self._source_name.upper(), # e.g., "APEC"
                "stage": stage,                      # e.g., "Extracting Job Data"
                "node": self._source_name.lower(),   # 🚨 Using your class prop!
                "status": "error" if error else status,
                "error": error,
                "search_id": search_id
            })
        except Exception:
            pass  # never let a progress emit crash a worker node

    # In your Worker class
    def _generate_fast_hash(self, company_name: str, job_title: str, user_id: str) -> str:
        # 🚨 MIRROR THE DOMAIN LOGIC EXACTLY
        c = str(company_name).replace(" ", "").lower().strip()
        t = str(job_title).replace(" ", "").lower().strip()
        u = str(user_id).strip()
        b = "apec" # Or self.board_name
        
        raw_string = f"{c}_{t}_{b}_{u}"
        return hashlib.md5(raw_string.encode()).hexdigest()



    # --- HELPER: Session Management ---
    def _get_session_file_path(self, user_id: str) -> str:
        """Generates a local file path for the user's APEC session."""
        # For now, we store in a local /tmp/sessions folder. 
        # Easy to swap to GCP later!
        directory = os.path.join(os.getcwd(), "tmp", "sessions")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{user_id}_apec_session.json")

    async def _save_auth_state(self, user_id: str):
        """Extracts cookies/local storage and saves to JSON."""
        if self.context:
            path = self._get_session_file_path(user_id)
            await self.context.storage_state(path=path)
            print(f"🔒 [APEC] Session saved securely for user {user_id}")

    def _get_auth_state_path(self, user_id: str) -> str | None:
        """Checks if a session file exists and returns the path."""
        path = self._get_session_file_path(user_id)
        if os.path.exists(path):
            return path
        return None
    

    # --- HELPER: Handle Cookies ---
    async def _handle_cookies(self):
        try: 
            # Handle Cookies
            await self.page.wait_for_selector('button:has-text("Refuser tous les cookies")', state='attached', timeout=5000)
            cookie_btn = self.page.locator('button:has-text("Refuser tous les cookies")')
            if await cookie_btn.count() > 0:
                await cookie_btn.click()
        except Exception:
            print("No Cookies popup")

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
                self.page = None
                print("  ✓ Page closed")
        except Exception as e:
            print(f"  ⚠️ Page close error: {e}")
        
        try:
            if self.context:
                await self.context.close()
                self.context = None
                print("  ✓ Context closed")
        except Exception as e:
            print(f"  ⚠️ Context close error: {e}")
        
        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
                print("  ✓ Browser closed")
        except Exception as e:
            print(f"  ⚠️ Browser close error: {e}")
        
        try:
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
                print("  ✓ Playwright stopped")
        except Exception as e:
            print(f"  ⚠️ Playwright stop error: {e}")
        
        print("✅ Force cleanup complete")


    # --- HELPER: Resume Extraction ---
    def _extract_resume(self, resume_path: str) -> str:
        text = ""
        try:
            # Note: Ensure resume_path is a valid string/path before opening
            if not resume_path:
                return ""
            with pdfplumber.open(resume_path) as pdf:
                for p in pdf.pages:
                    text += p.extract_text() + "\n"
        except Exception as e:
            print(f"Error reading resume: {e}")
        return text

        

    async def get_raw_job_data(self, card: Locator):
        try:
            raw_title = await card.locator('h2[class="card-title"]').inner_text()

            raw_company = await card.locator('p[class="card-offer__company"]').inner_text()

            raw_location = await card.locator('li:has(img[alt="localisation"])').inner_text() 

            return raw_company.strip(), raw_title.strip(), raw_location.strip()

        except Exception:
            print("    ⚠️ Offer details not found, skipping card.")
            return None, None, None


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


    # --- HELPER: Apply APEC Advanced Filters ---
    async def _apply_filters(self, job_title: str, contract_types: list[ContractType], min_salary: int):
        """
        Navigates to the advanced search page and applies specific filters.
        """
        print(f"--- [APEC] Applying Advanced Filters for: {job_title} ---")
        try:
            # 1. Click on "Recherche avancée"
            await self.page.wait_for_selector('a[id="advancedSearch"]', state="visible", timeout=60000)
            await self.page.locator('a[id="advancedSearch"]').click()
            await self.page.wait_for_selector('input[id="keywords"]', state="visible")

            
            # 2. Fill the Job Title (Keywords field in advanced search)
            await self.page.locator('input[id="keywords"]').fill(job_title)

            # 3. Handle Contract Types (Select Dropdown)
            # Mapping based on your provided HTML values
            contract_map = {
                "CDI": "101888",
                "CDD": "101887",
                "Alternance": "20053",
                "Intérim": "101930",                
                "Stage": "597171"
            }

            if contract_types:
                # Note: APEC's native select often requires a value match
                # We take the first match for simplicity, or select multiple if supported
                for contract in contract_types:
                    val = contract_map.get(str(contract.value), None)
                    if val:
                        await self.page.select_option('select[formcontrolname="typesContrat"]', value=val)
                        print(f"  ✓ Contract selected: {contract}")
                        break # Standard selects usually handle one value easily

            # 4. Handle Salary (Input inside the apec-slider)
            if min_salary > 0:
                # The HTML shows a pull-left class for the lower bound input
                salary_input = self.page.locator('apec-slider input.pull-left')
                if await salary_input.count() > 0:
                    # Salary is usually in K€ (e.g., 45 for 45,000)
                    salary_k = str(min_salary // 1000) if min_salary >= 1000 else str(min_salary)
                    await salary_input.fill(salary_k)
                    # Trigger blur as indicated by the ng-blur attribute in your HTML
                    #await salary_input.dispatchEvent("blur")
                    print(f"  ✓ Min Salary set to: {salary_k}K€")

            # 5. Submit the search
            # await self.page.wait_for_timeout(2000)
            await self.page.locator('button:has-text("RECHERCHER")').click()
            
            # Wait for results page to load
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(3000)

        except Exception as e:
            print(f"❌ Error applying APEC filters: {e}")
    

    # --- HELPER: Get Job attributes ---
    async def _get_job_attribute(self, card: Locator, selector: str, default_value: str=None):
        try:           
            content = await card.locator(selector).inner_text()
            return content.strip()
        except Exception:
            return default_value
    

   # --- NODE 1: Start Session ---
    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser")  # ← fires immediately

        print(f"--- [APEC] Starting session for {state['user'].firstname} ---")
        preferences = state["preferences"]
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless= not preferences.browser_headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            # 1. Inject Human Fingerprint
            real_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            self.context = await self.browser.new_context(
                user_agent=real_user_agent
            )
            
            # 2. Apply V2 Stealth
            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)
            
            self.page = await self.context.new_page()
            
            return {}
        except Exception as e:
            print(f"Session Error: {e}")
            return {"error": "Failed to start the secure browsing session. Our servers might be under heavy load, please try again."}

    # --- NODE 1 Bis: Boot & Inject Session (Submit Track) ---
    async def start_session_with_auth(self, state: JobApplicationState):
        await self._emit(state, "Initializing Secure Browser") 

        """Used by the SUBMIT track to boot directly into an authenticated browser."""
        print("--- [APEC] Booting Browser (Session Injection) ---")
        user_id = str(state["user"].id)
     
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless= not state["preferences"].browser_headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            # 1. Inject Human Fingerprint
            real_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            
            # 🚨 Look for the saved session file
            session_path = self._get_auth_state_path(user_id)
            
            if session_path:
                print(f"🔓 Found saved session for user {user_id}. Injecting cookies...")
                self.context = await self.browser.new_context(
                    storage_state=session_path,
                    user_agent=real_user_agent,
                    device_scale_factor=1,
                    has_touch=False,
                    is_mobile=False
                )
            else:
                print("⚠ No session found. Booting fresh context...")
                self.context = await self.browser.new_context(
                    user_agent=real_user_agent,
                )

            # 2. Apply V2 Stealth
            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)

            self.page = await self.context.new_page()
            
            # Navigate to base URL to initialize the page object
            await self.page.goto(self.base_url, wait_until="networkidle", timeout=90000)
            await self.page.wait_for_timeout(20000)
            await self._handle_cookies()

            ### DUMMY SEARCH TO ESTABLISH SESSION CONTEXT (Important for APEC)
            search_entity = state["job_search"]
            job_title = search_entity.job_title
            
            # Get preferences from entity
            contract_types = getattr(search_entity, 'contract_types', [])
            min_salary = getattr(search_entity, 'min_salary', 0)

            print("🔎 [APEC] Dummy Starting Search Process")       
            try:
                # Call the filter helper which handles job title + filters
                await self.page.wait_for_timeout(30000)
                await self._apply_filters(job_title, contract_types, min_salary)    

                # Verify if results appeared
                try:
                    card_selector = 'div[class="card card-offer mb-20 card--clickable card-offer--qualified"]'
                    # 🚨 Keeping your exact selector and timeout
                    await self.page.wait_for_selector(card_selector, timeout=5000)
                    print("✅ Search results loaded successfully.")
                except Exception:
                    print("⚠️ No results found after applying filters.")
                    # 🚨 CIRCUIT BREAKER: Logical end (No results)

                return {} 
            except Exception as e:
                print(f"Search Error: {e}")            
                # 🚨 CIRCUIT BREAKER: Infrastructure/UI failure
            
        except Exception as e:
            print(f"Browser Auth Initialization Error: {e}")
            return {"error": f"Failed to initialize browser with session: {str(e)}"}


    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board")
        print("--- [APEC] Navigating to Board ---")
        try:
            # 1. Wait until the network is actually quiet
            await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)

            await self.page.wait_for_timeout(15000)
            
            # 2. Handle Cookies (important for HelloWork to stop blocking the view)
            await self._handle_cookies()  
            
            # 3. EXTRA SAFETY: Wait for a specific element that proves the page is ready
            # e.g., the search bar or the logo
            await self.page.wait_for_selector('li[id="header-monespace"]', state="visible", timeout=30000)
            
            return {}
        except Exception as e:
            print(f"Nav Error: {e}")
            return {"error": "Could not reach APEC.fr. The job board might be down or undergoing maintenance."}
            
        
    # --- NODE 3: Login ---
    async def request_login(self, state: JobApplicationState):
        await self._emit(state, "Authenticating")  # ← fires immediately
        
        prefs = state["preferences"]
        creds = state.get("credentials")

        # 🚨 [NEW] Extract user ID for the session file
        user_id = state["user"].id

        print("--- [APEC] Login Phase ---")

        if prefs.is_full_automation and creds["apec"]:
            print("🔐 Full Automation: Attempting auto-login...")

            login_plain = None
            pass_plain = None

            try:
                login_plain = await self.encryption_service.decrypt(creds["apec"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["apec"].password_encrypted)

                
                await self.page.wait_for_timeout(30000)
                try:
                    await self.page.wait_for_selector('li[id="header-monespace"]', state="visible")
                except Exception:
                    print("Reloading the page because the landing page button is absent")
                    await self.page.reload(wait_until="networkidle")
                
                
                await self.page.locator('li[id="header-monespace"]').click()
                await self.page.wait_for_timeout(5000)

                count = 0

                while count < 3:
                    try:
                        await self.page.wait_for_selector('input[id="emailid"]', state="visible")
                        await self.page.locator('input[id="emailid"]').fill(login_plain)
                        await self.page.locator('input[id="password"]').fill(pass_plain) 
                        await self.page.wait_for_timeout(5000)                   
                        await self.page.locator('button[type="submit"][value="Login"]').first.click()
                    except Exception as e:
                        count += 1
                        print(f"Login form error: {e}") 
                        continue
                    break                
                await self.page.wait_for_timeout(20000)      
                await self.page.goto(f"{self.base_url}candidat.html", wait_until="networkidle", timeout=90000)      
                print("✅ Auto-login successful")

                # 🚨 [NEW] Save the session cookies!
                await self._save_auth_state(str(user_id))

                #return {"is_logged_in": True, "status": "login_complete"}
                return {}

            except Exception as e:
                print(f"❌ Auto-login failed: {e}")
                # 🚨 Return error instead of raising exception
                return {"error": "Login failed. Please check your APEC credentials in your settings."}


            finally:
                # 🚨 THE MOST CRITICAL PART OF THE FILE
                # Since keep_first preserves the encrypted creds in the global state,
                # we MUST aggressively murder the decrypted plaintext variables in local RAM.
                if login_plain is not None:
                    del login_plain
                if pass_plain is not None:
                    del pass_plain
        else:
            print("👋 Semi-Automation: Requesting User Action")
            try:
                await self.page.locator('a[aria-label="Mon espace"]').click()
                print("⚠ ACTION REQUIRED: Please log in manually within 60 seconds...")
                
                await asyncio.sleep(90)
                
                await self.page.locator('[aria-label="menu"]').click()
                await self.page.locator('[href="/candidat.html"]').click()
                
                # 🚨 [NEW] Save the session cookies! (Works for manual login too!)
                await self._save_auth_state(user_id)

                #return {"is_logged_in": True, "status": "login_complete"}
                return {}
            except Exception as e:
                print(f"Manual Login Error: {e}")
                return {"error": "Login timed out. We didn't detect a successful login within the allowed time."}
    


    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state, "Searching for Jobs") 
        search_entity = state["job_search"]
        job_title = search_entity.job_title
        
        # Get preferences from entity
        contract_types = getattr(search_entity, 'contract_types', [])
        min_salary = getattr(search_entity, 'min_salary', 0)

        print("🔎 [APEC] Starting Search Process")       
        try:
            await self.page.wait_for_timeout(10000)
            await self._handle_cookies()
            # Call the filter helper which handles job title + filters
            await self._apply_filters(job_title, contract_types, min_salary)    

            # Verify if results appeared
            try:
                card_selector = 'div[class="card card-offer mb-20 card--clickable card-offer--qualified"]'
                # 🚨 Keeping your exact selector and timeout
                await self.page.wait_for_selector(card_selector, timeout=5000)
                print("✅ Search results loaded successfully.")
            except Exception:
                print("⚠️ No results found after applying filters.")
                # 🚨 CIRCUIT BREAKER: Logical end (No results)
                return {
                    "error": "No new matching jobs were found for this search today. We'll try again tomorrow!"
                }
                
        except Exception as e:
            print(f"Search Error: {e}")            
            # 🚨 CIRCUIT BREAKER: Infrastructure/UI failure
            return {
                "error": "We encountered an issue applying your search filters. The job board may have updated its layout."
            }
            
        # return {
        #     "status": "on search page",
        #     "current_url": self.page.url
        # }
        return {}



    async def nav_back(self, url: str):
        # 6. Navigate back to search results
        await self.page.goto(url, wait_until="networkidle")

        # 🚨 CRITICAL: Instead of just handling cookies, wait for the actual results to be visible
        try:
            # Wait for the specific job card container to reappear
            # This proves the "Bot Detection" or "Loading" overlay is gone.
            results_selector = 'div[class="card card-offer mb-20 card--clickable card-offer--qualified"]'  
            await self.page.wait_for_selector(results_selector, state="visible", timeout=10000)
            
            # Optional: A tiny human-like pause
            await asyncio.sleep(3) 
            
            await self._handle_cookies()

        except Exception:
            print("⚠️ Search results didn't reappear after going back. Possible bot detection or slow network.")
            # Fallback: Refresh the page entirely if the back button broke the state
            await self.page.reload(wait_until="networkidle")
            await self._handle_cookies()

    # --- NODE 5: Scrape Jobs (Integrated & Paginated) ---
    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data") 
        print("--- [APEC] Scraping Jobs ---")

        user_id = state["user"].id
        search_id = state["job_search"].id
        found_job_entities = []
        
        # 🚨 NEW: Get the target limit calculated by the Master
        worker_job_limit = 1 or state.get("worker_job_limit", 5) 
        
        # 🚨 REQUIREMENT 2: Fetch Ignored Hashes
        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=14)
        if not hash_result.is_success:
            print(f"⚠️ Warning: Could not fetch ignored hashes: {hash_result.error.message}")
            ignored_hashes = set()
        else:
            ignored_hashes = hash_result.value
            
        print(f"🛡️ Loaded {len(ignored_hashes)} ignored job hashes to prevent duplicates.")
        print(f"🎯 Target: Scraping up to {worker_job_limit} new jobs.")
        
        # APEC Card Selector
        card_selector = 'div[class="card card-offer mb-20 card--clickable card-offer--qualified"]'
        
        # 🚨 NEW: Pagination Setup
        page_number = 1
        max_pages = 20 # Safety limit to prevent infinite loops
        
        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:
                print(f"📄 [APEC] Processing Page {page_number}...")
                
                # Wait for list on current page
                cards = self.page.locator(card_selector)
                try:
                    await cards.first.wait_for(state="visible", timeout=5000)
                except Exception:
                    print(f"⚠️ No cards found on page {page_number}.")
                    # 🚨 UPDATE 1: Return an empty list instead of an error!
                    if page_number == 1:
                        print("📭 No job postings appeared on the results page.")
                        return {"found_raw_offers": []}
                    break

                count = await cards.count()
                
                # 🚨 UPDATE: Save current page URL so we return to the CORRECT page after clicking a job
                result_url = self.page.url 
                
                for i in range(count):
                    # 🚨 NEW: Hard stop if we hit the limit mid-page!
                    if len(found_job_entities) >= worker_job_limit:
                        print(f"✅ Reached target limit of {worker_job_limit} jobs.")
                        break

                    print(f"  -> Processing card {i+1}/{count} (Page {page_number})")
                    
                    # 1. Re-locate elements to avoid staleness
                    cards = self.page.locator(card_selector)
                    card = cards.nth(i)

                    # 2. Extract Metadata (Before Click - Safer/Faster)                            
                    raw_company, raw_title, raw_location  = await self.get_raw_job_data(card)
                    print(f"---[APEC WORKER] RAW DATA---\n[APEC Company]: {raw_company},\n[APEC Title]: {raw_title},\n[APEC Location]: {raw_location}")
                    if not raw_company or not raw_title:
                        print("    ⚠️ Missing title or company, skipping card.")
                        continue

                    # 🚨 REQUIREMENT 2: Early Exit Deduplication (Memory Optimized!)
                    fast_hash = self._generate_fast_hash(raw_company, raw_title, str(user_id))
                    
                    if fast_hash in ignored_hashes:
                        print(f"    ⏩ Skipping duplicate: {raw_title} at {raw_company}")
                        continue

                    # 3. Click & Load Detail View (in the side panel or new view)
                    await card.click()
                    await self.page.wait_for_load_state("networkidle")
                    await self.page.wait_for_timeout(10000)

                    # 🚨 REQUIREMENT 3: Scrape the Job Description
                    try:
                        desc_element = self.page.locator('div[class="col-lg-8 border-L"]')
                        if await desc_element.count() > 0:
                            job_desc = await desc_element.inner_text()
                        else:
                            job_desc = await self.page.locator("body").inner_text() 
                    except Exception:
                        job_desc = ""
                    
                    # 4. Check for "Easy Apply" Button
                    try:
                        await self.page.wait_for_selector('a[class="btn btn-primary ml-0"]', state="visible", timeout=5000)
                    except Exception:                
                        print("    ❌ External or already applied! Back to search result:")                  
                        await self.nav_back(result_url) 
                        continue 
                    
                    apply_btn = self.page.locator('a[class="btn btn-primary ml-0"]')
                        
                    if await apply_btn.count() > 0:
                        href = await apply_btn.get_attribute("href")
                        
                        # APEC Logic: "to=int" means internal application form
                        if href and "to=int" in href:
                            print(f"    ✅ Internal offer found: {raw_title}")
                            
                            full_offer_url = f"https://www.apec.fr{href}"
                            
                            await self.page.goto(full_offer_url, wait_until="networkidle")                        
                            await self.page.wait_for_timeout(5000)

                            try:
                                await self.page.wait_for_selector('button[title="Postuler"]', state="visible")
                            except Exception:
                                await self.nav_back(result_url)
                                continue

                            postule_btn = self.page.locator('button[title="Postuler"]')

                            if await postule_btn.count() > 0:
                                await postule_btn.click()
                                await self.page.wait_for_load_state("networkidle")
                                await self.page.wait_for_timeout(5000)

                                print("    ✅ Valid application form confirmed. Saving to batch.")   

                                # Create Domain Entity
                                offer = JobOffer(
                                    url=full_offer_url,
                                    form_url=self.page.url,
                                    search_id=search_id,
                                    user_id=state["user"].id,
                                    company_name=raw_company,
                                    job_title=raw_title,
                                    location=raw_location, 
                                    job_board=JobBoard.APEC,
                                    status=ApplicationStatus.FOUND,                                    
                                    job_desc=job_desc
                                )                                
                                
                                found_job_entities.append(offer)
                                print(f"    📦 Current batch size: {len(found_job_entities)}/{worker_job_limit}")
                    
                    # 5. Navigate back to the current page's search results
                    await self.nav_back(result_url)

                # --- END OF PAGE LOGIC ---
                
                # Double-check limit before attempting pagination
                if len(found_job_entities) >= worker_job_limit:
                    break
                    
                # 🚨 NEW: Attempt Pagination
                if not await self._handle_apec_pagination(page_number):
                    break
                page_number += 1

        except Exception as e:
            print(f"Scraping Error: {e}")
            return {"error": "A critical error occurred while scanning the job listings. We have safely halted the process."}

        # 🚨 UPDATE 2: Return an empty array here instead of an error!
        if not found_job_entities:
            print("⚠️ Scanned jobs across pages, but none were valid for auto-application.")
            return {"found_raw_offers": []}

        print(f"🎉 APEC Scraping Complete! Handing {len(found_job_entities)} jobs back to the Master Orchestrator.")
        return {"found_raw_offers": found_job_entities}
    


    # --- NODE 6: Analyze (Optimized Flow) ---
    async def analyze_jobs(self, state: JobApplicationState):
        print("--- [APEC] Analyzing Jobs with Gemini ---")
        
        user_id = state["user"].id
        search_id = state["job_search"].id
        raw_offers = state["found_raw_offers"]
        

        # 1. OPTIMIZATION: Filter & Persist Raw Jobs FIRST
        print("🔍 Checking DB for duplicates...")
        pre_process_result = await self.results_processor.execute(user_id, search_id, raw_offers)
        
        if not pre_process_result.is_success:
            print(f"DB Error during pre-check: {pre_process_result.error.message}")
            return {"error": "A database error occurred while checking your application history. Process halted to prevent duplicate applications."}

        jobs_to_analyze = pre_process_result.value
        
        if not jobs_to_analyze:
            print("All jobs were duplicates. Skipping LLM.")
            return {"error": "All jobs found in this run have already been processed or applied to previously."}

        # 🚨 [NEW] 2. Pre-Check AI Credits
        jobs_count = len(jobs_to_analyze)
        print(f"Optimization: Need to analyze {jobs_count} new jobs.")
        
        subscription = state.get("subscription")
        if not subscription:
             return {"error": "Could not verify your subscription status."}
             
        # Optional: You could just deduct at the end, but pre-checking prevents wasting LLM API 
        # calls if you already know they are completely out of credits.
        if not subscription.has_sufficient_credits():
             return {"error": "You are out of AI Credits for this billing cycle. Please upgrade or wait for your credits to replenish."}
             
        # If they have some credits, but not enough for all jobs, we just slice the list!
        if subscription.ai_credits_balance < jobs_count:
             print(f"⚠ Low balance! Only analyzing {subscription.ai_credits_balance} out of {jobs_count} jobs.")
             jobs_to_analyze = jobs_to_analyze[:subscription.ai_credits_balance]


        # 3. Prepare Resume
        resume_path = state["user"].resume_path
        resume_text = await asyncio.to_thread(self._extract_resume, resume_path)

        # ✅ Get User-Specific LLM
        llm = self._get_llm(state["preferences"])

        processed_offers = []

        # 4. LLM Loop
        for offer in jobs_to_analyze:
            print(f"🤖 Analyzing: {offer.job_title}")
            try:
                # A. Navigation
                await self.page.goto(offer.url, wait_until='networkidle')
                
                # B. Scrape Description (APEC Specific)
                try:
                    desc_element = self.page.locator('div[class="col-lg-8 border-L"]')
                    if await desc_element.count() > 0:
                        job_desc = await desc_element.inner_text()
                    else:
                        job_desc = await self.page.locator("body").inner_text() 
                except Exception:
                    job_desc = ""

                # C. Validation
                if len(job_desc) < 50:
                    print("⏩ Description too short, skipping.")
                    continue

                # D. LLM Call
                system_message = SystemMessage(
                """
                    You're an excellent AI assistant that take a job description and a resume as input and 
                    generate a custom cover letter(in french, you should write the cover letter in 
                    french) and a ranking number from 1 to 10 describing how well the job matches 
                    the resume, with 1 meaning low matching and 10 the highest rank.

                    Task for a job application assistant:
                
                    Given a job description and resume, generate:
                    1. A cover letter in French: It should be very simple and concise three to four sentences max. It's just a custom message to introduce the candidate and highlight relevant skills for the job.
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
                
                # E. Parsing
                try:
                    data = json.loads(response.content[0]["text"])
                    
                    offer.cover_letter = data.get("cover_letter", "")
                    offer.ranking = int(data.get("ranking", 5))

                    offer.status = ApplicationStatus.GENERATED
                    processed_offers.append(offer)
                except Exception as e:
                     print(f"JSON Parsing Error for job {offer.url}: {e}")

            except Exception as e:
                print(f"LLM Error for {offer.job_title}: {e}")

        # 🚨 CIRCUIT BREAKER: All LLM calls failed
        if not processed_offers:
            print("⚠️ Zero cover letters were successfully generated.")
            return {"error": "Our AI engine couldn't successfully generate valid cover letters for the extracted jobs. Please try again later."}

        # 🚨 [NEW] 5. Deduct Credits for Successful Generations
        if processed_offers:
            credits_to_deduct = len(processed_offers)
            print(f"💳 Deducting {credits_to_deduct} credits...")
            
            # Call the use case!
            billing_result = await self.consume_credits.execute(user_id=user_id, amount=credits_to_deduct)
            
            if not billing_result.is_success:
                print(f"⚠ Billing Error: {billing_result.error.message}")
                return {"error": "A billing error occurred while processing your AI credits. Please contact support."}
            
            # Update the state subscription object so downstream nodes have the fresh balance
            subscription.consume_credits(credits_to_deduct)
            
            # 6. Save Drafts
            print(f"💾 Saving {len(processed_offers)} drafts for review...")
            save_result = await self.results_saver.execute(processed_offers)
            if not save_result.is_success:
                print(f"⚠ Error saving drafts: {save_result.error.message}")

        # Return the processed offers to the State
        return {
            "processed_offers": processed_offers, 
            "phase": "review_pending"
        }
    
    # # --- [NEW] ROUTER LOGIC ---
    # def check_review_requirements(self, state: JobApplicationState):        
    #     subscription = state.get("subscription")       
    #     if not subscription:
    #         print("⚠ No subscription found in state, defaulting to BASIC (Auto-Submit)")
    #         return
    #     print(f"--- Router Checking: User is {subscription.account_type} ---")

    #     if subscription.account_type == ClientType.PREMIUM:
    #         return "wait_for_review"
    #     return "submit"

    # --- NODE 7: Submit & Save (APEC) ---
    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state, "Submitting Applications")
        print("--- [APEC] Submitting Applications ---")

        # 1. Get Inputs from State's "Inbox"
        jobs_to_process = state.get("processed_offers", [])
        user = state["user"] 

        # 🚨 SCHEMA FIX: Read the exact limit assigned by the Master Orchestrator
        assigned_submit_limit = state.get("worker_job_limit", 5) 

        # 🚨 V2 REQUIREMENT: Filter to make sure this worker ONLY applies to APEC jobs
        # that have been explicitly APPROVED by the user (or auto-approved for basic).
        apec_jobs = [job for job in jobs_to_process if job.job_board == JobBoard.APEC and job.status == ApplicationStatus.APPROVED]

        if not apec_jobs:
            print("No approved APEC jobs in submission queue.")
            return {"status": "no_apec_jobs_to_submit"}

        successful_submissions = []

        i = 0
        for offer in apec_jobs:
            # ==========================================
            # 🚨 THE FIX: HARD STOP
            # ==========================================
            if len(successful_submissions) >= assigned_submit_limit:
                print(f"🛑 [APEC] Reached assigned submission limit ({assigned_submit_limit}). Halting further submissions.")
                break
            # ==========================================

            print(f"📝 Applying to: {offer.job_title}: {i+1} out of {len(apec_jobs)}")
            try:
                # 🚨 V2 WAF BYPASS: Commit early, don't wait for networkidle
                await self.page.goto(offer.form_url, wait_until='commit', timeout=90000)
                await self.page.wait_for_timeout(5000)

                # B. CV Upload (APEC Specific Logic)
                try:
                    await self.page.wait_for_selector('#formUpload, .form-check-true.profil-selection', state="visible", timeout=60000)
                except Exception:
                    print(f"⚠ Form did not load correctly for {offer.form_url}. Possible WAF block.")
                    continue

                if user.resume_path:
                    print("⬇️ Downloading resume from cloud to RAM...")
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"

                    # Detect which scenario we're in
                    has_saved_resume = await self.page.locator('.form-check-true.profil-selection').count() > 0

                    if has_saved_resume:
                        print("📄 [APEC] Saved resume detected — uploading a fresh one instead.")
                        # Click "Importer un CV" radio to reveal the upload form
                        await self.page.locator('label.choice-highlight.import-cv').click()
                        await self.page.wait_for_timeout(1000)
                        # Upload from RAM
                        await self.page.locator('#formUpload input[type="file"]').first.set_input_files({
                            "name": human_name,
                            "mimeType": "application/pdf",
                            "buffer": resume_bytes
                        })
                    else:
                        print("📄 [APEC] No saved resume — uploading directly.")
                        # Upload form is already visible, go straight to it
                        await self.page.locator('#formUpload input[type="file"]').first.set_input_files({
                            "name": human_name,
                            "mimeType": "application/pdf",
                            "buffer": resume_bytes
                        })

                    await self.page.wait_for_timeout(1000)

                    # Uncheck "save CV to account" to avoid polluting the user's APEC profile
                    try:
                        save_checkbox = self.page.locator('input[formcontrolname="isCvSave"]')
                        if await save_checkbox.count() > 0 and await save_checkbox.is_checked():
                            await save_checkbox.uncheck()
                    except Exception:
                        pass

                # C. Cover Letter
                try:
                    has_radio_version = await self.page.locator('input[formcontrolname="choixLm"]').count() > 0

                    if has_radio_version:
                        print("📝 [APEC] Cover letter: last-night radio version detected.")
                        await self.page.wait_for_selector('label:has-text("Saisir directement ma lettre de motivation")', state="visible")
                        
                        # Second radio = "Saisir directement ma lettre de motivation"
                        await self.page.locator('input[formcontrolname="choixLm"]').last.click()
                        await self.page.wait_for_timeout(3000)

                        if offer.cover_letter:
                            textarea = self.page.locator('textarea[formcontrolname="lmTexteSaisie"]')
                            await textarea.fill(offer.cover_letter)
                            await textarea.dispatch_event('input')

                    else:
                        print("📝 [APEC] Cover letter: current simple message version detected.")
                        await self.page.wait_for_selector('a[aria-controls="collapseThree"]', state="visible")
                        anchor = self.page.locator('a[aria-controls="collapseThree"]').first
                        anchor_label = self.page.locator('div[id="headingThree"]').first

                        await anchor_label.click()
                        await self.page.wait_for_timeout(5000)

                        val = await anchor.get_attribute('aria-expanded')
                        if val != 'true':
                            print("⚠ Accordion didn't open, clicking again...")
                            await anchor_label.click()

                        await self.page.locator('#collapseThree').wait_for(state="visible")

                        if offer.cover_letter:
                            await self.page.locator('#comment').fill(offer.cover_letter)
                            await self.page.locator('#comment').dispatch_event('input')

                except Exception as e:
                    print(f"⚠ Could not fill cover letter: {e}")

                # D. Additional Data
                try:
                    await self.page.wait_for_selector('a[aria-controls="#collapse_additionalData"]', state="visible")
                    anchor_sec = self.page.locator('a[aria-controls="#collapse_additionalData"]').first
                    anchor_sec_label = self.page.locator('div[id="heading_additionalData"]').first
                    
                    await anchor_sec_label.click()
                    await self.page.wait_for_timeout(5000)
                    
                    if await anchor_sec.get_attribute('aria-expanded') != 'true':
                        await anchor_sec_label.click()

                    # Angular Selectors
                    if hasattr(user, 'study_level') and user.study_level:
                        await self.page.locator('ng-select[formcontrolname="idNiveauFormation"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.study_level}")').first.click()

                    if hasattr(user, 'major') and user.major:
                        await self.page.locator('ng-select[formcontrolname="idDiscipline"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.major}")').first.click()

                    if hasattr(user, 'school_type') and user.school_type:
                        await self.page.locator('ng-select[formcontrolname="idNatureFormation"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.school_type}")').first.click()

                    if hasattr(user, 'graduation_year') and user.graduation_year:
                        await self.page.locator('ng-select[formcontrolname="anneeObtention"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.graduation_year}")').first.click()
                
                except Exception as e:
                     print(f"⚠ Could not fill additional data (optional): {e}")

                # F. Submit
                submit_btn = self.page.locator('button[title="Envoyer ma candidature"]')
                if await submit_btn.is_visible():
                    await submit_btn.click() 
                    await self.page.wait_for_timeout(45000)

                    try:
                        await self.page.wait_for_selector('div[class="notification-title"]', timeout=2000)

                    except Exception:
                        print(f"Submission of {offer.form_url} failed because of random input fields in the application form")
                        i += 1
                        continue 

                    print(" ✅  Application submitted")                    
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                else:
                    print("❌ Submit button not visible.")

            except Exception as e:
                # 🚨 NON-FATAL: Catch specific job error and continue loop
                print(f"❌ Submission failed for {offer.url}: {e}")
            
            i += 1 # Increment your counter!

        # 🚨 CIRCUIT BREAKER: 100% Submission Failure
        if not successful_submissions:
            return {"error": "All application attempts failed. The job board may have updated its application form structure."}

        # 🚨 NO MORE DB SAVING HERE.
        # We simply return the updated list to the Master Agent!
        print(f"✅ Successfully submitted {len(successful_submissions)} applications. Handing back to Orchestrator...")

        return {
            "submitted_offers": successful_submissions
        }

    # --- NODE 9: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up")
        print("--- [APEC] Cleanup ---")
        # Reuse force_cleanup logic but as a step
        await self.force_cleanup()
        #return {"status": "finished"}
        return {}

    # --- ROUTING HELPER ---
    def route_action_intent(self, state: JobApplicationState):
        """The traffic cop that decides which track the Worker runs."""
        intent = state.get("action_intent", "SCRAPE") # Default to SCRAPE
        
        if intent == "SUBMIT":
            print("🛤️ [APEC] Routing to SUBMIT track...")
            return "start_with_session"
        
        print("🛤️ [APEC] Routing to SCRAPE track...")
        return "start"


    # --- THE NEW Y-SHAPED GRAPH BUILDER ---
    def get_graph(self):
        workflow = StateGraph(JobApplicationState)
        
        # --- SCRAPE TRACK NODES ---
        workflow.add_node("start", self.start_session)
        workflow.add_node("nav", self.go_to_job_board)
        workflow.add_node("login", self.request_login) # Will be updated to save session json!
        workflow.add_node("search", self.search_jobs)
        workflow.add_node("scrape", self.get_matched_jobs)
        
        # --- SUBMIT TRACK NODES ---
        # 🚨 [NEW] Boots browser and injects the saved session JSON
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
        workflow.add_conditional_edges("start", self.route_node_exit, {"error": "cleanup", "continue": "nav"})
        workflow.add_conditional_edges("nav", self.route_node_exit, {"error": "cleanup", "continue": "login"})
        workflow.add_conditional_edges("login", self.route_node_exit, {"error": "cleanup", "continue": "search"})
        workflow.add_conditional_edges("search", self.route_node_exit, {"error": "cleanup", "continue": "scrape"})
        # After scraping is done, we go straight to cleanup. No more LLM!
        workflow.add_conditional_edges("scrape", self.route_node_exit, {"error": "cleanup", "continue": "cleanup"}) 

        # --- TRACK B EDGES (SUBMIT) ---
        workflow.add_conditional_edges("start_with_session", self.route_node_exit, {"error": "cleanup", "continue": "submit"})
        workflow.add_conditional_edges("submit", self.route_node_exit, {"error": "cleanup", "continue": "cleanup"})
        
        # --- FINAL EXIT ---
        workflow.add_edge("cleanup", END)
        
        # 🚨 Notice: NO MORE `interrupt_before`. The Master Agent pauses now, not the worker!
        return workflow.compile()