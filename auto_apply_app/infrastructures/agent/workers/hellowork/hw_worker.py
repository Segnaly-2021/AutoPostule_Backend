# auto_apply_app/infrastructure/agent/workers/hellowork_worker.py
import asyncio
import hashlib
import os
from typing import Optional
from langgraph.graph import StateGraph, END
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

    # 🚨 CLASS CONSTANT: Single source of truth for the job card selector
    CARD_SELECTOR = '[data-id-storage-target="item"]'

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
        self._source_name = "HELLOWORK"


    # =========================================================================
    # HELPERS
    # =========================================================================

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
        b = "hellowork"
        raw_string = f"{c}_{t}_{b}_{u}"
        return hashlib.md5(raw_string.encode()).hexdigest()

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


    # --- HELPER: Get Raw Job Data (dissociated try/except per field) ---
    async def get_raw_job_data(self, card: Locator):
        raw_title = None
        raw_company = None
        raw_location = None

        # Gate check — if anchor not attached, card content hasn't rendered
        try:
            await card.locator('a[data-cy="offerTitle"]').wait_for(state="attached", timeout=10000)
        except Exception as e:
            print(f"    ⚠️ Card content not ready: {e}")
            return "No Name", None, None

        try:
            anchor = card.locator('a[data-cy="offerTitle"]')
            paragraphs = anchor.locator('p')
            raw_title = await paragraphs.nth(0).inner_text()
        except Exception as e:
            print(f"    ⚠️ Could not extract title: {e}")

        try:
            anchor = card.locator('a[data-cy="offerTitle"]')
            paragraphs = anchor.locator('p')
            raw_company = await paragraphs.nth(1).inner_text()
        except Exception as e:
            raw_company = "No Name"
            print(f"    ⚠️ Could not extract company: {e}")

        try:
            raw_location = await card.locator('div[data-cy="localisationCard"]').inner_text()
        except Exception as e:
            print(f"    ⚠️ Could not extract location: {e}")

        return (
            raw_company.strip() if raw_company else None,
            raw_title.strip() if raw_title else None,
            raw_location.strip() if raw_location else None
        )


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


    # --- HELPER: Nav Back to Search Results with Retry + Verification ---
    async def _nav_back_to_search(self, search_url: str) -> bool:
        """
        Navigate back to the search results page with retry logic.
        Returns True if successful, False if the page state is broken.
        """
        for attempt in range(3):
            try:
                await self.page.goto(search_url, wait_until="networkidle")
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                return True
            except Exception as e:
                if attempt == 2:
                    print(f"    ⚠️ Could not return to search results after 3 attempts: {e}")
                    return False
                print(f"    ⚠️ Nav back attempt {attempt+1} failed. Retrying...")
                await asyncio.sleep(2 ** attempt)
        return False


    # --- HELPER: Apply Filters with Retry ---
    async def _apply_filters(self, contract_types: list[ContractType], min_salary: int):
        try:
            print("--- [HW] Applying Search Filters ---")

            # ✅ RETRY UNIT 1: Open the filter panel
            # all_filters_label is the gateway — if this fails, no filters get applied
            # Using :has-text("Filtres") to disambiguate from sticky/inline filter labels
            FILTER_LABEL_SELECTOR = 'div[class="tw-layout-inner-grid"] label[for="allFilters"][data-cy="serpFilters"]:has-text(" Filtres ")'

            for attempt in range(3):
                try:
                    await self.page.wait_for_selector(FILTER_LABEL_SELECTOR, state="visible", timeout=20000)
                    all_filters_label = self.page.locator(FILTER_LABEL_SELECTOR).first
                    await all_filters_label.click()
                    # Verify the panel actually opened
                    await self.page.wait_for_selector('input#toggle-salary', state="attached", timeout=10000)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"⚠️ [HW] Could not open filter panel after 3 attempts: {e}")
                        raise
                    await asyncio.sleep(2 ** attempt)

            # Apply contract types
            if contract_types:
                for contract in contract_types:
                    try:
                        checkbox_selector = f'input[id="c-{str(contract.value)}"]'
                        checkbox = self.page.locator(checkbox_selector)
                        if await checkbox.count() > 0 and not await checkbox.is_checked():
                            await self.page.locator(f'label[for="{await checkbox.get_attribute("id")}"]').click()
                    except Exception:
                        pass

            # Apply salary
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

            # ✅ RETRY UNIT 2: Submit filters + verify results appeared
            submit_filters_btn = self.page.locator('[data-cy="offerNumberButton"]')
            if await submit_filters_btn.is_visible():
                for attempt in range(3):
                    try:
                        await submit_filters_btn.click()
                        await self.page.wait_for_load_state("networkidle")
                        await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                        break
                    except Exception as e:
                        if attempt == 2:
                            print(f"⚠️ [HW] Filter submit failed after 3 attempts: {e}")
                            raise
                        await asyncio.sleep(2 ** attempt)

        except Exception as e:
            print(f"❌ Error applying filters: {e}")
            raise  # bubble up so callers can handle


    # --- HELPER: Handle Pagination with Retry ---
    async def _handle_hw_pagination(self, page_number: int) -> bool:
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

            # ✅ RETRY: Click next + wait for cards as one unit
            for attempt in range(3):
                try:
                    await next_button.click()
                    await self.page.wait_for_load_state("networkidle")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"⚠️ [HW] Pagination failed after 3 attempts: {e}")
                        return False
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            return True

        except Exception as e:
            print(f"⚠️ [HW] Pagination error: {e}")
            return False


    async def _handle_cookies(self):
        is_visible = False
        try:
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


    async def route_node_exit(self, state: JobApplicationState) -> str:
        if state.get("error"):
            print(f"🛑 [HW Worker] Circuit Breaker Tripped: {state['error']}")
            return "error"

        try:
            user_id = state["user"].id
            state_result = await self.get_agent_state.execute(user_id)
            if state_result.is_success and state_result.value.is_shutdown:
                print("🛑 [HW Worker] User Kill Switch Detected! Aborting gracefully...")
                return "error"
        except Exception as e:
            print(f"⚠️ [HW Worker] Failed to check DB for agent state: {e}")
            pass

        return "continue"


    def route_action_intent(self, state: JobApplicationState):
        if state.get("action_intent", "SCRAPE") == "SUBMIT":
            print("🛤️ [HW] Routing to SUBMIT track...")
            return "start_with_session"
        print("🛤️ [HW] Routing to SCRAPE track...")
        return "start"


    # =========================================================================
    # NODES
    # =========================================================================

    # --- NODE 1: Start Session ---
    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser") 
        print(f"--- [HW] Starting session for {state['user'].firstname} ---")
        preferences = state["preferences"]
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=preferences.browser_headless, 
            args=['--disable-blink-features=AutomationControlled']
        )
        real_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        self.context = await self.browser.new_context(
            user_agent=real_user_agent,
        )
        stealth = Stealth()
        await stealth.apply_stealth_async(self.context)
        self.page = await self.context.new_page()
        return {}


    # --- NODE 1 Bis: Boot & Inject Session (Submit Track) ---
    async def start_session_with_auth(self, state: JobApplicationState):
        await self._emit(state, "Initializing Secure Browser") 
        print("--- [HW] Booting Browser (Session Injection) ---")
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

            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)
            self.page = await self.context.new_page()

            # ✅ RETRY: goto + header element as one unit
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=120000)
                    await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=60000)
                    break
                except Exception as e:
                    if attempt == 2:
                        return {"error": f"Failed to reach HelloWork after 3 attempts: {str(e)}"}
                    print(f"⚠️ [HW] Auth boot attempt {attempt+1} failed. Retrying in {2 ** attempt}s...")
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()

            # Dummy Search to Establish Session Context
            search_entity = state["job_search"]
            job_title = search_entity.job_title
            contract_types = getattr(search_entity, 'contract_types', [])
            min_salary = getattr(search_entity, 'min_salary', 0)
            location = getattr(search_entity, 'location', "")

            print(f"--- [HW] Dummy Searching for: {job_title} ---")
            try:
                await self.page.locator('input[id="k"]').fill(job_title)
                if location and location.strip() != "":
                    await self.page.locator('input[id="l"]').fill(location)
                await self.page.keyboard.press("Enter")

                await self.page.wait_for_load_state("networkidle")
                await self._handle_cookies()

                if contract_types or min_salary > 0:
                    await self._apply_filters(contract_types, min_salary)

                return {}
            except Exception as e:
                print(f"🚨 Session Initialization Error: {e}")
                return {}
            
        except Exception as e:
            return {"error": f"Failed to initialize HelloWork browser: {e}"}


    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board")
        print("--- [HW] Navigating to HelloWork ---")
        try:
            # ✅ RETRY: goto + cookies + wait_for_selector as one unit
            # Cookies sit between goto and header check because the cookie banner
            # can block the header element from being visible
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                    await self._handle_cookies()
                    await self.page.wait_for_selector('[data-cy="headerAccountMenu"]', state="visible", timeout=30000)
                    break
                except Exception:
                    if attempt == 2:
                        return {"error": "Could not reach HelloWork. The job board might be down or undergoing maintenance."}
                    print(f"⚠️ [HW] Navigation attempt {attempt+1} failed. Retrying in {2 ** attempt}s...")
                    await asyncio.sleep(2 ** attempt)
            
            return {}        
        except Exception as e:
            print(f"🚨 Navigation Error: {e}")
            return {"error": f"Navigation failed: {e}"}


    # --- NODE 3: Login ---
    async def request_login(self, state: JobApplicationState):
        await self._emit(state, "Authenticating")
        prefs = state["preferences"]
        creds = state.get("credentials")
        user_id = str(state["user"].id)

        print("--- [HW] Requesting Login ---")
        
        if prefs.is_full_automation and creds["hellowork"]:
            print("🔐 Full Automation: Attempting auto-login...")

            login_plain = None
            pass_plain = None

            try:
                # ✅ RETRY UNIT 1: Open login modal + verify email input appears
                for attempt in range(3):
                    try:
                        await self.page.locator('[data-cy="headerAccountMenu"]').click()
                        await self.page.locator('[data-cy="headerAccountLogIn"]').click()
                        await self.page.wait_for_selector('input[name="email2"]', state="visible", timeout=30000)
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Could not open the login modal."}
                        print(f"⚠️ [HW] Login modal attempt {attempt+1} failed. Reloading...")
                        await self.page.reload(wait_until="networkidle")
                        await asyncio.sleep(2 ** attempt)

                login_plain = await self.encryption_service.decrypt(creds["hellowork"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["hellowork"].password_encrypted)

                # ✅ RETRY UNIT 2: Fill credentials + submit
                for attempt in range(3):
                    try:
                        await self.page.locator('input[name="email2"]').clear()
                        await self.page.locator('input[name="email2"]').fill(login_plain)
                        await self.page.locator('input[name="password2"]').clear()
                        await self.page.locator('input[name="password2"]').fill(pass_plain)
                        await self.page.locator('button[type="button"][class="profile-button"]').click()
                        await self.page.wait_for_selector('a[data-cy="cpMenuDashboard"]', state="visible", timeout=60000)
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Could not submit credentials."}
                        await asyncio.sleep(2 ** attempt)

                # ✅ RETRY UNIT 3: Proof of login
                for attempt in range(3):
                    try:
                        await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                        await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=20000)
                        break
                    except Exception as e:
                        if attempt == 2:
                            return {"error": "Login failed. Please check your HelloWork credentials in your settings."}
                        print(f" [HW] Post-login verification attempt {attempt+1} failed. Error: {e}. Retrying...")
                        await asyncio.sleep(2 ** attempt)

                await self._save_auth_state(user_id)
                print("✅ Auto-login successful")                
                return {}

            except Exception as e:
                return {"error": f"Failed to log into HelloWork. Check credentials: {e}."}
            
            finally:
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
                await self._save_auth_state(user_id)
                return {}
            except Exception:
                return {"error": "Manual login timed out."}


    # --- NODE 4: Search ---
    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state, "Searching for Jobs") 

        search_entity = state["job_search"]
        job_title = search_entity.job_title
        contract_types = getattr(search_entity, 'contract_types', [])
        min_salary = getattr(search_entity, 'min_salary', 0)
        location = getattr(search_entity, 'location', "")

        print(f"--- [HW] Searching for: {job_title} ---")
        try:
            # ✅ RETRY UNIT 1: Search input + submit
            for attempt in range(3):
                try:
                    await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=15000)
                    await self.page.locator('input[id="k"]').fill(job_title)
                    if location and location.strip() != "":
                        await self.page.locator('input[id="l"]').fill(location)
                    await self.page.keyboard.press("Enter")
                    await self.page.wait_for_load_state("networkidle")
                    break
                except Exception as e:
                    if attempt == 2:
                        return {"error": f"Failed to search HelloWork: {e}"}
                    print(f"⚠️ [HW] Search attempt {attempt+1} failed. Retrying in {2 ** attempt}s...")
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()

            if contract_types or min_salary > 0:
                await self._apply_filters(contract_types, min_salary)

            # ✅ Verify results appeared (logical check — no retry needed)
            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                print("✅ Search results loaded successfully.")
            except Exception:
                return {"error": "No new matching jobs were found for this search today."}

            return {}
        except Exception as e:
            return {"error": f"Failed to search HelloWork: {e}"}


    # --- NODE 5: Scrape Jobs ---
    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data")
        print("--- [HW] Scraping Jobs ---")

        user_id = state["user"].id
        search_id = state["job_search"].id
        found_job_entities = []
        
        worker_job_limit = 1 or state.get("worker_job_limit", 5) 
        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=14)
        ignored_hashes = hash_result.value if hash_result.is_success else set()
        
        print(f"🎯 Target: {worker_job_limit} jobs. 🛡️ Ignored Hashes: {len(ignored_hashes)}")

        page_number = 1
        max_pages = 20
        
        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:
                print(f"📄 [HW] Processing Page {page_number}...")
                
                cards_locator = self.page.locator(self.CARD_SELECTOR)
                try:
                    await cards_locator.first.wait_for(state='visible', timeout=30000)
                except Exception:
                    if page_number == 1: 
                        print(f"🕵️ [HW] No results found on Page 1 for {state.get('job_search').job_title}")
                        return {"found_raw_offers": []}
                    print(f"🏁 [HW] No more cards found on page {page_number}. Ending scrape.")
                    break
                    
                count = await cards_locator.count()
                search_url = self.page.url
                
                for i in range(count):
                    if len(found_job_entities) >= worker_job_limit: 
                        break
                    
                    try:
                        card = self.page.locator(self.CARD_SELECTOR).nth(i)

                        # ✅ Scroll card into viewport before reading its content
                        await card.scroll_into_view_if_needed()

                        raw_company, raw_title, raw_location = await self.get_raw_job_data(card)
                        print(f"---[HW JOB DATA - user: {user_id}]---")
                        print(f"[HW - {user_id} - Company]: {raw_company}")
                        print(f"[HW - {user_id} - Title]: {raw_title}")
                        print(f"[HW - {user_id} - Location]: {raw_location}")

                        if not raw_title:
                            print("    ⚠️ Missing title, skipping card.")
                            continue

                        # ✅ RETRY: card.click() + networkidle as one unit
                        # Card can go stale between locating and clicking on slow pages
                        click_success = False
                        for attempt in range(3):
                            try:
                                card = self.page.locator(self.CARD_SELECTOR).nth(i)
                                await card.click()
                                await self.page.wait_for_load_state("networkidle")
                                click_success = True
                                break
                            except Exception as e:
                                if attempt == 2:
                                    print(f"    ⚠️ Card click failed after 3 attempts. Skipping. Error: {e}")
                                    break
                                await asyncio.sleep(2 ** attempt)

                        if not click_success:
                            continue

                        current_url = self.page.url      
                            
                        fast_hash = self._generate_fast_hash(raw_company, raw_title, str(user_id))
                        if fast_hash in ignored_hashes:
                            print(f"     ⏩ Skipping duplicate: {raw_title} at {raw_company}")
                            # ✅ Use nav_back helper with retry + verification
                            if not await self._nav_back_to_search(search_url):
                                break
                            continue

                        # Extract Description
                        try:
                            desc_el = self.page.locator('div[id="content"]')                          
                            if await desc_el.count() == 0: 
                                desc_el = self.page.locator('div[data-id-storage-local-storage-key-param="visited-offers"]')
                            job_desc = await desc_el.inner_text()
                        except Exception:
                            job_desc = ""

                        # ✅ RETRY: Apply button click only
                        # The internal/external form detection logic stays intact after the retry
                        moving_to_form_btn = self.page.locator('a[data-cy="applyButtonHeader"]').first
                        if await moving_to_form_btn.count() > 0:
                            click_ok = False
                            for attempt in range(3):
                                try:
                                    await moving_to_form_btn.click()
                                    click_ok = True
                                    break
                                except Exception as e:
                                    if attempt == 2:
                                        print(f"    ⚠️ Apply button click failed after 3 attempts. Error: {e} Skipping form interaction.")
                                        break
                                    await asyncio.sleep(2 ** attempt)

                            if click_ok:
                                try:                                
                                    await self.page.wait_for_selector(
                                        selector='button[data-cy="applyButton"]', 
                                        timeout=3000, 
                                        state='visible'
                                    )
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
                                    print(f"    📦 Current batch size: {len(found_job_entities)}/{worker_job_limit}")

                        # ✅ Use nav_back helper with retry + verification
                        if not await self._nav_back_to_search(search_url):
                            break
                        
                    except Exception as e:
                        print(f"⚠️ Error processing card {i} on page {page_number}: {e}")
                        if not await self._nav_back_to_search(search_url):
                            break
                        continue

                if len(found_job_entities) >= worker_job_limit: 
                    break
                
                if not await self._handle_hw_pagination(page_number):
                    break
                page_number += 1

        except Exception as e:
            return {"error": f"[HW] Fatal Scraping Error: {e}"}

        if not found_job_entities:
            print("⚠️ Scanned jobs across pages, but none were valid for auto-application.")
            return {"found_raw_offers": []}
        
        print(f"🎉 [HW] Scraping Complete! Handing {len(found_job_entities)} jobs back to Master.")
        return {"found_raw_offers": found_job_entities}


    # --- NODE 7: Submit Applications ---
    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state, "Submitting Applications")
        print("--- [HW] Submitting Applications ---")
        jobs_to_submit = state.get("processed_offers", [])
        user = state["user"]

        assigned_submit_limit = state.get("worker_job_limit", 5) 

        hw_jobs = [job for job in jobs_to_submit if job.job_board == JobBoard.HELLOWORK and job.status == ApplicationStatus.APPROVED]

        if not hw_jobs: 
            return {"error": "no job to submit for HW worker"}

        successful_submissions = []
        i = 0
        for offer in hw_jobs:
            if len(successful_submissions) >= assigned_submit_limit:
                print(f"🛑 [HW] Reached assigned submission limit ({assigned_submit_limit}). Halting further submissions.")
                break

            print(f"📝 Applying to: {offer.job_title} ({i+1}/{len(hw_jobs)})")
            try:
                # ✅ RETRY: goto + applyButtonHeader click + Firstname input as one unit
                # This is the form entry point — WAF hot zone
                form_loaded = False
                for attempt in range(3):
                    try:
                        await self.page.goto(offer.form_url, wait_until="commit", timeout=60000)

                        moving_to_form_btn = self.page.locator('a[data-cy="applyButtonHeader"]')
                        if await moving_to_form_btn.count() > 0:
                            await moving_to_form_btn.click()

                        await self.page.wait_for_selector('input[name="Firstname"]', state="visible", timeout=15000)
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

                await self.page.locator('input[name="LastName"]').fill(user.lastname)

                if user.resume_path:
                    print("⬇️ Downloading resume from cloud to RAM...")
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"
                    await self.page.locator('[data-cy="cv-uploader-input"]').set_input_files({
                        "name": human_name,
                        "mimeType": "application/pdf",
                        "buffer": resume_bytes
                    })
                
                if offer.cover_letter:
                    await self.page.locator('[data-cy="motivationFieldButton"]').click()
                    await self.page.wait_for_selector('textarea[name="MotivationLetter"]', state="visible", timeout=10000)
                    await self.page.locator('textarea[name="MotivationLetter"]').fill(offer.cover_letter)

                # ✅ NO RETRY on submit click — would cause duplicate submission risk
                submit_btn = self.page.locator('[data-cy="submitButton"]')
                if await submit_btn.is_visible():
                    await submit_btn.click()

                    # Wait directly for notification
                    try:
                        notification = self.page.locator('[data-intersect-name-value="notification"]')
                        await notification.wait_for(state="attached", timeout=45000)

                        if await notification.count() > 0:
                            use_tag = notification.locator('svg use[href*="badges"]')
                            if await use_tag.count() > 0:
                                href_value = await use_tag.get_attribute("href")
                                if href_value and "error" in href_value.lower():
                                    print(f"❌ Submission of {offer.form_url} blocked by HelloWork error badge.")
                                    i += 1
                                    continue

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
        
        return {"submitted_offers": successful_submissions}


    # --- NODE 9: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up")
        await self.force_cleanup()
        return {}


    # =========================================================================
    # GRAPH
    # =========================================================================

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
            {"start": "start", "start_with_session": "start_with_session"}
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