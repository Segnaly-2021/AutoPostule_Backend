# auto_apply_app/infrastructures/agent/workers/teaser/teaser_worker.py
import hashlib
import os
import json
import asyncio
import pdfplumber
from typing import Optional
from langgraph.graph import StateGraph, END
from playwright_stealth import Stealth
from playwright.async_api import Locator, async_playwright, Page, Browser, BrowserContext, Playwright
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage


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
                 api_keys: dict,
                 get_agent_state: GetAgentStateUseCase
                ):
        
        # Static Dependencies
        self.get_ignored_hashes = get_ignored_hashes       
        self.encryption_service = encryption_service
        self.base_url = "https://www.jobteaser.com/fr"
        self.file_storage = file_storage
        self.get_agent_state = get_agent_state
        self.api_keys = api_keys
        
        # Runtime State (Lazy Initialization)
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Progress callback (set per-run by master)
        self._progress_callback = None
        self._source_name = "TEASER"


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
        b = "wttj"
        raw_string = f"{c}_{t}_{b}_{u}"
        return hashlib.md5(raw_string.encode()).hexdigest()

    def _get_session_file_path(self, user_id: str) -> str:
        directory = os.path.join(os.getcwd(), "tmp", "sessions")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{user_id}_wttj_session.json")

    async def _save_auth_state(self, user_id: str):
        if self.context:
            path = self._get_session_file_path(user_id)
            await self.context.storage_state(path=path)
            print(f"🔒 [WTTJ] Session saved securely for user {user_id}")

    def _get_auth_state_path(self, user_id: str) -> str | None:
        path = self._get_session_file_path(user_id)
        if os.path.exists(path):
            return path
        return None

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
            print("info: No cookie popup detected (or already gone).")

    async def _handle_wttj_application_modal(self):
        try:
            modal = self.page.locator('[data-testid="modals"]')
            if not await modal.is_visible():
                return
            
            later_button = modal.get_by_text("Peut-être plus tard", exact=True)
            if await later_button.is_visible():
                await human_click(later_button)  # 🚨 NEW
                await modal.wait_for(state="hidden", timeout=5000)
                print("✅ [WTTJ] Dismissed application modal.")
            else:
                await self.page.evaluate("""
                    const portal = document.getElementById('portal/:rcm:');
                    if (portal) portal.remove();
                """)
                print("✅ [WTTJ] Removed application modal via DOM.")
        except Exception as e:
            print(f"⚠️ [WTTJ] Could not dismiss application modal: {e}")

    async def _handle_wttj_close_modal(self):
        try:
            modal = self.page.locator('[data-testid="apply-form-modal"]')
            if not await modal.is_visible():
                return
            
            close_button = modal.locator('[data-dialog-dismiss][title="Close"]')
            if await close_button.is_visible():
                await human_click(close_button)  # 🚨 NEW
                await modal.wait_for(state="hidden", timeout=5000)
                print("✅ [WTTJ] Closed apply form modal.")
            else:
                await self.page.evaluate("""
                    const modal = document.querySelector('[data-testid="apply-form-modal"]');
                    if (modal) modal.closest('[role="dialog"]').remove();
                """)
                print("✅ [WTTJ] Removed apply form modal via DOM.")
        except Exception as e:
            print(f"⚠️ [WTTJ] Could not close apply form modal: {e}")

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
                print("🔚 [TEASER] No pagination nav. Single page of results.")
                return False

            next_control = nav.locator('> *:last-child')
            if await next_control.count() == 0:
                print("🔚 [TEASER] No next control found.")
                return False

            tag_name = await next_control.evaluate("el => el.tagName.toLowerCase()")
            if tag_name == "button":
                print("🔚 [TEASER] Next button disabled. Reached last page.")
                return False

            if tag_name != "a":
                print(f"🔚 [TEASER] Unexpected last pagination element: <{tag_name}>")
                return False

            print(f"➡️ [TEASER] Moving to page {page_number + 1}...")
            await human_delay(1500, 3500)  # 🚨 NEW: pause before pagination

            for attempt in range(3):
                try:
                    await next_control.click()
                    await self.page.wait_for_load_state("networkidle")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=45000)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"⚠️ [TEASER] Pagination failed after 3 attempts: {e}")
                        return False
                    await asyncio.sleep(2 ** attempt)

            return True

        except Exception as e:
            print(f"⚠️ [TEASER] Pagination error: {e}")
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
                            print(f"  ⚠️ [TEASER] No mapping for contract '{contract.value}', skipping.")
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
                                print(f"  ✓ Contract checked: {contract.value}")
                        except Exception as e:
                            print(f"  ⚠️ Could not check contract '{contract.value}': {e}")

                    # Apply contract filters before moving to location (they can cause a full page reload)
                    await self.page.keyboard.press("Enter")
                    await self.page.wait_for_load_state("networkidle")
                except Exception as e:
                    print(f"  ⚠️ [TEASER] Contract filter step failed: {e}")

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
                    await first_suggestion.wait_for(state="visible", timeout=8000)
                    await human_click(first_suggestion)
                    await self.page.wait_for_load_state("networkidle")
                    print(f"  ✓ Location selected: {location}")
                except Exception as e:
                    print(f"  ⚠️ [TEASER] Location filter step failed: {e}")

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
                        print(f"⚠️ [TEASER] Could not open secondary filters modal: {e}")
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
                        print(f"⚠️ [TEASER] Filter modal submit failed after 3 attempts: {e}")
                        raise
                    await asyncio.sleep(2 ** attempt)

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
            print("🛤️ [WTTJ] Routing to SUBMIT track...")
            return "start_with_session"
        print("🛤️ [WTTJ] Routing to SCRAPE track...")
        return "start"


    # =========================================================================
    # NODES
    # =========================================================================

    # --- NODE 1: Start Session ---
    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser") 
        print(f"--- [TEASER] Starting session for {state['user'].firstname} ---")
        
        preferences = state["preferences"]

        # 🚨 NEW: Pull identity from state
        fingerprint = state.get("user_fingerprint")
        proxy_config = state.get("proxy_config")
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=preferences.browser_headless, 
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
        print("--- [TEASER] Booting Browser (Session Injection) ---")
        user_id = str(state["user"].id)

        # 🚨 NEW: Pull identity from state
        fingerprint = state.get("user_fingerprint")
        proxy_config = state.get("proxy_config")

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=state["preferences"].browser_headless,
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
                    print(f"⚠️ [TEASER] Auth boot attempt {attempt+1} failed. Retrying in {2 ** attempt}s...")
                    await asyncio.sleep(2 ** attempt)

            await human_warmup(self.page, self.base_url)  # 🚨 NEW: human warmup

            # Click the "Offres" nav link to reach the job search page
            # Uses partial class match (Nav_app-Nav__link has a CSS Modules hash suffix)
            # combined with inner text "Offres" for stability
            offres_link = self.page.locator(
                'a[class*="Nav_app-Nav__link"]:has_text="Offres"'
            ).first
            await offres_link.wait_for(state="visible", timeout=15000)
            await human_click(offres_link)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_selector(
                'input[id="job-ads-autocomplete-keyword-search"]',
                state="visible",
                timeout=30000,
            )
            search_entity = state["job_search"]
            job_title = search_entity.job_title
            contract_types = getattr(search_entity, 'contract_types', [])
            location = getattr(search_entity, 'location', "")

            print(f"--- [TEASER] Dummy Searching for: {job_title} ---")
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
                    print("✅ [TEASER] Dummy search complete — session warmed up.")
                except Exception:
                    # No results is fine — we're not actually scraping here, just looking human
                    print("⚠️ [TEASER] Dummy search ran but no cards found. Continuing.")

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
        print("--- [TEASER] Navigating ---")
        try:
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=90000)
                    #await self._handle_cookies()
                    await self.page.wait_for_selector('button[id="UnloggedUserDropdownButton"]', state="visible", timeout=30000)
                    break
                except Exception as e:
                    if attempt == 2:
                        return {"error": "Could not reach JobTeaser. The job board might be down or undergoing maintenance."}
                    print(f"⚠️ [TEASER] Navigation attempt {attempt+1} failed. Error: {e}. Retrying in {2 ** attempt}s...")
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
        
        print("--- [TEASER] Requesting Login ---")

        if prefs.is_full_automation and creds["jobteaser"]:
            print("🔐 [TEASER] Full Automation: Attempting auto-login...")

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
                        print(f"⚠️ [TEASER] Login flow attempt {attempt+1} failed. Error: {e}. Reloading...")
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
                        break
                    except Exception as e:
                        if attempt == 2:
                            return {"error": "Login failed. Could not submit credentials."}
                        print(f"⚠️ [TEASER] Credential submission attempt {attempt+1} failed. Error: {e}. Retrying in {2 ** attempt}s...")
                        await asyncio.sleep(2 ** attempt)

              # ✅ RETRY UNIT 3: Proof of login
                for attempt in range(3):
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=60000)
                        # Use class prefix matcher — CSS Modules hashes change on rebuild
                        await self.page.wait_for_selector('span[class*="Greeting_firstWord"]', state="attached", timeout=30000)
                        break
                    except Exception as e:
                        if attempt == 2:
                            return {"error": "Login failed. Please check your JobTeaser credentials in your settings."}
                        print(f"⚠️ [TEASER] Post-login verification attempt {attempt+1} failed. Error: {e}. Retrying in {2 ** attempt}s...")
                        await asyncio.sleep(2 ** attempt)

                print("✅ [TEASER] Auto-login successful")
                await self._save_auth_state(user_id)
                return {}

            except Exception as e:
                print(f"❌ [TEASER] Auto-login failed: {e}")
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

        print(f"--- [TEASER] Searching for: {job_title} ---")

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
                    print(f"⚠️ [TEASER] Search field attempt {attempt+1} failed. Error: {e}. Reloading...")
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

            return {}

        except Exception as e:
            print(f"Search Error: {e}")
            return {"error": f"Failed to execute search for '{job_title}' on JobTeaser."}

    # --- NODE 5: Scrape Jobs ---
    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data")
        print("--- [TEASER] Scraping Jobs ---")

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
                print(f"📄 [TEASER] Processing Page {page_number}...")

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
                            print(f"     ⚠️ No internal apply button on detail page. Skipping.")

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
        print("--- [WTTJ] Submitting Applications ---")
        
        jobs_to_process = state.get("processed_offers", [])
        user = state["user"] 
        preferences = state["preferences"]

        assigned_submit_limit = state.get("worker_job_limit", 5) 

        wttj_jobs = [job for job in jobs_to_process if job.job_board == JobBoard.WTTJ and job.status == ApplicationStatus.APPROVED]

        if not wttj_jobs:
            print("No approved WTTJ jobs in submission queue.")
            return {"status": "no_wttj_jobs_to_submit"}

        successful_submissions = []
        i = 0
        for offer in wttj_jobs:
            if len(successful_submissions) >= assigned_submit_limit:
                print(f"🛑 [WTTJ] Reached assigned submission limit ({assigned_submit_limit}). Halting further submissions.")
                break

            print(f"📝 [WTTJ] Applying to: {offer.job_title} ({i+1}/{len(wttj_jobs)})")
            try:
                # ✅ RETRY: form entry as one critical unit
                form_opened = False
                for attempt in range(3):
                    try:
                        await self.page.goto(offer.url, wait_until="commit", timeout=60000)
                        await self._handle_cookies()
                        await human_delay(1500, 3500)  # 🚨 NEW: pause after navigation
                        
                        await self.page.wait_for_selector('[data-testid="job_bottom-button-apply"]', state="attached", timeout=30000)
                        apply_btn = self.page.locator('[data-testid="job_bottom-button-apply"]').first
                        
                        if await apply_btn.count() == 0:
                            raise Exception("Apply button not found")
                        
                        await human_click(apply_btn)  # 🚨 NEW
                        await self.page.wait_for_selector('[data-testid="apply-form-field-firstname"]', state="visible", timeout=15000)
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

                await self._handle_cookies()
                
                # Fill Form with HUMAN typing for short fields
                await human_type(self.page.get_by_test_id("apply-form-field-firstname"), user.firstname)  # 🚨 NEW
                await human_delay(200, 500)  # 🚨 NEW
                await human_type(self.page.get_by_test_id("apply-form-field-lastname"), user.lastname)  # 🚨 NEW
                
                if user.phone_number: 
                    await human_delay(200, 500)  # 🚨 NEW
                    await human_type(self.page.get_by_test_id("apply-form-field-phone"), user.phone_number)  # 🚨 NEW
                
                current_pos = getattr(user, 'current_position', "")
                if current_pos:
                    await human_delay(200, 500)  # 🚨 NEW
                    await human_type(self.page.get_by_test_id("apply-form-field-subtitle"), current_pos)  # 🚨 NEW

                # File Upload
                resume_bytes = None
                if user.resume_path:
                    print("⬇️ Downloading resume from cloud to RAM...")
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"
                    await self.page.get_by_test_id("apply-form-field-resume").set_input_files({
                        "name": human_name,
                        "mimeType": "application/pdf",
                        "buffer": resume_bytes
                    })
                    await human_delay(1000, 2000)  # 🚨 NEW: wait for upload to register

                # Dynamic Questions
                if resume_bytes:
                    questions = await self._handle_dynamic_questions(user, preferences, resume_bytes)
                    if not questions:
                        print("No dynamic questions detected or failed to parse.")
                    else:
                        for testid, field in questions.items():
                            if field.get("skip"):
                                continue
                            try:
                                match field["type"]:
                                    case "text":
                                        await self.page.locator(f'[data-testid="{testid}-input"]').fill(field["value"])
                                    case "textarea":
                                        await self.page.locator(f'[data-testid="{testid}-input"]').fill(field["value"])
                                    case "radio":
                                        await self.page.locator(
                                            f'[data-testid^="{testid}-RADIO"][label="{field["value"]}"]'
                                        ).click()
                                    case "dropdown":
                                        await self.page.locator(f'[data-testid="{testid}-DROPDOWN"]').click()
                                        await self.page.wait_for_selector('[role="listbox"]', state="visible", timeout=5000)
                                        await self.page.locator('[role="listbox"] li').filter(has_text=field["value"]).click()
                                    case "checkbox":
                                        await self.page.locator(f'[data-testid="{testid}-input"]').check()
                                await human_delay(300, 700)  # 🚨 NEW: between dynamic questions
                            except Exception as e:
                                print(f"⚠️ [WTTJ] Could not fill question {testid}: {e}")
                                continue
                    
                # Cover Letter — fill is fine, humans paste cover letters
                if offer.cover_letter:
                    await self.page.get_by_test_id("apply-form-field-cover_letter").fill(offer.cover_letter)
                
                # Consent Checkbox
                checkbox = self.page.locator('input[id="consent"]')
                if await checkbox.count() > 0 and not await checkbox.is_checked():
                    await human_delay(300, 700)  # 🚨 NEW
                    await self.page.locator('label[for="consent"]').click()
                
                # ✅ NO RETRY on submit click — would cause duplicate submission risk
                await human_delay(1500, 3500)  # 🚨 NEW: review before submitting
                await self.page.wait_for_selector('[data-testid="apply-form-submit"]', state="attached")
                submit_btn = self.page.locator('[data-testid="apply-form-submit"]')
                
                if await submit_btn.is_visible():
                    await submit_btn.click()

                    try:
                        await self.page.wait_for_selector('svg[alt="Paperplane"]', state="visible", timeout=45000)
                        print(f"✅ [WTTJ] Application submitted for {offer.job_title}")                    
                        offer.status = ApplicationStatus.SUBMITTED
                        successful_submissions.append(offer)
                    except Exception:
                        print(f"⚠️ Submission of {offer.url} failed — confirmation not received.")
                        continue
                else:
                    print(f"❌ [WTTJ] Submit button not visible for {offer.job_title}.")

            except Exception as e:
                print(f"❌ [WTTJ] Submission failed for {offer.url}: {e}")
            
            i += 1 

        if not successful_submissions:
            return {"error": "All WTTJ application attempts failed. Forms may have changed."}

        print(f"✅ [WTTJ] Successfully submitted {len(successful_submissions)} applications. Handing back to Master...")
        return {"submitted_offers": successful_submissions}


    # --- NODE 8: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up")
        print("--- [WTTJ] Cleanup ---")
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