import asyncio
import hashlib
from typing import Optional
from langgraph.graph import StateGraph, END
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

    # 🚨 CLASS CONSTANT: Single source of truth for the job card selector
    CARD_SELECTOR = 'div[class="card card-offer mb-20 card--clickable card-offer--qualified"]'

    # --- ADVANCED SEARCH FALLBACK URL ---
    ADVANCED_SEARCH_URL = "https://www.apec.fr/candidat/recherche-emploi.html/emploi/recherche-avancee"

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
        self._source_name = "APEC"


    # =========================================================================
    # HELPERS
    # =========================================================================

    async def route_node_exit(self, state: JobApplicationState) -> str:
        if state.get("error"):
            print(f"🛑 [APEC Worker] Circuit Breaker Tripped: {state['error']}")
            return "error"

        try:
            user_id = state["user"].id
            state_result = await self.get_agent_state.execute(user_id)
            if state_result.is_success and state_result.value.is_shutdown:
                print("🛑 [APEC Worker] User Kill Switch Detected! Aborting gracefully...")
                return "error"
        except Exception as e:
            print(f"⚠️ [APEC] Failed to check DB for agent state: {e}")
            pass

        return "continue"
    

    async def _emit(self, state: JobApplicationState, stage: str, status: str = "in_progress", error: str = None):
        if not self._progress_callback:
            return
        try:
            search_id = str(state["job_search"].id) if "job_search" in state else ""
            await self._progress_callback({
                "source": self._source_name.upper(),
                "stage": stage,
                "node": self._source_name.lower(),
                "status": "error" if error else status,
                "error": error,
                "search_id": search_id
            })
        except Exception:
            pass


    def _generate_fast_hash(self, company_name: str, job_title: str, user_id: str) -> str:
        c = str(company_name).replace(" ", "").lower().strip()
        t = str(job_title).replace(" ", "").lower().strip()
        u = str(user_id).strip()
        b = "apec"
        raw_string = f"{c}_{t}_{b}_{u}"
        return hashlib.md5(raw_string.encode()).hexdigest()


    def _get_session_file_path(self, user_id: str) -> str:
        directory = os.path.join(os.getcwd(), "tmp", "sessions")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{user_id}_apec_session.json")

    async def _save_auth_state(self, user_id: str):
        if self.context:
            path = self._get_session_file_path(user_id)
            await self.context.storage_state(path=path)
            print(f"🔒 [APEC] Session saved securely for user {user_id}")

    def _get_auth_state_path(self, user_id: str) -> str | None:
        path = self._get_session_file_path(user_id)
        if os.path.exists(path):
            return path
        return None


    async def _handle_cookies(self):
        try: 
            await self.page.wait_for_selector('button:has-text("Refuser tous les cookies")', state='attached', timeout=5000)
            cookie_btn = self.page.locator('button:has-text("Refuser tous les cookies")')
            if await cookie_btn.count() > 0:
                await cookie_btn.click()
        except Exception:
            print("No Cookies popup")


    async def force_cleanup(self):
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


    def _extract_resume(self, resume_path: str) -> str:
        text = ""
        try:
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


    # --- HELPER: Nav Back ---
    async def nav_back(self, url: str):
        await self.page.goto(url, wait_until="networkidle")

        try:
            # ✅ Wait for cards to confirm we're back on a valid results page
            await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
            await asyncio.sleep(3) 
            await self._handle_cookies()

        except Exception:
            print("⚠️ Cards didn't reappear after going back. Reloading...")
            await self.page.reload(wait_until="networkidle")
            # ✅ Verify cards appeared after reload too
            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
            except Exception:
                print("⚠️ Cards still not visible after reload. Page state is broken.")
            await self._handle_cookies()


    # --- HELPER: Handle Pagination ---
    async def _handle_apec_pagination(self, page_number: int) -> bool:
        try:
            next_button = self.page.locator(
                'nav[aria-label="Page navigation"] li[class="page-item"]'
            )

            if await next_button.count() == 0:
                print("🔚 [APEC] No next button found. Reached last page.")
                return False

            butt_child = next_button.locator("a")
            if await butt_child.count() == 0:
                print("🔚 [APEC] Next button inactive. Reached last page.")
                return False            

            print(f"➡️ [APEC] Moving to page {page_number + 1}...")

            # ✅ RETRY: Click next + wait for cards as one unit
            for attempt in range(3):
                try:
                    await next_button.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"⚠️ [APEC] Pagination failed after 3 attempts: {e}")
                        return False
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            return True

        except Exception as e:
            print(f"⚠️ [APEC] Pagination error: {e}")
            return False


    # --- HELPER: Apply APEC Advanced Filters ---
    async def _apply_filters(self, job_title: str, contract_types: list[ContractType], min_salary: int):
        print(f"--- [APEC] Applying Advanced Filters for: {job_title} ---")
        try:
            # ✅ RETRY: advancedSearch element with fallback URL
            # This logic is centralized here so both search_jobs and
            # start_session_with_auth benefit from it automatically
            for attempt in range(3):
                try:
                    await self.page.wait_for_selector('a[id="advancedSearch"]', state="visible", timeout=20000)
                    break
                except Exception:
                    if attempt == 2:
                        print("⚠️ advancedSearch not found after 3 attempts. Navigating directly to advanced search page...")
                        await self.page.goto(self.ADVANCED_SEARCH_URL, wait_until="networkidle", timeout=60000)
                    else:
                        print(f"⚠️ advancedSearch not visible, attempt {attempt+1}. Retrying in {2 ** attempt}s...")
                        await asyncio.sleep(2 ** attempt)

            # ✅ If we navigated directly, keywords input is already visible
            # If not, we need to click the advancedSearch link first
            try:
                await self.page.wait_for_selector('input[id="keywords"]', state="visible", timeout=5000)
                print("✅ Already on advanced search page — skipping link click.")
            except Exception:
                await self.page.locator('a[id="advancedSearch"]').click()
                await self.page.wait_for_selector('input[id="keywords"]', state="visible", timeout=15000)

            # 2. Fill the Job Title
            await self.page.locator('input[id="keywords"]').fill(job_title)

            # 3. Handle Contract Types
            contract_map = {
                "CDI": "101888",
                "CDD": "101887",
                "Alternance": "20053",
                "Intérim": "101930",                
                "Stage": "597171"
            }

            for attempt in range(3):
                try:
                    await self.page.wait_for_selector('select[formcontrolname="typesContrat"]', state="visible", timeout=45000)
                    await self.page.wait_for_selector('apec-slider input.pull-left', state="visible", timeout=45000)
                    print("✅ Full form rendered — proceeding with filters.")
                    break
                except Exception:
                    if attempt == 2:
                        print("⚠️ Form never fully rendered after 3 attempts.")
                        raise
                    print(f"⚠️ Form not ready, attempt {attempt+1}. Reloading...")
                    await self.page.reload(wait_until="networkidle")
                    await asyncio.sleep(2 ** attempt)



            if contract_types:
                for contract in contract_types:
                    val = contract_map.get(str(contract.value), None)
                    if val:
                        await self.page.select_option('select[formcontrolname="typesContrat"]', value=val)
                        print(f"  ✓ Contract selected: {contract}")
                        break

            # 4. Handle Salary
            if min_salary > 0:
                salary_input = self.page.locator('apec-slider input.pull-left')
                if await salary_input.count() > 0:
                    salary_k = str(min_salary // 1000) if min_salary >= 1000 else str(min_salary)
                    await salary_input.fill(salary_k)
                    print(f"  ✓ Min Salary set to: {salary_k}K€")

            # 5. Submit the search
            # ✅ RETRY: RECHERCHER button click is critical
            for attempt in range(3):
                try:
                    await self.page.locator('button:has-text("RECHERCHER")').click()
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)

            # ✅ Wait for results — networkidle + cards appearing
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)

        except Exception as e:
            print(f"❌ Error applying APEC filters: {e}")
            raise  # bubble up so callers can handle it


    # --- HELPER: Get Job attributes ---
    async def _get_job_attribute(self, card: Locator, selector: str, default_value: str=None):
        try:           
            content = await card.locator(selector).inner_text()
            return content.strip()
        except Exception:
            return default_value


    # =========================================================================
    # NODES
    # =========================================================================

    # --- NODE 1: Start Session ---
    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser")
        print(f"--- [APEC] Starting session for {state['user'].firstname} ---")
        preferences = state["preferences"]
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=preferences.browser_headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            real_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            self.context = await self.browser.new_context(
                user_agent=real_user_agent
            )
            
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
        print("--- [APEC] Booting Browser (Session Injection) ---")
        user_id = str(state["user"].id)
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=state["preferences"].browser_headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            real_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
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

            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)
            self.page = await self.context.new_page()
            
            # ✅ RETRY: goto + wait_for_selector as one unit
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=90000)
                    await self.page.wait_for_selector('li[id="header-monespace"]', state="visible", timeout=30000)
                    break
                except Exception as e:
                    if attempt == 2:
                        return {"error": f"Failed to reach APEC after 3 attempts: {str(e)}"}
                    print(f"⚠️ [APEC] Auth boot attempt {attempt+1} failed. Retrying in {2 ** attempt}s...")
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()

            # DUMMY SEARCH TO ESTABLISH SESSION CONTEXT
            search_entity = state["job_search"]
            job_title = search_entity.job_title
            contract_types = getattr(search_entity, 'contract_types', [])
            min_salary = getattr(search_entity, 'min_salary', 0)

            print("🔎 [APEC] Dummy Starting Search Process")       
            try:
                # ✅ _apply_filters now owns the advancedSearch retry + fallback internally
                await self._apply_filters(job_title, contract_types, min_salary)    

                try:
                    await self.page.wait_for_selector(self.CARD_SELECTOR, timeout=5000)
                    print("✅ Search results loaded successfully.")
                except Exception:
                    print("⚠️ No results found after applying filters.")

                return {} 
            except Exception as e:
                print(f"Search Error: {e}")
                return {}
            
        except Exception as e:
            print(f"Browser Auth Initialization Error: {e}")
            return {"error": f"Failed to initialize browser with session: {str(e)}"}


    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board")
        print("--- [APEC] Navigating to Board ---")
        try:
            # ✅ RETRY: goto + cookies + wait_for_selector as one unit
            # _handle_cookies is part of this unit because the cookie banner
            # can block the header element from being visible
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                    await self._handle_cookies()
                    await self.page.wait_for_selector('li[id="header-monespace"]', state="visible", timeout=30000)
                    break
                except Exception:
                    if attempt == 2:
                        return {"error": "Could not reach APEC.fr. The job board might be down or undergoing maintenance."}
                    print(f"⚠️ [APEC] Navigation attempt {attempt+1} failed. Retrying in {2 ** attempt}s...")
                    await asyncio.sleep(2 ** attempt)
            
            return {}
        except Exception as e:
            print(f"Nav Error: {e}")
            return {"error": "Could not reach APEC.fr. The job board might be down or undergoing maintenance."}
            

    # --- NODE 3: Login ---
    async def request_login(self, state: JobApplicationState):
        await self._emit(state, "Authenticating")
        
        prefs = state["preferences"]
        creds = state.get("credentials")
        user_id = state["user"].id

        print("--- [APEC] Login Phase ---")

        if prefs.is_full_automation and creds["apec"]:
            print("🔐 Full Automation: Attempting auto-login...")

            login_plain = None
            pass_plain = None

            try:
                login_plain = await self.encryption_service.decrypt(creds["apec"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["apec"].password_encrypted)

                # ✅ RETRY UNIT 1: Open login modal + verify email input appears
                for attempt in range(3):
                    try:
                        await self.page.wait_for_selector('li[id="header-monespace"]', state="visible", timeout=30000)
                        await self.page.locator('li[id="header-monespace"]').click()
                        await self.page.wait_for_selector('input[id="emailid"]', state="visible", timeout=15000)
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Could not open the login modal."}
                        print(f"⚠️ [APEC] Login modal attempt {attempt+1} failed. Reloading...")
                        await self.page.reload(wait_until="networkidle")
                        await asyncio.sleep(2 ** attempt)

                # ✅ RETRY UNIT 2: Fill credentials + submit
                # Clear fields before each attempt to avoid corrupted state
                for attempt in range(3):
                    try:
                        await self.page.locator('input[id="emailid"]').clear()
                        await self.page.locator('input[id="emailid"]').fill(login_plain)
                        await self.page.locator('input[id="password"]').clear()
                        await self.page.locator('input[id="password"]').fill(pass_plain)
                        await self.page.wait_for_selector('button[type="submit"][value="Login"]', state="visible", timeout=10000)
                        await self.page.locator('button[type="submit"][value="Login"]').first.click()
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Could not submit credentials."}
                        await asyncio.sleep(2 ** attempt)

                # ✅ RETRY UNIT 3: Proof of login
                for attempt in range(3):
                    try:
                        await self.page.wait_for_url("**/candidat**", timeout=30000)
                        await self.page.goto(f"{self.base_url}candidat.html", wait_until="networkidle", timeout=90000)
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Please check your APEC credentials in your settings."}
                        await asyncio.sleep(2 ** attempt)

                print("✅ Auto-login successful")
                await self._save_auth_state(str(user_id))
                return {}

            except Exception as e:
                print(f"❌ Auto-login failed: {e}")
                return {"error": "Login failed. Please check your APEC credentials in your settings."}

            finally:
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
                await self._save_auth_state(user_id)
                return {}
            except Exception as e:
                print(f"Manual Login Error: {e}")
                return {"error": "Login timed out. We didn't detect a successful login within the allowed time."}
    

    # --- NODE 4: Search ---
    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state, "Searching for Jobs") 
        search_entity = state["job_search"]
        job_title = search_entity.job_title
        contract_types = getattr(search_entity, 'contract_types', [])
        min_salary = getattr(search_entity, 'min_salary', 0)

        print("🔎 [APEC] Starting Search Process")       
        try:
            # ✅ _handle_cookies first, then _apply_filters which owns
            # the advancedSearch retry + fallback navigation internally
            await self._handle_cookies()
            await self._apply_filters(job_title, contract_types, min_salary)    

            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, timeout=5000)
                print("✅ Search results loaded successfully.")
            except Exception:
                print("⚠️ No results found after applying filters.")
                return {
                    "error": "No new matching jobs were found for this search today. We'll try again tomorrow!"
                }
                
        except Exception as e:
            print(f"Search Error: {e}")            
            return {
                "error": "We encountered an issue applying your search filters. The job board may have updated its layout."
            }
            
        return {}


    # --- NODE 5: Scrape Jobs ---
    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data") 
        print("--- [APEC] Scraping Jobs ---")

        user_id = state["user"].id
        search_id = state["job_search"].id
        found_job_entities = []
        
        worker_job_limit = 1 or state.get("worker_job_limit", 5) 
        
        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=30)
        if not hash_result.is_success:
            print(f"⚠️ Warning: Could not fetch ignored hashes: {hash_result.error.message}")
            ignored_hashes = set()
        else:
            ignored_hashes = hash_result.value
            
        print(f"🛡️ Loaded {len(ignored_hashes)} ignored job hashes to prevent duplicates.")
        print(f"🎯 Target: Scraping up to {worker_job_limit} new jobs.")
        
        page_number = 1
        max_pages = 20
        
        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:
                print(f"📄 [APEC] Processing Page {page_number}...")
                
                cards = self.page.locator(self.CARD_SELECTOR)
                try:
                    # ✅ Increased timeout — cards are the backbone of scraping
                    await cards.first.wait_for(state="visible", timeout=15000)
                except Exception:
                    print(f"⚠️ No cards found on page {page_number}.")
                    if page_number == 1:
                        print("📭 No job postings appeared on the results page.")
                        return {"found_raw_offers": []}
                    break

                count = await cards.count()
                result_url = self.page.url 
                
                for i in range(count):
                    if len(found_job_entities) >= worker_job_limit:
                        print(f"✅ Reached target limit of {worker_job_limit} jobs.")
                        break

                    print(f"  -> Processing card {i+1}/{count} (Page {page_number})")

                    raw_company, raw_title, raw_location = await self.get_raw_job_data(
                        self.page.locator(self.CARD_SELECTOR).nth(i)
                    )
                    print(f"---[APEC WORKER] RAW DATA---\n[APEC Company]: {raw_company},\n[APEC Title]: {raw_title},\n[APEC Location]: {raw_location}")
                    if not raw_company or not raw_title:
                        print("    ⚠️ Missing title or company, skipping card.")
                        continue

                    fast_hash = self._generate_fast_hash(raw_company, raw_title, str(user_id))
                    if fast_hash in ignored_hashes:
                        print(f"    ⏩ Skipping duplicate: {raw_title} at {raw_company}")
                        continue

                    # ✅ RETRY: card.click() + networkidle as one unit
                    # Card can go stale between locating and clicking on slow pages
                    click_success = False
                    for attempt in range(3):
                        try:
                            cards = self.page.locator(self.CARD_SELECTOR)
                            card = cards.nth(i)
                            await card.click()
                            await self.page.wait_for_load_state("networkidle")
                            click_success = True
                            break
                        except Exception:
                            if attempt == 2:
                                print("    ⚠️ Card click failed after 3 attempts. Skipping.")
                                break
                            await asyncio.sleep(2 ** attempt)

                    if not click_success:
                        continue

                    # ✅ Wait for job description — wrapped in try/except
                    # because this element doesn't exist on all job pages
                    try:
                        await self.page.wait_for_selector('div[class="col-lg-8 border-L"]', state="visible", timeout=10000)
                    except Exception:
                        pass

                    # Scrape the Job Description
                    try:
                        desc_element = self.page.locator('div[class="col-lg-8 border-L"]')
                        if await desc_element.count() > 0:
                            job_desc = await desc_element.inner_text()
                        else:
                            job_desc = await self.page.locator("body").inner_text() 
                    except Exception:
                        job_desc = ""
                    
                    # Check for "Easy Apply" Button
                    try:
                        await self.page.wait_for_selector('a[class="btn btn-primary ml-0"]', state="visible", timeout=5000)
                    except Exception:                
                        print("    ❌ External or already applied! Back to search result:")                  
                        await self.nav_back(result_url) 
                        continue 
                    
                    apply_btn = self.page.locator('a[class="btn btn-primary ml-0"]')
                        
                    if await apply_btn.count() > 0:
                        href = await apply_btn.get_attribute("href")
                        
                        if href and "to=int" in href:
                            print(f"    ✅ Internal offer found: {raw_title}")
                            full_offer_url = f"https://www.apec.fr{href}"
                            
                            await self.page.goto(full_offer_url, wait_until="networkidle")                        

                            try:
                                await self.page.wait_for_selector('button[title="Postuler"]', state="visible", timeout=15000)
                            except Exception:
                                await self.nav_back(result_url)
                                continue

                            postule_btn = self.page.locator('button[title="Postuler"]')

                            if await postule_btn.count() > 0:
                                await postule_btn.click()
                                await self.page.wait_for_load_state("networkidle")

                                print("    ✅ Valid application form confirmed. Saving to batch.")   

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
                    
                    await self.nav_back(result_url)

                if len(found_job_entities) >= worker_job_limit:
                    break
                    
                if not await self._handle_apec_pagination(page_number):
                    break
                page_number += 1

        except Exception as e:
            print(f"Scraping Error: {e}")
            return {"error": "A critical error occurred while scanning the job listings. We have safely halted the process."}

        if not found_job_entities:
            print("⚠️ Scanned jobs across pages, but none were valid for auto-application.")
            return {"found_raw_offers": []}

        print(f"🎉 APEC Scraping Complete! Handing {len(found_job_entities)} jobs back to the Master Orchestrator.")
        return {"found_raw_offers": found_job_entities}


    # --- NODE 7: Submit Applications ---
    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state, "Submitting Applications")
        print("--- [APEC] Submitting Applications ---")

        jobs_to_process = state.get("processed_offers", [])
        user = state["user"] 
        assigned_submit_limit = state.get("worker_job_limit", 5) 

        apec_jobs = [job for job in jobs_to_process if job.job_board == JobBoard.APEC and job.status == ApplicationStatus.APPROVED]

        if not apec_jobs:
            print("No approved APEC jobs in submission queue.")
            return {"status": "no_apec_jobs_to_submit"}

        successful_submissions = []

        i = 0
        for offer in apec_jobs:
            if len(successful_submissions) >= assigned_submit_limit:
                print(f"🛑 [APEC] Reached assigned submission limit ({assigned_submit_limit}). Halting further submissions.")
                break

            print(f"📝 Applying to: {offer.job_title}: {i+1} out of {len(apec_jobs)}")
            try:
                # ✅ RETRY: goto + form load as one critical unit
                # This is where WAF hits hardest — one shot is not enough
                form_loaded = False
                for attempt in range(3):
                    try:
                        await self.page.goto(offer.form_url, wait_until='commit', timeout=90000)
                        await self.page.wait_for_selector('#formUpload, .form-check-true.profil-selection', state="visible", timeout=60000)
                        form_loaded = True
                        break
                    except Exception:
                        if attempt == 2:
                            print(f"⚠ Form failed to load after 3 attempts for {offer.form_url}. Skipping.")
                            break
                        print(f"⚠ Form load attempt {attempt+1} failed. Retrying in {2 ** attempt}s...")
                        await asyncio.sleep(2 ** attempt)

                if not form_loaded:
                    i += 1
                    continue

                if user.resume_path:
                    print("⬇️ Downloading resume from cloud to RAM...")
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"

                    has_saved_resume = await self.page.locator('.form-check-true.profil-selection').count() > 0

                    if has_saved_resume:
                        print("📄 [APEC] Saved resume detected — uploading a fresh one instead.")
                        await self.page.locator('label.choice-highlight.import-cv').click()
                        await self.page.wait_for_selector('#formUpload input[type="file"]', state="visible", timeout=10000)
                        await self.page.locator('#formUpload input[type="file"]').first.set_input_files({
                            "name": human_name,
                            "mimeType": "application/pdf",
                            "buffer": resume_bytes
                        })
                    else:
                        print("📄 [APEC] No saved resume — uploading directly.")
                        await self.page.locator('#formUpload input[type="file"]').first.set_input_files({
                            "name": human_name,
                            "mimeType": "application/pdf",
                            "buffer": resume_bytes
                        })

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
                        print("📝 [APEC] Cover letter: radio version detected.")
                        await self.page.wait_for_selector('label:has-text("Saisir directement ma lettre de motivation")', state="visible")
                        await self.page.locator('input[formcontrolname="choixLm"]').last.click()
                        await self.page.wait_for_selector('textarea[formcontrolname="lmTexteSaisie"]', state="visible", timeout=10000)

                        if offer.cover_letter:
                            textarea = self.page.locator('textarea[formcontrolname="lmTexteSaisie"]')
                            await textarea.fill(offer.cover_letter)
                            await textarea.dispatch_event('input')

                    else:
                        print("📝 [APEC] Cover letter: accordion version detected.")
                        await self.page.wait_for_selector('a[aria-controls="collapseThree"]', state="visible")
                        anchor = self.page.locator('a[aria-controls="collapseThree"]').first
                        anchor_label = self.page.locator('div[id="headingThree"]').first

                        await anchor_label.click()
                        await self.page.locator('#collapseThree').wait_for(state="visible", timeout=10000)

                        val = await anchor.get_attribute('aria-expanded')
                        if val != 'true':
                            print("⚠ Accordion didn't open, clicking again...")
                            await anchor_label.click()
                            await self.page.locator('#collapseThree').wait_for(state="visible", timeout=10000)

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
                    await self.page.wait_for_selector('.ng-option', state="visible", timeout=10000)
                    
                    if await anchor_sec.get_attribute('aria-expanded') != 'true':
                        await anchor_sec_label.click()
                        await self.page.wait_for_selector('.ng-option', state="visible", timeout=10000)

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

                    # ✅ Wait directly for confirmation instead of blind sleep
                    try:
                        await self.page.wait_for_selector('div[class="notification-title"]', state="visible", timeout=45000)
                        print("✅ Application submitted")
                        offer.status = ApplicationStatus.SUBMITTED
                        successful_submissions.append(offer)
                    except Exception:
                        print(f"Submission of {offer.form_url} failed — confirmation not received.")
                        continue
                else:
                    print("❌ Submit button not visible.")

            except Exception as e:
                print(f"❌ Submission failed for {offer.url}: {e}")
            
            i += 1

        if not successful_submissions:
            return {"error": "All application attempts failed. The job board may have updated its application form structure."}

        print(f"✅ Successfully submitted {len(successful_submissions)} applications. Handing back to Orchestrator...")
        return {"submitted_offers": successful_submissions}


    # --- NODE 9: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up")
        print("--- [APEC] Cleanup ---")
        await self.force_cleanup()
        return {}


    # =========================================================================
    # GRAPH
    # =========================================================================

    def route_action_intent(self, state: JobApplicationState):
        intent = state.get("action_intent", "SCRAPE")
        if intent == "SUBMIT":
            print("🛤️ [APEC] Routing to SUBMIT track...")
            return "start_with_session"
        print("🛤️ [APEC] Routing to SCRAPE track...")
        return "start"


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

        workflow.set_conditional_entry_point(
            self.route_action_intent,
            {
                "start": "start",
                "start_with_session": "start_with_session"
            }
        )
        
        # SCRAPE EDGES
        workflow.add_conditional_edges("start", self.route_node_exit, {"error": "cleanup", "continue": "nav"})
        workflow.add_conditional_edges("nav", self.route_node_exit, {"error": "cleanup", "continue": "login"})
        workflow.add_conditional_edges("login", self.route_node_exit, {"error": "cleanup", "continue": "search"})
        workflow.add_conditional_edges("search", self.route_node_exit, {"error": "cleanup", "continue": "scrape"})
        workflow.add_conditional_edges("scrape", self.route_node_exit, {"error": "cleanup", "continue": "cleanup"}) 

        # SUBMIT EDGES
        workflow.add_conditional_edges("start_with_session", self.route_node_exit, {"error": "cleanup", "continue": "submit"})
        workflow.add_conditional_edges("submit", self.route_node_exit, {"error": "cleanup", "continue": "cleanup"})
        
        workflow.add_edge("cleanup", END)
        
        return workflow.compile()