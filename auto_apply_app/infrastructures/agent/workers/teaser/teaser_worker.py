# auto_apply_app/infrastructures/agent/workers/teaser/teaser_worker.py
import hashlib
import os
import asyncio
import pdfplumber
from typing import Optional
from langgraph.graph import StateGraph, END
from playwright_stealth import Stealth
from playwright.async_api import Locator, async_playwright, Page, Browser, BrowserContext, Playwright
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI



# 1. imports from Domain
from auto_apply_app.domain.value_objects import ApplicationStatus, JobBoard, ContractType
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.entities.user_preferences import UserPreferences

# 2. Imports from Infrastructure
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.application.use_cases.agent_state_use_cases import GetAgentStateUseCase
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort 
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort 
from auto_apply_app.application.use_cases.agent_use_cases import GetIgnoredHashesUseCase

# 🚨 NEW: Human behavior helpers
from auto_apply_app.infrastructures.agent.human_behavior import (
    human_delay,
    human_type,
    human_click,
    human_warmup,
)


class JobTeaserWorker:

    # 🚨 CLASS CONSTANT: Single source of truth for the job card selector
    CARD_SELECTOR = 'ul[data-testid="job-ads-wrapper"] > li'

    # At the top of teaser_worker.py, near the class
    TEASER_CONTRACT_NAME_MAP = {
        "CDI":         "cdi",
        "CDD":         "cdd",
        "Stage":       "internship",
        "Alternance":  "alternating",
        "freelance":   "freelance",
    }

    def __init__(self, 
                 get_ignored_hashes: GetIgnoredHashesUseCase,
                 encryption_service: EncryptionServicePort,
                 file_storage: FileStoragePort,
                 get_agent_state: GetAgentStateUseCase
                ):
        
        # Static Dependencies
        self.get_ignored_hashes = get_ignored_hashes       
        self.encryption_service = encryption_service
        self.base_url = "https://www.jobteaser.com/fr"
        self.file_storage = file_storage
        self.get_agent_state = get_agent_state
        
        # Runtime State (Lazy Initialization)
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Progress callback (set per-run by master)
        self._progress_callback = None
        self._source_name = "JOBTEASER"


    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_llm(self, preferences: UserPreferences) -> BaseChatModel:
        return ChatGoogleGenerativeAI(
            api_key=self.api_keys.get("gemini"),
            model="gemini-3-pro-preview",
            temperature=preferences.llm_temperature
        )

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
        b = "jobteaser"
        raw_string = f"{c}_{t}_{b}_{u}"
        return hashlib.md5(raw_string.encode()).hexdigest()

    def _get_session_file_path(self, user_id: str) -> str:
        directory = os.path.join(os.getcwd(), "tmp", "sessions")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{user_id}_jobteaser_session.json")

    async def _save_auth_state(self, user_id: str):
        if self.context:
            path = self._get_session_file_path(user_id)
            await self.context.storage_state(path=path)
            print(f"🔒 [JOBTEASER] Session saved securely for user {user_id}")

    def _get_auth_state_path(self, user_id: str) -> str | None:
        path = self._get_session_file_path(user_id)
        if os.path.exists(path):
            return path
        return None

    async def _handle_cookies(self):
        print("Checking for Didomi cookies...")
        try:
            # Target the specific agree button
            agree_button_selector = '#didomi-notice-agree-button'
            
            # Wait for the button to be visible (not just attached to the DOM)
            await self.page.wait_for_selector(agree_button_selector, state='visible', timeout=5000)
            
            # Click it natively
            await self.page.click(agree_button_selector)
            print("✓ Clicked 'Good for me' cookie button.")
            
            # Wait a brief moment for the banner's close animation to finish
            await self.page.wait_for_timeout(1000)
            
        except Exception:
            print("info: No cookie popup detected (or already gone).")

    

    async def force_cleanup(self):
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

    async def _get_job_attribute(self, selector: str, default_value: str = None):
        try:
            await self.page.wait_for_selector(selector, state='attached', timeout=5000)
            text = await self.page.locator(selector).first.inner_text()
            return text.strip() 
        except Exception:
            return default_value

    def _extract_resume(self, resume_path: str) -> str:
        text = ""
        try:
            with pdfplumber.open(resume_path) as pdf:
                for p in pdf.pages:
                    text += p.extract_text() + "\n"
        except Exception as e:
            print(f"Error reading resume: {e}")
        return text


    # --- HELPER: Extract card-level data (company, title, location) ---
    async def get_raw_job_data(self, card: Locator) -> tuple[str, str, str]:
        raw_company, raw_title, raw_location = "", "", ""

        try:
            # Company name appears twice per card (signature + header) — first one is fine
            company_el = card.locator('[data-testid="jobad-card-company-name"]').first
            raw_company = (await company_el.text_content() or "").strip()
        except Exception as e:
            print(f"    ⚠️ Company scrape failed: {e}")

        try:
            title_link = card.locator('h3 a').first
            raw_title = (await title_link.text_content() or "").strip()
        except Exception as e:
            print(f"    ⚠️ Title scrape failed: {e}")

        try:
            location_el = card.locator('[data-testid="jobad-card-location"] span').first
            raw_location = (await location_el.text_content() or "").strip()
        except Exception as e:
            print(f"    ⚠️ Location scrape failed: {e}")

        return raw_company if raw_company else "No name", raw_title, raw_location


    # --- HELPER: Pagination ---
    async def _handle_teaser_pagination(self, page_number: int) -> bool:
        """
        JobTeaser pagination: the LAST element in the pagination nav is either
        an <a> (more pages available) or a <button disabled> (last page).
        """
        try:
            nav = self.page.locator('nav[data-testid="job-ads-pagination"]')
            if await nav.count() == 0:
                print("🔚 [JOBTEASER] No pagination nav. Single page of results.")
                return False

            next_control = nav.locator('> *:last-child')
            if await next_control.count() == 0:
                print("🔚 [JOBTEASER] No next control found.")
                return False

            tag_name = await next_control.evaluate("el => el.tagName.toLowerCase()")
            if tag_name == "button":
                print("🔚 [JOBTEASER] Next button disabled. Reached last page.")
                return False

            if tag_name != "a":
                print(f"🔚 [JOBTEASER] Unexpected last pagination element: <{tag_name}>")
                return False

            print(f"➡️ [JOBTEASER] Moving to page {page_number + 1}...")
            await human_delay(1500, 3500)  # 🚨 NEW: pause before pagination

            for attempt in range(3):
                try:
                    await next_control.click()
                    await self.page.wait_for_load_state("networkidle")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=45000)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"⚠️ [JOBTEASER] Pagination failed after 3 attempts: {e}")
                        return False
                    await asyncio.sleep(2 ** attempt)
            
            await self._handle_cookies() # ✅ ADDED
            return True

        except Exception as e:
            print(f"⚠️ [JOBTEASER] Pagination error: {e}")
            return False


    # --- HELPER: Apply Search Filters (JobTeaser) ---
    async def _apply_filters(self, contract_types: list[ContractType], location: str = ""):
        """
        Applies JobTeaser's filters in order:
          1. Contract types (multi-select dropdown — click checkboxes by name attribute)
          2. Location (autocomplete — must click first suggestion)
          3. "Candidature simplifiée" via the secondary filters modal
        
        Note: JobTeaser has no min_salary filter — that param is omitted.
        """
        try:
            # ---------- 1. CONTRACT TYPES ----------
            if contract_types:
                try:
                    await self.page.wait_for_selector(
                        'div[data-testid="job-ads-contract-filter"] > button',
                        state="visible",
                        timeout=15000,
                    )
                    contract_btn = self.page.locator(
                        'div[data-testid="job-ads-contract-filter"] > button'
                    ).first
                    await human_click(contract_btn)
                    await self.page.wait_for_selector(
                        'ul#multi-select-container-options',
                        state="visible",
                        timeout=10000,
                    )

                    for contract in contract_types:
                        teaser_name = self.TEASER_CONTRACT_NAME_MAP.get(str(contract.value))
                        if not teaser_name:
                            print(f"  ⚠️ [JOBTEASER] No mapping for contract '{contract.value}', skipping.")
                            continue
                        try:
                            checkbox = self.page.locator(
                                f'ul#multi-select-container-options input[name="{teaser_name}"]'
                            )
                            if await checkbox.count() > 0 and await checkbox.get_attribute("aria-checked") == "false":
                                cb_id = await checkbox.get_attribute("id")
                                label = self.page.locator(f'ul#multi-select-container-options label[for="{cb_id}"]')
                                await human_delay(300, 700)
                                await label.click()
                                await self.page.wait_for_load_state("networkidle")
                                print(f"  ✓ Contract checked: {contract.value}")
                        except Exception as e:
                            print(f"  ⚠️ Could not check contract '{contract.value}': {e}")

                    # Apply contract filters before moving to location (they can cause a full page reload)
                    await self.page.keyboard.press("Enter")
                    await self.page.wait_for_load_state("networkidle")
                except Exception as e:
                    print(f"  ⚠️ [JOBTEASER] Contract filter step failed: {e}")

            # ---------- 2. LOCATION ----------
            if location and location.strip():
                try:
                    await self.page.wait_for_selector('input#location-filter', state="visible", timeout=10000)
                    location_input = self.page.locator('input#location-filter')
                    await human_click(location_input)
                    await human_delay(200, 500)
                    await human_type(location_input, location.strip())
                    await human_delay(800, 1500)  # let suggestions render

                    # Click the first real suggestion (skip the "À l'étranger" abroad option)
                    first_suggestion = self.page.locator(
                    'div[class*="LocationFilter_main"] div[class*="Dropdown_main"] button'
                    ).first
                    await first_suggestion.wait_for(state="visible", timeout=30000)
                    await human_click(first_suggestion)
                    await self.page.wait_for_load_state("networkidle")
                    print(f"  ✓ Location selected: {location}")
                except Exception as e:
                    print(f"  ⚠️ [JOBTEASER] Location filter step failed: {e}")

            # ---------- 3. SECONDARY FILTERS MODAL — "Candidature simplifiée" ----------
            for attempt in range(3):
                try:
                    modal_open_btn = self.page.locator(
                        'button[data-testid="job-ads-secondary-filters-modal-open-button"]'
                    )
                    await modal_open_btn.wait_for(state="visible", timeout=15000)
                    await human_click(modal_open_btn)
                    await self.page.wait_for_selector(
                        'div[data-testid="job-ads-candidacy-type-checklist"]',
                        state="visible",
                        timeout=10000,
                    )
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"⚠️ [JOBTEASER] Could not open secondary filters modal: {e}")
                        raise
                    await asyncio.sleep(2 ** attempt)

            try:
                simplifiee_id = "job-ads-candidacy-type-filter-INTERNAL"
                checkbox = self.page.locator(f'input#{simplifiee_id}')
                if await checkbox.count() > 0 and await checkbox.get_attribute("aria-checked") == "false":
                    await human_delay(300, 700)
                    await self.page.locator(f'label[for="{simplifiee_id}"]').click()
                    print("  ✓ Checked: Candidature simplifiée")
            except Exception as e:
                print(f"  ⚠️ Could not select 'Candidature simplifiée': {e}")

            await human_delay(800, 1800)

            for attempt in range(3):
                try:
                    apply_btn = self.page.locator(
                        'button[data-testid="job-ads-secondary-filters-apply-button"]'
                    )
                    await apply_btn.click()
                    await self.page.wait_for_load_state("networkidle")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="attached", timeout=15000)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"⚠️ [JOBTEASER] Filter modal submit failed after 3 attempts: {e}")
                        raise
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies() # ✅ ADDED

        except Exception as e:
            print(f"❌ Error applying filters: {e}")
            raise


    # --- HELPER: Nav Back with Retry + Verification ---
    async def nav_back(self, url: str) -> bool:
        try:
            if await self.page.locator('a[title="Retourner aux résultats"]').count() > 0:
                await self.page.locator('a[title="Retourner aux résultats"]').click()
                await self.page.wait_for_load_state("networkidle")
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
                await self._handle_cookies()
                await human_delay(800, 2000)  # 🚨 NEW: pause to "read" results
                return True
        except Exception:
            pass

        for attempt in range(3):
            try:
                await self.page.goto(url, wait_until="networkidle")
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
                await self._handle_cookies()
                await human_delay(800, 2000)  # 🚨 NEW
                return True
            except Exception as e:
                if attempt == 2:
                    print(f"    ⚠️ Could not return to search results after 3 attempts: {e}")
                    try:
                        await self.page.reload(wait_until="networkidle")
                        await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                        await self._handle_cookies()
                        return True
                    except Exception:
                        return False
                print(f"    ⚠️ Nav back attempt {attempt+1} failed. Retrying...")
                await asyncio.sleep(2 ** attempt)
        return False


    async def route_node_exit(self, state: JobApplicationState) -> str:
        if state.get("error"):
            print(f"🛑 [WTTJ Worker] Circuit Breaker Tripped: {state['error']}")
            return "error"

        try:
            user_id = state["user"].id
            state_result = await self.get_agent_state.execute(user_id)
            if state_result.is_success and state_result.value.is_shutdown:
                print("🛑 [WTTJ Worker] User Kill Switch Detected! Aborting gracefully...")
                return "error"
        except Exception as e:
            print(f"⚠️ [WTTJ Worker] Failed to check DB for agent state: {e}")
            pass

        return "continue"

    def route_action_intent(self, state: JobApplicationState):
        intent = state.get("action_intent", "SCRAPE") 
        if intent == "SUBMIT":
            print("🛤️ [JOBTEASER] Routing to SUBMIT track...")
            return "start_with_session"
        print("🛤️ [JOBTEASER] Routing to SCRAPE track...")
        return "start"


    # =========================================================================
    # NODES
    # =========================================================================

    # --- NODE 1: Start Session ---
    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser") 
        print(f"--- [JOBTEASER] Starting session for {state['user'].firstname} ---")
        
        preferences = state["preferences"]

        # 🚨 NEW: Pull identity from state
        fingerprint = state.get("user_fingerprint")
        #proxy_config = state.get("proxy_config")
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless= preferences.browser_headless, 
                args=['--disable-blink-features=AutomationControlled']
            )

            # 🚨 NEW: Build context kwargs from fingerprint + proxy
            context_kwargs = {}
            if fingerprint:
                context_kwargs.update(fingerprint.to_playwright_context_args())
                print(f"   🪪 Applying fingerprint: {fingerprint.platform} / {fingerprint.viewport_width}x{fingerprint.viewport_height}")
            else:
                context_kwargs["user_agent"] = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                )

            # if proxy_config:
            #     context_kwargs["proxy"] = {
            #         "server": proxy_config["server"],
            #         "username": proxy_config["username"],
            #         "password": proxy_config["password"],
            #     }
            #     print(f"   🌐 Routing through proxy: {proxy_config['server']}")

            self.context = await self.browser.new_context(**context_kwargs)

            # 🚨 NEW: Inject fingerprint init script BEFORE any page loads
            if fingerprint:
                await self.context.add_init_script(fingerprint.to_init_script())
            
            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)
            self.page = await self.context.new_page()
            
            return {}
        except Exception as e:
            print(f"Session Error: {e}")
            return {"error": "Failed to start the secure browsing session."}


    # --- NODE 1 Bis: Boot & Inject Session (Submit Track) ---
    async def start_session_with_auth(self, state: JobApplicationState):
        await self._emit(state, "Initializing Secure Browser")
        print("--- [JOBTEASER] Booting Browser (Session Injection) ---")
        user_id = str(state["user"].id)

        # 🚨 NEW: Pull identity from state
        fingerprint = state.get("user_fingerprint")
        #proxy_config = state.get("proxy_config")

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless= state["preferences"].browser_headless,
                args=['--disable-blink-features=AutomationControlled']
            )

            session_path = self._get_auth_state_path(user_id)

            # 🚨 NEW: Build context kwargs from fingerprint + proxy + saved session
            context_kwargs = {}
            if fingerprint:
                context_kwargs.update(fingerprint.to_playwright_context_args())
                context_kwargs.update({
                    "device_scale_factor": fingerprint.device_scale_factor,
                    "has_touch": False,
                    "is_mobile": False,
                })
            else:
                context_kwargs["user_agent"] = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                )

            if session_path:
                print(f"🔓 Found saved session for user {user_id}. Injecting cookies...")
                context_kwargs["storage_state"] = session_path
            else:
                print("⚠ No session found. Booting fresh context...")

            # if proxy_config:
            #     context_kwargs["proxy"] = {
            #         "server": proxy_config["server"],
            #         "username": proxy_config["username"],
            #         "password": proxy_config["password"],
            #     }
            #     print(f"   🌐 Routing through proxy: {proxy_config['server']}")

            self.context = await self.browser.new_context(**context_kwargs)

            # 🚨 NEW: Inject fingerprint init script
            if fingerprint:
                await self.context.add_init_script(fingerprint.to_init_script())

            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)
            self.page = await self.context.new_page()

            # ✅ RETRY: goto + verify logged-in state as one unit
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=120000)
                    await self._handle_cookies() # ✅ ADDED
                    # Confirm session is valid by looking for the logged-in greeting
                    # (CSS Modules hash on Greeting_firstWord — use prefix matcher)
                    await self.page.wait_for_selector(
                        'span[class*="Greeting_firstWord"]',
                        state="attached",
                        timeout=45000,
                    )
                    break
                except Exception as e:
                    if attempt == 2:
                        return {"error": f"Failed to reach JobTeaser after 3 attempts: {str(e)}"}
                    print(f"⚠️ [JOBTEASER] Auth boot attempt {attempt+1} failed. Retrying in {2 ** attempt}s...")
                    await asyncio.sleep(2 ** attempt)

            await human_warmup(self.page, self.base_url)  # 🚨 NEW: human warmup

            # Click the "Offres" nav link to reach the job search page
            # Uses partial class match (Nav_app-Nav__link has a CSS Modules hash suffix)
            # combined with inner text "Offres" for stability
            offres_link = self.page.locator(
                'a[class*="Nav_app-Nav__link"]:has-text("Offres")'
            ).first
            await offres_link.wait_for(state="visible", timeout=15000)
            await human_click(offres_link)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_selector(
                'input[id="job-ads-autocomplete-keyword-search"]',
                state="visible",
                timeout=60000,
            )


            search_entity = state["job_search"]
            job_title = search_entity.job_title
            contract_types = getattr(search_entity, 'contract_types', [])
            location = getattr(search_entity, 'location', "")

            print(f"--- [JOBTEASER] Dummy Searching for: {job_title} ---")
            try:
                # Replicate the exact search_jobs flow so behavioral fingerprint
                # looks consistent with what an organic user would do.
                search_field = self.page.locator('input[id="job-ads-autocomplete-keyword-search"]')

                # Autocomplete needs a real click to activate the suggestion dropdown
                await human_click(search_field)
                await human_delay(200, 500)

                await human_type(search_field, job_title)
                await human_delay(800, 1500)  # let suggestions render

                # Click the first suggestion
                first_suggestion = self.page.locator(
                    '[id^="job-ads-autocomplete-suggestion-"]'
                ).first
                await first_suggestion.wait_for(state="visible", timeout=8000)
                await human_click(first_suggestion)
                await self.page.wait_for_load_state("networkidle")

                # Apply filters (always — at minimum we want "Candidature simplifiée")
                await self._apply_filters(contract_types, location)

                await self.page.wait_for_load_state("networkidle")

                # Confirm results loaded — gives us a stable post-warmup state
                try:
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="attached", timeout=30000)
                    print("✅ [JOBTEASER] Dummy search complete — session warmed up.")
                except Exception:
                    # No results is fine — we're not actually scraping here, just looking human
                    print("⚠️ [JOBTEASER] Dummy search ran but no cards found. Continuing.")

                return {}
            except Exception as e:
                print(f"⚠️ Initial search failed during session boot: {e}")
                return {}

        except Exception as e:
            print(f"Browser Auth Initialization Error: {e}")
            return {"error": f"Failed to initialize JobTeaser browser with session: {str(e)}"}


    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board")
        print("--- [JOBTEASER] Navigating ---")
        try:
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=90000)
                    await self._handle_cookies() # ✅ UNCOMMENTED
                    await self.page.wait_for_selector('button[id="UnloggedUserDropdownButton"]', state="visible", timeout=30000)
                    break
                except Exception as e:
                    if attempt == 2:
                        return {"error": "Could not reach JobTeaser. The job board might be down or undergoing maintenance."}
                    print(f"⚠️ [JOBTEASER] Navigation attempt {attempt+1} failed. Error: {e}. Retrying in {2 ** attempt}s...")
                    await asyncio.sleep(2 ** attempt)

            await human_warmup(self.page, self.base_url)  # 🚨 NEW: warmup after navigation
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
        
        print("--- [JOBTEASER] Requesting Login ---")
        
        await self._handle_cookies() # ✅ ADDED

        if prefs.is_full_automation and creds["jobteaser"]:
            print("🔐 [JOBTEASER] Full Automation: Attempting auto-login...")

            login_plain = None
            pass_plain = None

            try:
                # ✅ RETRY UNIT 1: Open dropdown → click signin link → click JobTeaser Connect → wait for email input
                for attempt in range(3):
                    try:
                        # Step 1: open the unlogged dropdown
                        await human_click(self.page.locator('button#UnloggedUserDropdownButton'))
                        await self.page.wait_for_selector('a[data-testid="signinLink"]', state="visible", timeout=10000)

                        # Step 2: click "Connexion" link in the dropdown
                        await human_click(self.page.locator('a[data-testid="signinLink"]'))
                        await self.page.wait_for_load_state("networkidle")

                        # Step 3: click the "JobTeaser Connect" anchor
                        connect_btn = self.page.locator('a[href*="/users/auth/connect"]')
                        await self.page.wait_for_selector('a[href*="/users/auth/connect"]', state="visible", timeout=15000)
                        await human_click(connect_btn)
                        await self.page.wait_for_load_state("networkidle")

                        # Step 4: wait for the actual login form
                        await self.page.wait_for_selector('input#email', state="visible", timeout=60000)
                        break
                    except Exception as e:
                        if attempt == 2:
                            return {"error": "Login failed. Could not reach the login form."}
                        print(f"⚠️ [JOBTEASER] Login flow attempt {attempt+1} failed. Error: {e}. Reloading...")
                        await self.page.reload(wait_until="networkidle")
                        await asyncio.sleep(2 ** attempt)

                login_plain = await self.encryption_service.decrypt(creds["jobteaser"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["jobteaser"].password_encrypted)

                # ✅ RETRY UNIT 2: Fill credentials with HUMAN typing + submit
                for attempt in range(3):
                    try:
                        await human_delay(300, 700)
                        await human_type(self.page.locator('input#email'), login_plain)

                        await human_delay(400, 900)

                        await human_type(self.page.locator('input#passwordInput'), pass_plain)

                        await human_delay(600, 1500)

                        # JobTeaser uses a plain submit button inside the sign-in form
                        submit_btn = self.page.locator('form[data-e2e="sign-in-form"] button[type="submit"]')
                        await submit_btn.click()
                        await self.page.wait_for_load_state("networkidle", timeout=60000)
                        break
                    except Exception as e:
                        if attempt == 2:
                            return {"error": "Login failed. Could not submit credentials."}
                        print(f"⚠️ [JOBTEASER] Credential submission attempt {attempt+1} failed. Error: {e}. Retrying in {2 ** attempt}s...")
                        await asyncio.sleep(2 ** attempt)

                # ✅ RETRY UNIT 3: Proof of login
                for attempt in range(3):
                    try:
                        # Use class prefix matcher — CSS Modules hashes change on rebuild
                        await self.page.wait_for_selector('span[class*="Greeting_firstWord"]', state="attached", timeout=60000)
                        break
                    except Exception as e:
                        if attempt == 2:
                            return {"error": "Login failed. Please check your JobTeaser credentials in your settings."}
                        print(f"⚠️ [JOBTEASER] Post-login verification attempt {attempt+1} failed. Error: {e}. Retrying in {2 ** attempt}s...")
                        await asyncio.sleep(2 ** attempt)

                # preparing for search page — click the "Offres" nav link to reach the job search page

                offres_link = self.page.locator(
                    'a[class*="Nav_app-Nav__link"]:has-text("Offres")'
                ).first
                await offres_link.wait_for(state="visible", timeout=15000)
                await human_click(offres_link)
                await self.page.wait_for_load_state("networkidle")

                print("✅ [JOBTEASER] Auto-login successful")
                await self._save_auth_state(user_id)
                return {}

            except Exception as e:
                print(f"❌ [JOBTEASER] Auto-login failed: {e}")
                return {"error": "Failed to log into JobTeaser. Please check your credentials."}
              

            finally:
                if login_plain is not None:
                    del login_plain
                if pass_plain is not None:
                    del pass_plain

        else:
            try:
                await self.page.locator('button#UnloggedUserDropdownButton').click()
                await self.page.locator('a[data-testid="signinLink"]').click()
                print("⚠ ACTION REQUIRED: Manual login required (waiting 90s)...")
                await asyncio.sleep(90)
                await self._save_auth_state(user_id)
                return {}
            except Exception as e:
                print(f"Login Error: {e}")
                return {"error": "Manual login timed out. We didn't detect a successful login."}


    # --- NODE 4: Search ---
    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state, "Searching for Jobs")

        search_entity = state["job_search"]
        job_title = search_entity.job_title
        contract_types = getattr(search_entity, 'contract_types', [])
        location = getattr(search_entity, 'location', "")

        print(f"--- [JOBTEASER] Searching for: {job_title} ---")

        try:
            await human_warmup(self.page, self.base_url)

            # ✅ RETRY UNIT 1: Click search input → type → click first suggestion
            for attempt in range(3):
                try:
                    search_input_selector = 'input[id="job-ads-autocomplete-keyword-search"]'
                    await self.page.wait_for_selector(search_input_selector, state="visible", timeout=90000)
                    search_field = self.page.locator(search_input_selector)

                    # Autocomplete needs a real click to activate the dropdown
                    await human_click(search_field)
                    await human_delay(200, 500)

                    await human_type(search_field, job_title)
                    await human_delay(800, 1500)  # let suggestions render

                    # Click the first suggestion (id starts with "job-ads-autocomplete-suggestion-")
                    # Suggestions are role="button" elements with auto-generated IDs like
                    # "job-ads-autocomplete-suggestion-0-0", "...-0-1", etc.
                    first_suggestion = self.page.locator(
                        '[id^="job-ads-autocomplete-suggestion-"]'
                    ).first
                    await first_suggestion.wait_for(state="visible", timeout=8000)
                    await human_click(first_suggestion)
                    await self.page.wait_for_load_state("networkidle")
                    break
                except Exception as e:
                    if attempt == 2:
                        return {"error": f"Failed to execute search for '{job_title}' on JobTeaser."}
                    print(f"⚠️ [JOBTEASER] Search field attempt {attempt+1} failed. Error: {e}. Reloading...")
                    await self.page.reload(wait_until="networkidle")
                    await asyncio.sleep(2 ** attempt)

            # Apply filters (always — at minimum we want "Candidature simplifiée")
            await self._apply_filters(contract_types, location)

            await self.page.wait_for_load_state("networkidle")

            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="attached", timeout=90000)
                print("✅ Search results loaded successfully.")
            except Exception:
                return {"error": "No new matching jobs were found for this search today."}

            await self._handle_cookies() # ✅ ADDED
            return {}

        except Exception as e:
            print(f"Search Error: {e}")
            return {"error": f"Failed to execute search for '{job_title}' on JobTeaser."}

    # --- NODE 5: Scrape Jobs ---
    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data")
        print("--- [JOBTEASER] Scraping Jobs ---")

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
        print(f"🎯 Target: Scraping up to {worker_job_limit} new TEASER jobs.")

        page_number = 1
        max_pages = 20

        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:
                print(f"📄 [JOBTEASER] Processing Page {page_number}...")

                try:
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
                except Exception:
                    print(f"⚠️  No results found on page {page_number}.")
                    if page_number == 1:
                        return {"found_raw_offers": []}
                    break

                cards = self.page.locator(self.CARD_SELECTOR)
                count = await cards.count()
                result_url = self.page.url

                for i in range(count):
                    if len(found_job_entities) >= worker_job_limit:
                        break

                    print(f"  -> Processing card {i+1}/{count} (Page {page_number})")

                    try:
                        card = self.page.locator(self.CARD_SELECTOR).nth(i)

                        await card.scroll_into_view_if_needed()
                        await human_delay(400, 1000)  # 🚨 NEW: pause to "look at" the card

                        raw_company, raw_title, raw_location = await self.get_raw_job_data(card)
                        print(f"---[TEASER WORKER] RAW DATA---\n[TEASER Company]: {raw_company},\n[TEASER Title]: {raw_title},\n[TEASER Location]: {raw_location}")
                        if not raw_title:
                            print("     ⚠️ Missing title, skipping card.")
                            continue

                        fast_hash = self._generate_fast_hash(raw_company, raw_title, str(user_id))
                        if fast_hash in ignored_hashes:
                            print(f"     ⏩ Skipping duplicate: {raw_title} at {raw_company}")
                            continue

                        # ✅ RETRY: click title link → wait for detail page apply button
                        click_success = False
                        for attempt in range(3):
                            try:
                                card = self.page.locator(self.CARD_SELECTOR).nth(i)
                                title_link = card.locator('h3 a').first
                                await human_click(title_link)  # 🚨 NEW
                                await self.page.wait_for_load_state("networkidle")
                                await self.page.wait_for_selector(
                                    'button[data-testid="jobad-DetailView__CandidateActions__Buttons_apply_internal_candidacy"]',
                                    state="attached",
                                    timeout=40000,
                                )
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

                        await human_delay(1500, 3500)  # 🚨 NEW: pause to "read" the job description

                        # Expand the description if "Voir plus" is present, then grab full text
                        try:
                            desc_article = self.page.locator('article[data-testid="jobad-DetailView__Description"]')
                            await desc_article.wait_for(state="attached", timeout=10000)
                            try:
                                voir_plus = desc_article.locator('button').first
                                if await voir_plus.count() > 0 and await voir_plus.is_visible():
                                    await human_click(voir_plus)
                                    await human_delay(400, 900)
                            except Exception:
                                pass
                            job_desc = await desc_article.inner_text()
                        except Exception:
                            job_desc = ""

                        # Click apply button → navigates to the application form URL (option i confirmed)
                        apply_btn = self.page.locator(
                            'button[data-testid="jobad-DetailView__CandidateActions__Buttons_apply_internal_candidacy"]'
                        ).first

                        if await apply_btn.count() > 0:
                            try:
                                await human_click(apply_btn)
                                await self.page.wait_for_load_state("networkidle", timeout=30000)
                                form_url = self.page.url
                                print(f"     ✅ Internal form confirmed: {raw_title}")

                                offer = JobOffer(
                                    url=current_url,
                                    form_url=form_url,
                                    search_id=search_id,
                                    user_id=state["user"].id,
                                    company_name=raw_company,
                                    job_title=raw_title,
                                    location=raw_location,
                                    job_board=JobBoard.JOBTEASER,
                                    status=ApplicationStatus.FOUND,
                                    job_desc=job_desc,
                                )
                                found_job_entities.append(offer)
                                print(f"     📦 Current batch size: {len(found_job_entities)}/{worker_job_limit}")
                            except Exception as e:
                                print(f"     ⚠️ Apply click failed for {raw_title}: {e}")
                        else:
                            print("     ⚠️    No internal apply button on detail page. Skipping.")

                        if not await self.nav_back(result_url):
                            break

                    except Exception as e:
                        print(f"     ⚠️ Error on card {i}: {e}")
                        try:
                            if not await self.nav_back(result_url):
                                break
                        except Exception:
                            pass
                        continue

                if len(found_job_entities) >= worker_job_limit:
                    break

                if not await self._handle_teaser_pagination(page_number):
                    break
                page_number += 1

        except Exception as e:
            print(f"Fatal Scraping Error: {e}")
            return {"error": "A critical error occurred while scanning JobTeaser."}

        if not found_job_entities:
            print("⚠️ Scanned jobs, but none were valid internal applications.")
            return {"found_raw_offers": []}

        print(f"🎉 TEASER Scraping Complete! Handing {len(found_job_entities)} jobs back to Master.")
        return {"found_raw_offers": found_job_entities}



    # --- NODE 7: Submit ---
    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state, "Submitting Applications")
        print("--- [JOBTEASER] Submitting Applications ---")

        jobs_to_process = state.get("processed_offers", [])
        user = state["user"]
        #preferences = state["preferences"]

        assigned_submit_limit = state.get("worker_job_limit", 5)

        teaser_jobs = [
            job for job in jobs_to_process
            if job.job_board == JobBoard.JOBTEASER and job.status == ApplicationStatus.APPROVED
        ]

        if not teaser_jobs:
            print("No approved JobTeaser jobs in submission queue.")
            return {"status": "no_teaser_jobs_to_submit"}

        successful_submissions = []
        i = 0
        for offer in teaser_jobs:
            if len(successful_submissions) >= assigned_submit_limit:
                print(f"🛑 [JOBTEASER] Reached assigned submission limit ({assigned_submit_limit}). Halting.")
                break

            print(f"📝 [JOBTEASER] Applying to: {offer.job_title} ({i+1}/{len(teaser_jobs)})")
            try:
                # ✅ RETRY: navigate to offer page, click apply, wait for form
                form_opened = False
                for attempt in range(3):
                    try:
                        # Navigate to the OFFER page (not form_url) — humans click through
                        await self.page.goto(offer.url, wait_until="commit", timeout=60000)
                        await self._handle_cookies() # ✅ ADDED
                        await human_delay(1500, 3500)  # 🚨 pause after navigation

                        # Wait for the apply button on the detail page
                        await self.page.wait_for_selector(
                            'button[data-testid="jobad-DetailView__CandidateActions__Buttons_apply_internal_candidacy"]',
                            state="attached",
                            timeout=60000,
                        )
                        apply_btn = self.page.locator(
                            'button[data-testid="jobad-DetailView__CandidateActions__Buttons_apply_internal_candidacy"]'
                        ).first

                        if await apply_btn.count() == 0:
                            raise Exception("Apply button not found on offer page")

                        await human_click(apply_btn)  # 🚨

                        # Wait for the application form to render
                        await self.page.wait_for_selector(
                            'form#application-flow-form',
                            state="visible",
                            timeout=15000,
                        )
                        form_opened = True
                        break
                    except Exception as e:
                        if attempt == 2:
                            print(f"⚠ Form failed to load after 3 attempts for {offer.url}. Skipping. Error: {e}")
                            break
                        print(f"⚠ Form load attempt {attempt+1} failed. Retrying in {2 ** attempt}s...")
                        await asyncio.sleep(2 ** attempt)

                if not form_opened:
                    i += 1
                    continue

                # Profile is pre-filled by JobTeaser from the user's account.
                # We only need to upload resume + (conditionally) fill cover letter.

                # Resume upload — JobTeaser file input is hidden, set_input_files works on attached input
                if user.resume_path:
                    print("⬇️ Downloading resume from cloud to RAM...")
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"

                    resume_input = self.page.locator('input#resume_0[type="file"]')
                    await resume_input.wait_for(state="attached", timeout=10000)
                    await resume_input.set_input_files({
                        "name": human_name,
                        "mimeType": "application/pdf",
                        "buffer": resume_bytes,
                    })
                    await human_delay(1500, 2500)  # 🚨 wait for upload to register

                # Cover letter — DETECT which variant of the form we got
                # Variant A: <textarea name="coverLetterContent"> exists → required, fill it
                # Variant B: <div class="CoverLetter_cvOnlyDescription..."> → no cover letter requested
                cover_textarea = self.page.locator('textarea[name="coverLetterContent"]')
                if await cover_textarea.count() > 0:
                    if offer.cover_letter:
                        await human_delay(400, 900)
                        # fill() is appropriate for long text — humans paste cover letters
                        await cover_textarea.fill(offer.cover_letter)
                        print("   📝  Cover letter filled.")
                    else:
                        # Form requires a cover letter but we have none — skip this offer
                        print(f"⚠️ [JOBTEASER] Form requires cover letter but none generated. Skipping {offer.job_title}.")
                        i += 1
                        continue
                else:
                    print(f"   ℹ️ No cover letter requested for {offer.job_title}.")

                # ✅ NO RETRY on submit click — duplicate submission risk
                await human_delay(1500, 3500)  # 🚨 review before submitting

                # The submit button stays disabled until the form is fully valid
                # (resume uploaded, cover letter filled if required).
                # Wait for it to BECOME enabled before clicking.
                submit_btn = self.page.locator(
                    'button[data-testid="jobad-DetailView__ApplicationFlow__Buttons__apply_button"]'
                )
                await submit_btn.wait_for(state="attached", timeout=10000)

                try:
                    # Wait until the disabled attribute is removed
                    # (Playwright's :enabled pseudo-class handles this cleanly)
                    await self.page.wait_for_selector(
                        'button[data-testid="jobad-DetailView__ApplicationFlow__Buttons__apply_button"]:not([disabled])',
                        state="visible",
                        timeout=15000,
                    )
                except Exception:
                    print(f"❌ [JOBTEASER] Submit button stayed disabled for {offer.job_title}. Form may be incomplete.")
                    continue

                await submit_btn.click()

                try:
                    # Success indicator can take several seconds to appear server-side
                    await self.page.wait_for_selector(
                        'aside[data-testid="jobad-DetailView__Heading__already_applied"]',
                        state="visible",
                        timeout=45000,
                    )
                    print(f"✅ [JOBTEASER] Application submitted for {offer.job_title}")
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                except Exception:
                    print(f"⚠️ Submission of {offer.url} failed — confirmation not received.")
                    continue
                else:
                    print(f"❌ [JOBTEASER] Submit button not visible for {offer.job_title}.")

            except Exception as e:
                print(f"❌ [JOBTEASER] Submission failed for {offer.url}: {e}")

            i += 1

        if not successful_submissions:
            return {"error": "All JobTeaser application attempts failed. Forms may have changed."}

        print(f"✅ [JOBTEASER] Successfully submitted {len(successful_submissions)} applications. Handing back to Master...")
        return {"submitted_offers": successful_submissions}


    # --- NODE 8: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up")
        print("--- [JOBTEASER] Cleanup ---")
        await self.force_cleanup()
        return {}


    # =========================================================================
    # GRAPH
    # =========================================================================

    def get_graph(self):
        workflow = StateGraph(JobApplicationState)
        
        workflow.add_node("start", self.start_session)
        workflow.add_node("nav", self.go_to_job_board)
        workflow.add_node("login", self.request_login)
        workflow.add_node("search", self.search_jobs)
        workflow.add_node("scrape", self.get_matched_jobs)
        
        workflow.add_node("start_with_session", self.start_session_with_auth) 
        workflow.add_node("submit", self.submit_applications)
        
        workflow.add_node("cleanup", self.cleanup)

        workflow.set_conditional_entry_point(
            self.route_action_intent,
            {
                "start": "start",
                "start_with_session": "start_with_session"
            }
        )
        
        workflow.add_conditional_edges("start", self.route_node_exit, {"error": "cleanup", "continue": "nav"})
        workflow.add_conditional_edges("nav", self.route_node_exit, {"error": "cleanup", "continue": "login"})
        workflow.add_conditional_edges("login", self.route_node_exit, {"error": "cleanup", "continue": "search"})
        workflow.add_conditional_edges("search", self.route_node_exit, {"error": "cleanup", "continue": "scrape"})
        workflow.add_conditional_edges("scrape", self.route_node_exit, {"error": "cleanup", "continue": "cleanup"}) 

        workflow.add_conditional_edges("start_with_session", self.route_node_exit, {"error": "cleanup", "continue": "submit"})
        workflow.add_conditional_edges("submit", self.route_node_exit, {"error": "cleanup", "continue": "cleanup"})
        
        workflow.add_edge("cleanup", END)
        
        return workflow.compile()