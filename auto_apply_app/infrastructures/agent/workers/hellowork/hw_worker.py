# auto_apply_app/infrastructures/agent/workers/hellowork/hw_worker.py
import asyncio
import hashlib
import logging
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
from auto_apply_app.application.use_cases.agent_state_use_cases import IsAgentKilledForSearchUseCase
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort
from auto_apply_app.application.use_cases.agent_use_cases import GetIgnoredHashesUseCase

# Human behavior helpers
from auto_apply_app.infrastructures.agent.human_behavior import (
    human_delay,
    human_type,
    human_click,
    human_warmup,
)

logger = logging.getLogger(__name__)


class HelloWorkWorker:

    CARD_SELECTOR = '[data-id-storage-target="item"]'

    def __init__(
        self,
        get_ignored_hashes: GetIgnoredHashesUseCase,
        encryption_service: EncryptionServicePort,
        file_storage: FileStoragePort,
        is_agent_killed_for_search: IsAgentKilledForSearchUseCase,
    ):
        self.get_ignored_hashes = get_ignored_hashes
        self.encryption_service = encryption_service
        self.base_url = "https://www.hellowork.com/fr-fr/"
        self.file_storage = file_storage
        self.is_agent_killed_for_search = is_agent_killed_for_search

        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

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
            logger.info("[HW] Session saved for user %s", user_id)

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
                "search_id": search_id,
            })
        except Exception:
            logger.exception("[HW] Progress emit failed")

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
        except Exception:
            logger.exception("[HW] Error reading resume")
        return text

    async def get_raw_job_data(self, card: Locator):
        raw_title = None
        raw_company = None
        raw_location = None

        try:
            await card.locator('a[data-cy="offerTitle"]').wait_for(state="attached", timeout=10000)
        except Exception:
            logger.warning("[HW] Card content not ready")
            return "No Name", None, None

        try:
            anchor = card.locator('a[data-cy="offerTitle"]')
            paragraphs = anchor.locator('p')
            raw_title = await paragraphs.nth(0).inner_text()
        except Exception:
            logger.warning("[HW] Could not extract title")

        try:
            anchor = card.locator('a[data-cy="offerTitle"]')
            paragraphs = anchor.locator('p')
            raw_company = await paragraphs.nth(1).inner_text()
        except Exception:
            raw_company = "No Name"
            logger.warning("[HW] Could not extract company")

        try:
            raw_location = await card.locator('div[data-cy="localisationCard"]').inner_text()
        except Exception:
            logger.warning("[HW] Could not extract location")

        return (
            raw_company.strip() if raw_company else None,
            raw_title.strip() if raw_title else None,
            raw_location.strip() if raw_location else None,
        )

    async def force_cleanup(self):
        logger.info("[HW] Force cleanup initiated")
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            logger.exception("[HW] Cleanup error")
        logger.info("[HW] Force cleanup complete")

    async def _get_job_attribute(self, selector: str, default_value: str = None):
        try:
            await self.page.wait_for_selector(selector, state='attached', timeout=5000)
            text = await self.page.locator(selector).first.inner_text()
            return text.strip()
        except Exception:
            return default_value

    async def _nav_back_to_search(self, search_url: str) -> bool:
        for attempt in range(3):
            try:
                await self.page.goto(search_url, wait_until="networkidle")
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                await human_delay(800, 2000)
                return True
            except Exception:
                if attempt == 2:
                    logger.warning("[HW] Could not return to search results after 3 attempts")
                    return False
                await asyncio.sleep(2 ** attempt)
        return False

    async def _apply_filters(self, contract_types: list[ContractType], min_salary: int):
        try:
            logger.info("[HW] Applying search filters")

            FILTER_LABEL_SELECTOR = 'div[class="tw-layout-inner-grid"] label[for="allFilters"][data-cy="serpFilters"]:has-text(" Filtres ")'

            for attempt in range(3):
                try:
                    await self.page.wait_for_selector(FILTER_LABEL_SELECTOR, state="visible", timeout=20000)
                    all_filters_label = self.page.locator(FILTER_LABEL_SELECTOR).first
                    await human_click(all_filters_label)
                    await self.page.wait_for_selector('input#toggle-salary', state="attached", timeout=10000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[HW] Could not open filter panel after 3 attempts")
                        raise
                    await asyncio.sleep(2 ** attempt)

            if contract_types:
                for contract in contract_types:
                    try:
                        checkbox_selector = f'input[id="c-{str(contract.value)}"]'
                        checkbox = self.page.locator(checkbox_selector)
                        if await checkbox.count() > 0 and not await checkbox.is_checked():
                            await human_delay(300, 700)
                            await self.page.locator(f'label[for="{await checkbox.get_attribute("id")}"]').click()
                    except Exception:
                        pass

            if min_salary > 0:
                toggle_salary = self.page.locator('input#toggle-salary')
                if await toggle_salary.count() > 0:
                    await human_delay(300, 700)
                    await self.page.locator('label[for="toggle-salary"]').click()
                    await self.page.wait_for_selector('input#msa:not([disabled])', timeout=3000)
                    await self.page.evaluate("""(val) => {
                        const el = document.querySelector('input#msa');
                        el.value = val;
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    }""", min_salary)

            await human_delay(800, 1800)

            submit_filters_btn = self.page.locator('[data-cy="offerNumberButton"]')
            if await submit_filters_btn.is_visible():
                for attempt in range(3):
                    try:
                        await submit_filters_btn.click()
                        await self.page.wait_for_load_state("networkidle")
                        await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                        break
                    except Exception:
                        if attempt == 2:
                            logger.exception("[HW] Filter submit failed after 3 attempts")
                            raise
                        await asyncio.sleep(2 ** attempt)

        except Exception:
            logger.exception("[HW] Error applying filters")
            raise

    async def _handle_hw_pagination(self, page_number: int) -> bool:
        try:
            next_button = self.page.locator(
                'button[name="p"]:has(svg > use[href$="#right"])'
            ).first

            if await next_button.count() == 0:
                logger.info("[HW] No next button found. Reached last page.")
                return False

            is_disabled = await next_button.get_attribute("aria-disabled")
            if is_disabled == "true":
                logger.info("[HW] Next button disabled. Reached last page.")
                return False

            logger.info("[HW] Moving to page %s", page_number + 1)
            await human_delay(1500, 3500)

            for attempt in range(3):
                try:
                    await next_button.click()
                    await self.page.wait_for_load_state("networkidle")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[HW] Pagination failed after 3 attempts")
                        return False
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            return True

        except Exception:
            logger.exception("[HW] Pagination error")
            return False

    async def _handle_cookies(self):
        is_visible = False
        try:
            await self.page.wait_for_selector('button[id="hw-cc-notice-continue-without-accepting-btn"]', state="attached", timeout=3000)
            cookie_btn = self.page.locator('button[id="hw-cc-notice-continue-without-accepting-btn"]')
            if await cookie_btn.count() > 0:
                is_visible = True
                await human_delay(300, 800)
                await cookie_btn.click()
        except Exception:
            if is_visible:
                await self.page.wait_for_selector('div[class="hw-cc-main"]', state='attached', timeout=2000)
                await self.page.evaluate("""() => {
                    const overlays = document.querySelectorAll('.hw-cc-main');
                    overlays.forEach(el => el.remove());
                }""")

    async def route_node_exit(self, state: JobApplicationState) -> str:
        if state.get("error"):
            logger.warning("[HW] Circuit breaker tripped: %s", state["error"])
            return "error"

        user_id = state["user"].id
        search_id = state["job_search"].id

        killed_result = await self.is_agent_killed_for_search.execute(user_id, search_id)
        if killed_result.is_success and killed_result.value:
            logger.info("[HW] Kill switch detected for search %s. Aborting gracefully.", search_id)
            return "error"

        return "continue"

    def route_action_intent(self, state: JobApplicationState):
        if state.get("action_intent", "SCRAPE") == "SUBMIT":
            return "start_with_session"
        return "start"

    # =========================================================================
    # NODES
    # =========================================================================

    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser")
        logger.info("[HW] Starting session")
        preferences = state["preferences"]

        fingerprint = state.get("user_fingerprint")

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=preferences.browser_headless,
                args=['--disable-blink-features=AutomationControlled'],
            )

            context_kwargs = {}
            if fingerprint:
                context_kwargs.update(fingerprint.to_playwright_context_args())
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

            self.context = await self.browser.new_context(**context_kwargs)

            if fingerprint:
                await self.context.add_init_script(fingerprint.to_init_script())

            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)
            self.page = await self.context.new_page()
            return {}
        except Exception:
            logger.exception("[HW] Session error")
            return {"error": "Failed to start the secure browsing session."}

    async def start_session_with_auth(self, state: JobApplicationState):
        await self._emit(state, "Initializing Secure Browser")
        logger.info("[HW] Booting browser (session injection)")
        user_id = str(state["user"].id)

        fingerprint = state.get("user_fingerprint")

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=state["preferences"].browser_headless,
                args=['--disable-blink-features=AutomationControlled'],
            )

            session_path = self._get_auth_state_path(user_id)

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
                context_kwargs["storage_state"] = session_path

            # if proxy_config:
            #     context_kwargs["proxy"] = {
            #         "server": proxy_config["server"],
            #         "username": proxy_config["username"],
            #         "password": proxy_config["password"],
            #     }

            self.context = await self.browser.new_context(**context_kwargs)

            if fingerprint:
                await self.context.add_init_script(fingerprint.to_init_script())

            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)
            self.page = await self.context.new_page()

            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=120000)
                    await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=60000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[HW] Auth boot failed after 3 attempts")
                        return {"error": "Failed to reach HelloWork after multiple attempts."}
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            await human_warmup(self.page, self.base_url)

            search_entity = state["job_search"]
            job_title = search_entity.job_title
            contract_types = getattr(search_entity, 'contract_types', [])
            min_salary = getattr(search_entity, 'min_salary', 0)
            location = getattr(search_entity, 'location', "")

            try:
                await human_type(self.page.locator('input[id="k"]'), job_title)
                if location and location.strip() != "":
                    await human_delay(300, 700)
                    await human_type(self.page.locator('input[id="l"]'), location)
                await human_delay(500, 1200)
                await self.page.keyboard.press("Enter")

                await self.page.wait_for_load_state("networkidle")
                await self._handle_cookies()

                if contract_types or min_salary > 0:
                    await self._apply_filters(contract_types, min_salary)

                return {}
            except Exception:
                logger.exception("[HW] Session initialization error")
                return {}

        except Exception:
            logger.exception("[HW] Browser auth init error")
            return {"error": "Failed to initialize HelloWork browser."}

    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board")
        logger.info("[HW] Navigating to HelloWork")
        try:
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                    await self._handle_cookies()
                    await self.page.wait_for_selector('[data-cy="headerAccountMenu"]', state="visible", timeout=30000)
                    break
                except Exception:
                    if attempt == 2:
                        return {"error": "Could not reach HelloWork. The job board might be down or undergoing maintenance."}
                    await asyncio.sleep(2 ** attempt)

            await human_warmup(self.page, self.base_url)
            return {}
        except Exception:
            logger.exception("[HW] Navigation error")
            return {"error": "Navigation failed."}

    async def request_login(self, state: JobApplicationState):
        await self._emit(state, "Authenticating")
        prefs = state["preferences"]
        creds = state.get("credentials")
        user_id = str(state["user"].id)

        logger.info("[HW] Login phase")

        if prefs.is_full_automation and creds["hellowork"]:
            login_plain = None
            pass_plain = None

            try:
                for attempt in range(3):
                    try:
                        await human_click(self.page.locator('[data-cy="headerAccountMenu"]'))
                        await human_click(self.page.locator('[data-cy="headerAccountLogIn"]'))
                        await self.page.wait_for_selector('input[name="email2"]', state="visible", timeout=30000)
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Could not open the login modal."}
                        await self.page.reload(wait_until="networkidle")
                        await asyncio.sleep(2 ** attempt)

                login_plain = await self.encryption_service.decrypt(creds["hellowork"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["hellowork"].password_encrypted)

                for attempt in range(3):
                    try:
                        await self.page.locator('input[name="email2"]').clear()
                        await human_delay(300, 700)
                        await human_type(self.page.locator('input[name="email2"]'), login_plain)

                        await human_delay(400, 900)

                        await self.page.locator('input[name="password2"]').clear()
                        await human_delay(200, 500)
                        await human_type(self.page.locator('input[name="password2"]'), pass_plain)

                        await human_delay(600, 1500)
                        await self.page.locator('button[type="button"][class="profile-button"]').click()
                        await self.page.wait_for_selector('a[data-cy="cpMenuDashboard"]', state="attached", timeout=90000)
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Could not submit credentials."}
                        await asyncio.sleep(2 ** attempt)

                for attempt in range(3):
                    try:
                        await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                        await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=20000)
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Please check your HelloWork credentials in your settings."}
                        await asyncio.sleep(2 ** attempt)

                await self._save_auth_state(user_id)
                logger.info("[HW] Auto-login successful")
                return {}

            except Exception:
                logger.exception("[HW] Auto-login failed")
                return {"error": "Failed to log into HelloWork. Check credentials."}

            finally:
                if login_plain is not None:
                    del login_plain
                if pass_plain is not None:
                    del pass_plain

        else:
            try:
                await self.page.locator('[data-cy="headerAccountMenu"]').click()
                await self.page.locator('[data-cy="headerAccountLogIn"]').click()
                logger.info("[HW] ACTION REQUIRED: Please log in manually within 90 seconds")
                await asyncio.sleep(90)
                await self.page.locator('a[href="/fr-fr"]').first.click()
                await self._save_auth_state(user_id)
                return {}
            except Exception:
                return {"error": "Manual login timed out."}

    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state, "Searching for Jobs")

        search_entity = state["job_search"]
        job_title = search_entity.job_title
        contract_types = getattr(search_entity, 'contract_types', [])
        min_salary = getattr(search_entity, 'min_salary', 0)
        location = getattr(search_entity, 'location', "")

        logger.info("[HW] Starting search")
        try:
            await human_warmup(self.page, self.base_url)

            for attempt in range(3):
                try:
                    await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=15000)
                    await self.page.locator('input[id="k"]').clear()
                    await human_delay(200, 500)
                    await human_type(self.page.locator('input[id="k"]'), job_title)
                    if location and location.strip() != "":
                        await human_delay(300, 700)
                        await self.page.locator('input[id="l"]').clear()
                        await human_type(self.page.locator('input[id="l"]'), location)
                    await human_delay(500, 1200)
                    await self.page.keyboard.press("Enter")
                    await self.page.wait_for_load_state("networkidle")
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[HW] Search failed after 3 attempts")
                        return {"error": "Failed to search HelloWork."}
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()

            if contract_types or min_salary > 0:
                await self._apply_filters(contract_types, min_salary)

            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                logger.info("[HW] Search results loaded")
            except Exception:
                return {"error": "No new matching jobs were found for this search today."}

            return {}
        except Exception:
            logger.exception("[HW] Search error")
            return {"error": "Failed to search HelloWork."}

    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data")
        logger.info("[HW] Scraping jobs")

        user_id = state["user"].id
        search_id = state["job_search"].id
        found_job_entities = []

        worker_job_limit = 1 or state.get("worker_job_limit", 5)
        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=14)
        ignored_hashes = hash_result.value if hash_result.is_success else set()

        logger.info("[HW] Target: %s jobs. Ignored hashes: %s", worker_job_limit, len(ignored_hashes))

        page_number = 1
        max_pages = 20

        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:
                logger.info("[HW] Processing page %s", page_number)

                cards_locator = self.page.locator(self.CARD_SELECTOR)
                try:
                    await cards_locator.first.wait_for(state='visible', timeout=30000)
                except Exception:
                    if page_number == 1:
                        logger.info("[HW] No results found on page 1")
                        return {"found_raw_offers": []}
                    break

                count = await cards_locator.count()
                search_url = self.page.url

                for i in range(count):
                    if len(found_job_entities) >= worker_job_limit:
                        break

                    try:
                        card = self.page.locator(self.CARD_SELECTOR).nth(i)

                        await card.scroll_into_view_if_needed()
                        await human_delay(400, 1000)

                        raw_company, raw_title, raw_location = await self.get_raw_job_data(card)

                        if not raw_title:
                            continue

                        click_success = False
                        for attempt in range(3):
                            try:
                                card = self.page.locator(self.CARD_SELECTOR).nth(i)
                                await human_click(card)
                                await self.page.wait_for_load_state("networkidle")
                                click_success = True
                                break
                            except Exception:
                                if attempt == 2:
                                    logger.warning("[HW] Card click failed after 3 attempts. Skipping.")
                                    break
                                await asyncio.sleep(2 ** attempt)

                        if not click_success:
                            continue

                        current_url = self.page.url

                        fast_hash = self._generate_fast_hash(raw_company, raw_title, str(user_id))
                        if fast_hash in ignored_hashes:
                            if not await self._nav_back_to_search(search_url):
                                break
                            continue

                        await human_delay(1500, 3500)

                        try:
                            desc_el = self.page.locator('div[id="content"]')
                            if await desc_el.count() == 0:
                                desc_el = self.page.locator('div[data-id-storage-local-storage-key-param="visited-offers"]')
                            job_desc = await desc_el.inner_text()
                        except Exception:
                            job_desc = ""

                        moving_to_form_btn = self.page.locator('a[data-cy="applyButtonHeader"]').first
                        if await moving_to_form_btn.count() > 0:
                            click_ok = False
                            for attempt in range(3):
                                try:
                                    await human_click(moving_to_form_btn)
                                    click_ok = True
                                    break
                                except Exception:
                                    if attempt == 2:
                                        logger.warning("[HW] Apply button click failed after 3 attempts")
                                        break
                                    await asyncio.sleep(2 ** attempt)

                            if click_ok:
                                try:
                                    await self.page.wait_for_selector(
                                        selector='button[data-cy="applyButton"]',
                                        timeout=3000,
                                        state='visible',
                                    )
                                    logger.info("[HW] External form detected, skipping")
                                except Exception:
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
                                        job_desc=job_desc,
                                    )
                                    found_job_entities.append(offer)

                        if not await self._nav_back_to_search(search_url):
                            break

                    except Exception:
                        logger.exception("[HW] Error processing card %s on page %s", i, page_number)
                        if not await self._nav_back_to_search(search_url):
                            break
                        continue

                if len(found_job_entities) >= worker_job_limit:
                    break

                if not await self._handle_hw_pagination(page_number):
                    break
                page_number += 1

        except Exception:
            logger.exception("[HW] Fatal scraping error")
            return {"error": "[HW] Fatal scraping error."}

        if not found_job_entities:
            return {"found_raw_offers": []}

        logger.info("[HW] Scraping complete. Returning %s jobs.", len(found_job_entities))
        return {"found_raw_offers": found_job_entities}

    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state, "Submitting Applications")
        logger.info("[HW] Submitting applications")
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
                logger.info("[HW] Reached assigned submission limit (%s)", assigned_submit_limit)
                break

            try:
                form_loaded = False
                for attempt in range(3):
                    try:
                        await self.page.goto(offer.form_url, wait_until="commit", timeout=60000)
                        await human_delay(1500, 3500)

                        moving_to_form_btn = self.page.locator('a[data-cy="applyButtonHeader"]')
                        if await moving_to_form_btn.count() > 0:
                            await human_click(moving_to_form_btn)

                        await self.page.wait_for_selector('input[name="Firstname"]', state="visible", timeout=15000)
                        form_loaded = True
                        break
                    except Exception:
                        if attempt == 2:
                            logger.warning("[HW] Form failed to load after 3 attempts. Skipping.")
                            break
                        await asyncio.sleep(2 ** attempt)

                if not form_loaded:
                    i += 1
                    continue

                await human_delay(400, 1000)
                await human_type(self.page.locator('input[name="LastName"]'), user.lastname)

                if user.resume_path:
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"
                    await self.page.locator('[data-cy="cv-uploader-input"]').set_input_files({
                        "name": human_name,
                        "mimeType": "application/pdf",
                        "buffer": resume_bytes,
                    })
                    await human_delay(1000, 2000)

                if offer.cover_letter:
                    await human_click(self.page.locator('[data-cy="motivationFieldButton"]'))
                    await self.page.wait_for_selector('textarea[name="MotivationLetter"]', state="visible", timeout=10000)
                    await self.page.locator('textarea[name="MotivationLetter"]').fill(offer.cover_letter)

                await human_delay(1500, 3500)
                submit_btn = self.page.locator('[data-cy="submitButton"]')
                if await submit_btn.is_visible():
                    await submit_btn.click()

                    try:
                        notification = self.page.locator('[data-intersect-name-value="notification"]')
                        await notification.wait_for(state="attached", timeout=45000)

                        if await notification.count() > 0:
                            use_tag = notification.locator('svg use[href*="badges"]')
                            if await use_tag.count() > 0:
                                href_value = await use_tag.get_attribute("href")
                                if href_value and "error" in href_value.lower():
                                    logger.warning("[HW] Submission blocked by error badge")
                                    i += 1
                                    continue

                    except Exception:
                        logger.info("[HW] No notification appeared — assuming success")
                        i += 1

                    logger.info("[HW] Application submitted")
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                else:
                    logger.warning("[HW] Submit button not visible")

            except Exception:
                logger.exception("[HW] Submission failed for %s", offer.url)
            i += 1

        if not successful_submissions:
            return {"error": "All HW submissions failed."}

        return {"submitted_offers": successful_submissions}

    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up")
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
            {"start": "start", "start_with_session": "start_with_session"},
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