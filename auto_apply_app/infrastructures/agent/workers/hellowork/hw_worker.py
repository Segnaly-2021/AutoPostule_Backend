# auto_apply_app/infrastructure/agent/workers/hellowork_worker.py
import json
import asyncio
import hashlib
import os
from typing import Optional
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from playwright.async_api import Locator, async_playwright, Page, Browser, BrowserContext, Playwright
import pdfplumber
from playwright_stealth import Stealth

# --- DOMAIN IMPORTS ---
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.value_objects import ContractType, JobBoard, ApplicationStatus

# --- INFRA & APP IMPORTS ---
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.application.use_cases.agent_state_use_cases import GetAgentStateUseCase
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort 
from auto_apply_app.application.use_cases.agent_use_cases import GetIgnoredHashesUseCase

class HelloWorkWorker:
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
        self.base_url = "https://www.hellowork.com/fr-fr/"
        self.file_storage = file_storage
        self.get_agent_state = get_agent_state 


        # Runtime State (Lazy Initialization)
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Progress callback (set per-run by master)
        self._progress_callback = None
        self._source_name = "HELLOWORK"  # each worker defines its own name
            
    # ==========================================
    # --- V2 CORE HELPERS (SESSION & HASH) ---
    # ==========================================
    def _get_session_file_path(self, user_id: str) -> str:
        directory = os.path.join(os.getcwd(), "tmp", "sessions")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{user_id}_hellowork_session.json")

    async def _save_auth_state(self, user_id: str):
        if self.context:
            path = self._get_session_file_path(user_id)
            await self.context.storage_state(path=path)
            print(f"🔒 [HW] Session saved securely for user {user_id}")

    def _get_auth_state_path(self, user_id: str) -> str | None:
        path = self._get_session_file_path(user_id)
        if os.path.exists(path):
            return path
        return None
    
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
        b = "hellowork" # Or self.board_name
        
        raw_string = f"{c}_{t}_{b}_{u}"
        return hashlib.md5(raw_string.encode()).hexdigest()

    # ==========================================
    # --- LEGACY / REFERENCE HELPERS ---
    # ==========================================
    def _get_llm(self, preferences: UserPreferences) -> BaseChatModel:
        return ChatGoogleGenerativeAI(
            api_key="LEGACY_REFERENCE_ONLY", model="gemini-3-pro-preview", temperature=preferences.llm_temperature
        )

    def _extract_resume(self, resume_path: str) -> str:
        text = ""
        try:
            with pdfplumber.open(resume_path) as pdf:
                for p in pdf.pages: 
                    text += p.extract_text() + "\n"
        except Exception as e:
            print(f"Error reading resume: {e}")
        return text
    


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
    
    # ==========================================
    # --- OPERATIONAL HELPERS ---
    # ==========================================
    async def force_cleanup(self):
        print("🛑 Force cleanup initiated")
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
            print(f" ⚠️ Cleanup error: {e}")
        print("✅ Force cleanup complete")
    
    async def _get_job_attribute(self, selector: str, default_value: str = None):
        try:
            await self.page.wait_for_selector(selector, state='attached', timeout=5000)
            text = await self.page.locator(selector).first.inner_text()
            return text.strip() 
        except Exception:
            return default_value
        
    async def _apply_filters(self, contract_types: list[ContractType], min_salary: int):
        try:
            print("--- [HW] Applying Search Filters ---")
            all_filters_label = self.page.locator('label[for="allFilters"][data-cy="serpFilters"]').last
            if await all_filters_label.count() > 0:
                await all_filters_label.click()

            if contract_types:
                for contract in contract_types:
                    try:
                        checkbox_selector = f'input[id="c-{str(contract.value)}"]'
                        checkbox = self.page.locator(checkbox_selector)
                        if await checkbox.count() > 0 and not await checkbox.is_checked():
                            await self.page.locator(f'label[for="{await checkbox.get_attribute("id")}"]').click()
                    except Exception:
                        pass

            if min_salary > 0:
                toggle_salary = self.page.locator('input#toggle-salary')
                if await toggle_salary.count() > 0:
                    await self.page.locator('label[for="toggle-salary"]').click()
                    await self.page.wait_for_selector('input#msa:not([disabled])', timeout=3000)
                    await self.page.evaluate("""(val) => {
                        const el = document.querySelector('input#msa');
                        el.value = val;
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    }""", min_salary)

            submit_filters_btn = self.page.locator('[data-cy="offerNumberButton"]')
            if await submit_filters_btn.is_visible():
                await submit_filters_btn.click()
            await self.page.wait_for_timeout(2000)

        except Exception as e:
            print(f"❌ Error applying filters: {e}")


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
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(3000)
            await self._handle_cookies()
            return True

        except Exception as e:
            print(f"⚠️ [HW] Pagination error: {e}")
            return False
    
    async def _handle_cookies(self):
        try:#hw-cc-notice-continue-without-accepting-btn
            is_visible = False
            await self.page.wait_for_selector('button[id="hw-cc-notice-continue-without-accepting-btn"]', state="attached", timeout=3000)   
            cookie_btn = self.page.locator('button[id="hw-cc-notice-continue-without-accepting-btn"]')
            if await cookie_btn.count() > 0:
                is_visible = True
                await cookie_btn.click()
        except Exception as e:
            if is_visible:
                await self.page.wait_for_selector('div[class="hw-cc-main"]', state='attached', timeout=2000)
                await self.page.evaluate("""() => {
                    const overlays = document.querySelectorAll('.hw-cc-main');
                    overlays.forEach(el => el.remove());
                }""")
            else:
                print(f"Handle Cookies Error: {e}")

    # ==========================================
    # --- V2 ROUTING HELPERS ---
    # ==========================================
    
    async def route_node_exit(self, state: JobApplicationState) -> str:
        """
        Generic router. Checks internal state for errors, AND checks the DB 
        for a user-triggered kill switch. Routes to cleanup if either is true.
        """
        # 1. Internal Error Check
        if state.get("error"):
            print(f"🛑 [HW Worker] Circuit Breaker Tripped: {state['error']}")
            return "error"

        # 2. External Kill Switch Check
        try:
            user_id = state["user"].id
            state_result = await self.get_agent_state.execute(user_id)
            
            if state_result.is_success and state_result.value.is_shutdown:
                print("🛑 [HW Worker] User Kill Switch Detected! Aborting gracefully...")
                return "error"
        except Exception as e:
            print(f"⚠️ [APEC] Failed to check DB for agent state: {e}")
            pass # Failsafe: Continue if the DB check fails so we don't randomly crash

        return "continue"
    


    def route_action_intent(self, state: JobApplicationState):
        if state.get("action_intent", "SCRAPE") == "SUBMIT":
            print("🛤️ [HW] Routing to SUBMIT track...")
            return "start_with_session"
        print("🛤️ [HW] Routing to SCRAPE track...")
        return "start"

    # ==========================================
    # --- THE NODES ---
    # ==========================================
    async def start_session(self, state: JobApplicationState):
        await self._emit(state,"Initializing Browser") 
        print(f"--- [HW] Starting session for {state['user'].firstname} ---")
        preferences = state["preferences"]
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless= preferences.browser_headless, 
            args=['--disable-blink-features=AutomationControlled']
        )
        
        # 1. Inject Human Fingerprint
        real_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        self.context = await self.browser.new_context(
            user_agent=real_user_agent,
        )
        stealth = Stealth()
        await stealth.apply_stealth_async(self.context)
        self.page = await self.context.new_page()
        #return {"status": "session_started"}
        return {}

    async def start_session_with_auth(self, state: JobApplicationState):
        await self._emit(state,"Initializing Secure Browser") 
        print("--- [HW] Booting Browser (Session Injection) ---")
        user_id = str(state["user"].id)
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=state["preferences"].browser_headless, 
                args=['--disable-blink-features=AutomationControlled']
            )
            
            # 1. Inject a realistic Human Fingerprint
            real_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            
            session_path = self._get_auth_state_path(user_id)
            if session_path:
                self.context = await self.browser.new_context(
                    storage_state=session_path,
                    user_agent=real_user_agent,
                    device_scale_factor=1,
                    has_touch=False,
                    is_mobile=False
                )
            else:
                self.context = await self.browser.new_context(
                    user_agent=real_user_agent,
                    
                )

            # 🚨 2. The NEW V2 Stealth Fix: Apply it to the entire context
            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)

            # 3. Create the page and navigate
            self.page = await self.context.new_page()
            await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)
            await self.page.wait_for_timeout(30000)
            await self._handle_cookies()
            
            return {}
        except Exception as e:
            return {"error": f"Failed to initialize HelloWork browser: {e}"}

    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board")
        print("--- [HW] Navigating to HelloWork ---")
        try:
            # 1. Wait until the network is actually quiet
            await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)

            await self.page.wait_for_timeout(10000)
            
            # 2. Handle Cookies (important for HelloWork to stop blocking the view)
            await self._handle_cookies()  
            
            # 3. EXTRA SAFETY: Wait for a specific element that proves the page is ready
            # e.g., the search bar or the logo
            await self.page.wait_for_selector('[data-cy="headerAccountMenu"]', state="visible", timeout=30000)
            
            return {}        
        except Exception as e:
            print(f"🚨 Navigation Error: {e}")
            return {"error": f"Navigation failed: {e}"}

    async def request_login(self, state: JobApplicationState):
        await self._emit(state,"Authenticating")  # ← fires immediately
        prefs = state["preferences"]
        creds = state.get("credentials")
        user_id = str(state["user"].id)

        print("--- [HW] Requesting Login ---")
        
        if prefs.is_full_automation and creds["hellowork"]:
            print("🔐 Full Automation: Attempting auto-login...")

            login_plain = None
            pass_plain = None


            try:

                await self.page.wait_for_timeout(30000)


                await self.page.locator('[data-cy="headerAccountMenu"]').click()
                await self.page.locator('[data-cy="headerAccountLogIn"]').click()


                await self.page.wait_for_selector('input[name="email2"]', state="visible", timeout=30000)

                login_plain = await self.encryption_service.decrypt(creds["hellowork"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["hellowork"].password_encrypted)

                await self.page.locator('input[name="email2"]').fill(login_plain)        
                await self.page.locator('input[name="password2"]').fill(pass_plain)                
                
                await self.page.locator('button[type="button"][class="profile-button"]').click()
                await self.page.wait_for_timeout(45000)
                await self.page.wait_for_load_state("networkidle")
                await self.page.goto("https://www.hellowork.com/fr-fr/")

                await self.page.wait_for_timeout(5000)

                await self._save_auth_state(user_id) # 🚨 V2 SAVE SESSION
                print("✅ Auto-login successful")                
                #return {"is_logged_in": True, "status": "login_complete"}
                return {}
            except Exception as e:
                return {"error": f"Failed to log into HelloWork. Check credentials: {e}."}
            
            finally:
                # 🚨 THE MOST CRITICAL PART OF THE FILE
                # Since keep_first preserves the encrypted creds in the global state,
                # we MUST aggressively murder the decrypted plaintext variables in local RAM.
                if login_plain is not None:
                    del login_plain
                if pass_plain is not None:
                    del pass_plain

        else:
            try:
                await self.page.locator('[data-cy="headerAccountMenu"]').click()
                await self.page.locator('[data-cy="headerAccountLogIn"]').click()
                print("⚠ ACTION REQUIRED: Please log in manually within 90 seconds...")
                await asyncio.sleep(90)
                await self.page.locator('a[href="/fr-fr"]').first.click()            
                
                await self._save_auth_state(user_id) # 🚨 V2 SAVE SESSION
                #return {"is_logged_in": True, "status": "login_complete"}
                return {}
            except Exception:
                return {"error": "Manual login timed out."}



        
    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state,"Searching for Jobs") 

        search_entity = state["job_search"]
        job_title = search_entity.job_title
        contract_types = getattr(search_entity, 'contract_types', [])
        min_salary = getattr(search_entity, 'min_salary', 0)
        location = getattr(search_entity, 'location', "")

        print(f"--- [HW] Searching for: {job_title} ---")
        try:

            await self.page.wait_for_timeout(5000)

            await self.page.locator('input[id="k"]').fill(job_title)
            if location and location.strip() != "":
                await self.page.locator('input[id="l"]').fill(location)
            await self.page.keyboard.press("Enter")
            
            await self.page.wait_for_timeout(3000)
            await self._handle_cookies()

            if contract_types or min_salary > 0:
                await self._apply_filters(contract_types, min_salary)
                
            await self.page.wait_for_timeout(2000)
            #return {"status": "search_complete", "current_url": self.page.url}
            return {}
        except Exception as e:
            return {"error": f"Failed to search HelloWork: {e}"}
        

    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state,"Extracting Job Data")
        print("--- [HW] Scraping Jobs  ---")


        user_id = state["user"].id
        search_id = state["job_search"].id
        found_job_entities = []
        
        # 🚨 V2 Setup
        worker_job_limit = state.get("worker_job_limit", 5) 
        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=14)
        ignored_hashes = hash_result.value if hash_result.is_success else set()
        
        print(f"🎯 Target: {worker_job_limit} jobs. 🛡️ Ignored Hashes: {len(ignored_hashes)}")

        page_number = 1
        max_pages = 20
        
        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:
                print(f"📄 [HW] Processing Page {page_number}...")
                
                cards_locator = self.page.locator('[data-id-storage-target="item"]')
                try:
                    await cards_locator.first.wait_for(state='visible', timeout=30000)
                except Exception:
                    if page_number == 1: 
                        print(f"🕵️ [HW] No results found on Page 1 for {state.get('job_search').job_title}")
                        return {"error": "No jobs found for this search."}
                    
                    # If it's page 2, 3, etc., it just means we reached the end of the results
                    print(f"🏁 [HW] No more cards found on page {page_number}. Ending scrape.")
                    break
                    
                count = await cards_locator.count()
                search_url = self.page.url
                
                for i in range(count):
                    if len(found_job_entities) >= worker_job_limit: 
                        break
                    
                    try:
                        cards = self.page.locator('[data-id-storage-target="item"]')
                        card = cards.nth(i)
                        
                        
                        raw_company, raw_title, raw_location  = await self.get_raw_job_data(card) 
                        
                        if not raw_title or not raw_company:
                            print("    ⚠️ Missing title or company, skipping card.")
                            continue

                        await card.click()
                        await self.page.wait_for_load_state("networkidle")
                        await self.page.wait_for_timeout(1000)  
                        current_url = self.page.url      
                            
                        # 🚨 V2 HASH GATE
                        fast_hash = self._generate_fast_hash(raw_company, raw_title, str(user_id))
                        if fast_hash in ignored_hashes:
                            print(f"     ⏩ Skipping duplicate: {raw_title} at {raw_company}")
                            await self.page.goto(search_url, wait_until="networkidle")
                            continue

                        # Extract Description
                        try:
                            desc_el = self.page.locator('div[class="tw-layout-grid"]').first                            
                            if await desc_el.count() == 0: 
                                 desc_el = self.page.locator('div[id="offer-panel"]')
                            job_desc = await desc_el.inner_text()
                        except Exception:
                            job_desc = ""

                        # Apply Button Check
                        moving_to_form_btn = self.page.locator('a[data-cy="applyButton"]').first
                        if await moving_to_form_btn.count() > 0:
                            await moving_to_form_btn.click()
                            try:                                
                                await self.page.wait_for_selector(selector='button[data-cy="applyButton"]', timeout=3000, state='visible')
                                print(f"     ❌ External form detected: {self.page.url}")
                            except Exception:
                                print(f"     ✅ Internal form found: {raw_title}")
                                offer = JobOffer(
                                    url=current_url, 
                                    form_url=current_url, 
                                    search_id=search_id,
                                    user_id=state["user"].id, 
                                    company_name=raw_company, 
                                    job_title=raw_title,
                                    location=raw_location, 
                                    job_board=JobBoard.HELLOWORK, 
                                    status=ApplicationStatus.FOUND,                                
                                    job_desc=job_desc
                                )
                                
                                found_job_entities.append(offer)
                                
                        await self.page.goto(search_url, wait_until="networkidle")
                        await self.page.wait_for_timeout(1000)
                        
                    except Exception as e:
                        print(f"⚠️ Error processing card {i} on page {page_number}: {e}")
                        try:
                            await self.page.goto(search_url, wait_until="networkidle")
                        except Exception: 
                            pass
                        continue

                if len(found_job_entities) >= worker_job_limit: 
                    break
                
                # 🚨 PAGINATION
                if not await self._handle_hw_pagination(page_number):
                    break
                page_number += 1

        except Exception as e:
            return {"error": f"[HW] Fatal Scraping Error: {e}"}

        if not found_job_entities:
            return {"error": "[HW] No new internal HelloWork jobs found for your criteria today."}
            
        print(f"🎉 [HW] Scraping Complete! Handing {len(found_job_entities)} jobs back to Master.")
        return {"found_raw_offers": found_job_entities}

   # --- NODE 6: Analyze (Optimized Flow) ---
    async def analyze_jobs(self, state: JobApplicationState):
        print("--- [HW] Analyzing & Ranking Jobs ---")
        
        user_id = state["user"].id
        search_id = state["job_search"].id
        raw_offers = state["found_raw_offers"]

        # 1. OPTIMIZATION: Filter & Persist Raw Jobs FIRST
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
                await self.page.goto(offer.url, wait_until="networkidle")
                #await self._handle_cookies()
                
                try:
                    # HelloWork Description Selector
                    # Often in #offer-panel or generic container
                    desc_el = self.page.locator('section[class="tw-peer tw-flex tw-flex-col tw-w-full tw-border-b tw-border-b-grey-100 tw-pb-8 sm:tw-pb-10"]')
                    if await desc_el.count() == 0: 
                         desc_el = self.page.locator('div[id="offer-panel"]')
                    job_desc = await desc_el.inner_text()
                except Exception:
                    job_desc = ""

                # B. Validation Check
                if len(job_desc) < 50:
                    print("⏩ Description too short, skipping.")
                    continue

                # C. LLM Call (Your Exact System Message)
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
                
                prompt = HumanMessage(f"""
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
        

    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state,"Submitting Applications")
        print("--- [HW] Submitting Applications ---")
        jobs_to_submit = state.get("processed_offers", [])
        user = state["user"]

        # 🚨 V2 FILTER
        hw_jobs = [job for job in jobs_to_submit if job.job_board == JobBoard.HELLOWORK and job.status == ApplicationStatus.APPROVED]

        if not hw_jobs: 
            return {"error": "no job to submit for HW worker"}

        successful_submissions = []
        i = 0
        for offer in hw_jobs:
            print(f"📝 Applying to: {offer.job_title} ({i+1}/{len(hw_jobs)})")
            try:
                await self.page.goto(offer.form_url, wait_until="commit", timeout=60000)

                moving_to_form_btn = self.page.locator('a[data-cy="applyButton"]').first
                if await moving_to_form_btn.count() > 0:
                    await moving_to_form_btn.click()

                
                await self.page.wait_for_selector('input[name="Firstname"]', timeout=15000) 
                await self.page.locator('input[name="LastName"]').fill(user.lastname)
                if user.resume_path:
                
                    print("⬇️ Downloading resume from cloud to RAM...")
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    
                    # Fallback if the user entity doesn't have the new human name yet
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"

                    # 🚨 Playwright uploads securely from RAM! No temp files!
                    await self.page.locator('[data-cy="cv-uploader-input"]').set_input_files({
                        "name": human_name,
                        "mimeType": "application/pdf",
                        "buffer": resume_bytes
                    })
                
                if offer.cover_letter:
                    await self.page.locator('[data-cy="motivationFieldButton"]').click()
                    await self.page.wait_for_timeout(1000)
                    await self.page.locator('textarea[name="MotivationLetter"]').fill(offer.cover_letter)

                
                
                submit_btn = self.page.locator('[data-cy="submitButton"]')
                if await submit_btn.is_visible():
                    await submit_btn.click() 
                    await self.page.wait_for_timeout(45000)
                    try:
                        # 1. Wait for the notification to appear
                        notification = self.page.locator(
                            '[data-intersect-name-value="notification"]'
                        )
                        
                        await notification.wait_for(state="attached", timeout=3000)

                        if await notification.count() > 0:
                            # 2. Locate the use tag inside the SVG — fix quote nesting
                            use_tag = notification.locator('svg use[href*="badges"]')

                            if await use_tag.count() > 0:
                                href_value = await use_tag.get_attribute("href")

                                if href_value and "error" in href_value.lower():
                                    print(f"❌ Submission of {offer.form_url} blocked by HelloWork error badge.")
                                    i += 1
                                    continue
                                else: 
                                    pass


                    except Exception:
                        print(f"⚠️ No notification appeared for {offer.form_url} — assuming success.")
                        i += 1


                    print("✅ Job application submitted")                    
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                else:
                    print("❌ Submit button not visible.")

                    
            except Exception as e:
                print(f"❌ Submission failed for {offer.url}: {e}")
            i += 1

        if not successful_submissions: 
            return {"error": "All HW submissions failed."}
        
        # 🚨 V2 OUTBOX RETURN
        return {"submitted_offers": successful_submissions}

    async def cleanup(self, state: JobApplicationState):
        await self._emit(state,"Cleaning Up")
        await self.force_cleanup()
        # return {"status": "finished"}
        return {}

    # ==========================================
    # --- V2 GRAPH ROUTER ---
    # ==========================================
    def get_graph(self):
        workflow = StateGraph(JobApplicationState)
        
        # SCRAPE TRACK
        workflow.add_node("start", self.start_session)
        workflow.add_node("nav", self.go_to_job_board)
        workflow.add_node("login", self.request_login)
        workflow.add_node("search", self.search_jobs)
        workflow.add_node("scrape", self.get_matched_jobs)
        
        # SUBMIT TRACK
        workflow.add_node("start_with_session", self.start_session_with_auth)
        workflow.add_node("submit", self.submit_applications)
        
        # SHARED
        workflow.add_node("cleanup", self.cleanup)

        # ENTRY POINT
        workflow.set_conditional_entry_point(
            self.route_action_intent,
            {"start": "start", "start_with_session": "start_with_session"}
        )
        
        # TRACK A EDGES (Scrape)
        workflow.add_conditional_edges("start", self.route_node_exit, {"error": "cleanup", "continue": "nav"})
        workflow.add_conditional_edges("nav", self.route_node_exit, {"error": "cleanup", "continue": "login"})
        workflow.add_conditional_edges("login", self.route_node_exit, {"error": "cleanup", "continue": "search"})
        workflow.add_conditional_edges("search", self.route_node_exit, {"error": "cleanup", "continue": "scrape"})
        workflow.add_conditional_edges("scrape", self.route_node_exit, {"error": "cleanup", "continue": "cleanup"})

        # TRACK B EDGES (Submit)
        workflow.add_conditional_edges("start_with_session", self.route_node_exit, {"error": "cleanup", "continue": "submit"})
        workflow.add_conditional_edges("submit", self.route_node_exit, {"error": "cleanup", "continue": "cleanup"})
        
        workflow.add_edge("cleanup", END)
        return workflow.compile()