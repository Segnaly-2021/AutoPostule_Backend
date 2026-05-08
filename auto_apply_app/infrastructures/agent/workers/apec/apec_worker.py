# auto_apply_app/infrastructures/agent/workers/apec/apec_worker.py
import asyncio
import hashlib
import logging
import os
from typing import Optional
from langgraph.graph import StateGraph, END
from playwright_stealth import Stealth
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright, Locator
import pdfplumber


# --- DOMAIN IMPORTS ---
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import ContractType, JobBoard, ApplicationStatus

# --- INFRA & APP IMPORTS ---
from auto_apply_app.application.use_cases.agent_state_use_cases import IsAgentKilledForSearchUseCase
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.application.use_cases.agent_use_cases import (
    GetIgnoredHashesUseCase,
)
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort

# Human behavior helpers
from auto_apply_app.infrastructures.agent.human_behavior import (
    human_delay,
    human_type,
    human_click,
    human_warmup,
)

logger = logging.getLogger(__name__)


class ApecWorker():

    # CLASS CONSTANT: Single source of truth for the job card selector
    CARD_SELECTOR = 'div[class*="card card-offer mb-20 card--clickable"]'

    # --- ADVANCED SEARCH FALLBACK URL ---
    ADVANCED_SEARCH_URL = "https://www.apec.fr/candidat/recherche-emploi.html/emploi/recherche-avancee"

    def __init__(
        self,
        get_ignored_hashes: GetIgnoredHashesUseCase,
        encryption_service: EncryptionServicePort,
        file_storage: FileStoragePort,
        is_agent_killed_for_search: IsAgentKilledForSearchUseCase,
    ):
        # Static Dependencies
        self.get_ignored_hashes = get_ignored_hashes
        self.encryption_service = encryption_service
        self.base_url = "https://www.apec.fr/"
        self.file_storage = file_storage
        self.is_agent_killed_for_search = is_agent_killed_for_search

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
            logger.warning("[APEC] Circuit breaker tripped: %s", state["error"])
            return "error"

        user_id = state["user"].id
        search_id = state["job_search"].id

        killed_result = await self.is_agent_killed_for_search.execute(user_id, search_id)
        if killed_result.is_success and killed_result.value:
            logger.info("[APEC] Kill switch detected for search %s. Aborting gracefully.", search_id)
            return "error"

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
                "search_id": search_id,
            })
        except Exception:
            logger.exception("[APEC] Progress emit failed")

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
            logger.info("[APEC] Session saved for user %s", user_id)

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
                await human_delay(300, 800)
                await cookie_btn.click()
        except Exception:
            logger.debug("[APEC] No cookies popup")

    async def force_cleanup(self):
        logger.info("[APEC] Force cleanup initiated")
        try:
            if self.page:
                await self.page.close()
                self.page = None
        except Exception:
            logger.exception("[APEC] Page close error")

        try:
            if self.context:
                await self.context.close()
                self.context = None
        except Exception:
            logger.exception("[APEC] Context close error")

        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
        except Exception:
            logger.exception("[APEC] Browser close error")

        try:
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
        except Exception:
            logger.exception("[APEC] Playwright stop error")

        logger.info("[APEC] Force cleanup complete")

    def _extract_resume(self, resume_path: str) -> str:
        text = ""
        try:
            if not resume_path:
                return ""
            with pdfplumber.open(resume_path) as pdf:
                for p in pdf.pages:
                    text += p.extract_text() + "\n"
        except Exception:
            logger.exception("[APEC] Error reading resume")
        return text

    async def get_raw_job_data(self, card: Locator):
        raw_title = None
        raw_company = "No Name"
        raw_location = None

        try:
            await card.locator('p[class="card-offer__company"]').wait_for(state="attached", timeout=25000)
        except Exception:
            logger.warning("[APEC] Card content not ready")
            return "No Name", None, None

        try:
            raw_title = await card.locator('h2[class="card-title"]').inner_text()
        except Exception:
            logger.warning("[APEC] Could not extract title")

        try:
            raw_company = await card.locator('p[class="card-offer__company"]').first.inner_text()
        except Exception:
            raw_company = "No Name"
            logger.warning("[APEC] Could not extract company")

        try:
            raw_location = await card.locator('li:has(img[alt="localisation"])').inner_text()
        except Exception:
            logger.warning("[APEC] Could not extract location")

        return (
            raw_company.strip() if raw_company else None,
            raw_title.strip() if raw_title else None,
            raw_location.strip() if raw_location else None,
        )

    # --- HELPER: Nav Back ---
    async def nav_back(self, url: str):
        await self.page.goto(url, wait_until="networkidle")

        try:
            await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
            await human_delay(1000, 2500)
            await self._handle_cookies()
        except Exception:
            logger.warning("[APEC] Cards didn't reappear after going back. Reloading...")
            await self.page.reload(wait_until="networkidle")
            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
            except Exception:
                logger.warning("[APEC] Cards still not visible after reload. Page state may be broken.")
            await self._handle_cookies()

    # --- HELPER: Handle Pagination ---
    async def _handle_apec_pagination(self, page_number: int) -> bool:
        try:
            next_button = self.page.locator(
                'nav[aria-label="Page navigation"] li[class="page-item"]'
            )

            if await next_button.count() == 0:
                logger.info("[APEC] No next button found. Reached last page.")
                return False

            butt_child = next_button.locator("a")
            if await butt_child.count() == 0:
                logger.info("[APEC] Next button inactive. Reached last page.")
                return False

            logger.info("[APEC] Moving to page %s...", page_number + 1)
            await human_delay(1500, 3500)

            for attempt in range(3):
                try:
                    await next_button.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[APEC] Pagination failed after 3 attempts")
                        return False
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            return True

        except Exception:
            logger.exception("[APEC] Pagination error")
            return False

    # --- HELPER: Apply APEC Advanced Filters ---
    async def _apply_filters(self, job_title: str, contract_types: list[ContractType], min_salary: int):
        logger.info("[APEC] Applying advanced filters")
        try:
            for attempt in range(3):
                try:
                    await self.page.wait_for_selector('a[id="advancedSearch"]', state="visible", timeout=45000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.warning("[APEC] advancedSearch not found after 3 attempts. Direct nav...")
                        await self.page.goto(self.ADVANCED_SEARCH_URL, wait_until="networkidle", timeout=60000)
                    else:
                        await asyncio.sleep(2 ** attempt)

            await human_click(self.page.locator('a[id="advancedSearch"]'))
            await self.page.wait_for_load_state("networkidle", timeout=90000)

            await self.page.wait_for_selector('input[id="keywords"]', state="visible", timeout=15000)
            await human_type(self.page.locator('input[id="keywords"]'), job_title)

            contract_map = {
                "CDI": "101888",
                "CDD": "101887",
                "Alternance": "20053",
                "Intérim": "101930",
                "Stage": "597171",
            }

            for attempt in range(3):
                try:
                    await self.page.wait_for_selector('apec-slider input.pull-left', state="attached", timeout=45000)
                    logger.info("[APEC] Full Angular form rendered")
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[APEC] Angular form never fully rendered")
                        raise
                    await self.page.reload(wait_until="networkidle")
                    await asyncio.sleep(2 ** attempt)

            if contract_types:
                for contract in contract_types:
                    val = contract_map.get(str(contract.value), None)
                    if val:
                        await self.page.locator('select[formcontrolname="typesContrat"]').scroll_into_view_if_needed()
                        await human_delay(400, 900)
                        await self.page.select_option('select[formcontrolname="typesContrat"]', value=val)
                        break

            if min_salary > 0:
                await self.page.locator('apec-slider input.pull-left').scroll_into_view_if_needed()
                salary_input = self.page.locator('apec-slider input.pull-left')
                if await salary_input.count() > 0:
                    salary_k = str(min_salary // 1000) if min_salary >= 1000 else str(min_salary)
                    await human_delay(300, 700)
                    await human_type(salary_input, salary_k)

            await human_delay(800, 1800)
            for attempt in range(3):
                try:
                    await self.page.locator('button:has-text("RECHERCHER")').click()
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)

            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)

        except Exception:
            logger.exception("[APEC] Error applying filters")
            raise

    async def _get_job_attribute(self, card: Locator, selector: str, default_value: str = None):
        try:
            content = await card.locator(selector).inner_text()
            return content.strip()
        except Exception:
            return default_value

    # =========================================================================
    # NODES
    # =========================================================================

    # --- NODE 1: Start Session (SCRAPE track) ---
    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser")
        logger.info("[APEC] Starting session")
        preferences = state["preferences"]

        fingerprint = state.get("user_fingerprint")
        proxy_config = state.get("proxy_config")

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
            logger.exception("[APEC] Session error")
            return {"error": "Failed to start the secure browsing session. Our servers might be under heavy load, please try again."}

    # --- NODE 1 Bis: Boot & Inject Session (SUBMIT track) ---
    async def start_session_with_auth(self, state: JobApplicationState):
        await self._emit(state, "Initializing Secure Browser")
        logger.info("[APEC] Booting browser (session injection)")
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
                logger.info("[APEC] Found saved session for user. Injecting cookies.")
                context_kwargs["storage_state"] = session_path
            else:
                logger.info("[APEC] No session found. Booting fresh context.")

            self.context = await self.browser.new_context(**context_kwargs)

            if fingerprint:
                await self.context.add_init_script(fingerprint.to_init_script())

            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)
            self.page = await self.context.new_page()

            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=90000)
                    await self.page.wait_for_selector('div[class="card-title"] h2:has-text("Je suis candidat")', state="visible", timeout=45000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[APEC] Auth boot failed after 3 attempts")
                        return {"error": "Failed to reach APEC after multiple attempts."}
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            await human_warmup(self.page, self.base_url)
            await human_click(self.page.locator('div[class="card-title"] h2:has-text("Je suis candidat")'))

            search_entity = state["job_search"]
            job_title = search_entity.job_title
            contract_types = getattr(search_entity, 'contract_types', [])
            min_salary = getattr(search_entity, 'min_salary', 0)

            try:
                await self._apply_filters(job_title, contract_types, min_salary)

                try:
                    await self.page.wait_for_selector(self.CARD_SELECTOR, timeout=25000)
                    logger.info("[APEC] Search results loaded")
                except Exception:
                    logger.info("[APEC] No results found after applying filters")

                return {}
            except Exception:
                logger.exception("[APEC] Search error during auth track")
                return {}

        except Exception:
            logger.exception("[APEC] Browser auth initialization error")
            return {"error": "Failed to initialize browser with session."}

    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board")
        logger.info("[APEC] Navigating to board")
        try:
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                    await self._handle_cookies()
                    await self.page.wait_for_selector('li[id="header-monespace"]', state="visible", timeout=30000)
                    break
                except Exception:
                    if attempt == 2:
                        return {"error": "Could not reach APEC.fr. The job board might be down or undergoing maintenance."}
                    await asyncio.sleep(2 ** attempt)

            await human_warmup(self.page, self.base_url)
            return {}
        except Exception:
            logger.exception("[APEC] Nav error")
            return {"error": "Could not reach APEC.fr. The job board might be down or undergoing maintenance."}

    # --- NODE 3: Login ---
    async def request_login(self, state: JobApplicationState):
        await self._emit(state, "Authenticating")

        prefs = state["preferences"]
        creds = state.get("credentials")
        user_id = state["user"].id

        logger.info("[APEC] Login phase")

        if prefs.is_full_automation and creds["apec"]:
            logger.info("[APEC] Full automation: attempting auto-login")

            login_plain = None
            pass_plain = None

            try:
                login_plain = await self.encryption_service.decrypt(creds["apec"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["apec"].password_encrypted)

                # RETRY UNIT 1: Open login modal
                for attempt in range(3):
                    try:
                        await self.page.wait_for_selector('li[id="header-monespace"]', state="visible", timeout=30000)
                        await human_click(self.page.locator('li[id="header-monespace"]'))
                        await self.page.wait_for_selector('input[id="emailid"]', state="visible", timeout=15000)
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Could not open the login modal."}
                        await self.page.reload(wait_until="networkidle")
                        await asyncio.sleep(2 ** attempt)

                # RETRY UNIT 2: Fill credentials with HUMAN typing
                for attempt in range(3):
                    try:
                        await self.page.locator('input[id="emailid"]').clear()
                        await human_delay(300, 700)
                        await human_type(self.page.locator('input[id="emailid"]'), login_plain)

                        await human_delay(400, 900)

                        await self.page.locator('input[id="password"]').clear()
                        await human_delay(200, 500)
                        await human_type(self.page.locator('input[id="password"]'), pass_plain)

                        await human_delay(600, 1500)

                        await self.page.wait_for_selector('button[type="submit"][value="Login"]', state="visible", timeout=10000)
                        await self.page.locator('button[type="submit"][value="Login"]').first.click()
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Could not submit credentials."}
                        await asyncio.sleep(2 ** attempt)

                # RETRY UNIT 3: Proof of login
                for attempt in range(3):
                    try:
                        await self.page.wait_for_url("**/candidat**", timeout=30000)
                        await self.page.goto(f"{self.base_url}candidat.html", wait_until="networkidle", timeout=90000)
                        break
                    except Exception:
                        if attempt == 2:
                            return {"error": "Login failed. Please check your APEC credentials in your settings."}
                        await asyncio.sleep(2 ** attempt)

                logger.info("[APEC] Auto-login successful")
                await self._save_auth_state(str(user_id))
                return {}

            except Exception:
                logger.exception("[APEC] Auto-login failed")
                return {"error": "Login failed. Please check your APEC credentials in your settings."}

            finally:
                if login_plain is not None:
                    del login_plain
                if pass_plain is not None:
                    del pass_plain

        else:
            logger.info("[APEC] Semi-automation: requesting user action")
            try:
                await self.page.locator('a[aria-label="Mon espace"]').click()
                logger.info("[APEC] ACTION REQUIRED: Please log in manually within 60 seconds")
                await asyncio.sleep(90)
                await self.page.locator('[aria-label="menu"]').click()
                await self.page.locator('[href="/candidat.html"]').click()
                await self._save_auth_state(user_id)
                return {}
            except Exception:
                logger.exception("[APEC] Manual login error")
                return {"error": "Login timed out. We didn't detect a successful login within the allowed time."}

    # --- NODE 4: Search ---
    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state, "Searching for Jobs")
        search_entity = state["job_search"]
        job_title = search_entity.job_title
        contract_types = getattr(search_entity, 'contract_types', [])
        min_salary = getattr(search_entity, 'min_salary', 0)

        logger.info("[APEC] Starting search process")
        try:
            await self._handle_cookies()
            await human_warmup(self.page, self.base_url)
            await self._apply_filters(job_title, contract_types, min_salary)

            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, timeout=5000)
                logger.info("[APEC] Search results loaded")
            except Exception:
                logger.info("[APEC] No results found after applying filters")
                return {
                    "error": "No new matching jobs were found for this search today. We'll try again tomorrow!"
                }

        except Exception:
            logger.exception("[APEC] Search error")
            return {
                "error": "We encountered an issue applying your search filters. The job board may have updated its layout."
            }

        return {}

    # --- NODE 5: Scrape Jobs ---
    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data")
        logger.info("[APEC] Scraping jobs")

        user_id = state["user"].id
        search_id = state["job_search"].id
        found_job_entities = []

        worker_job_limit = 1 or state.get("worker_job_limit", 5)

        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=30)
        if not hash_result.is_success:
            logger.warning("[APEC] Could not fetch ignored hashes: %s", hash_result.error.message)
            ignored_hashes = set()
        else:
            ignored_hashes = hash_result.value

        logger.info("[APEC] Loaded %s ignored hashes. Target: %s new jobs.", len(ignored_hashes), worker_job_limit)

        page_number = 1
        max_pages = 20

        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:
                logger.info("[APEC] Processing page %s", page_number)

                cards = self.page.locator(self.CARD_SELECTOR)
                try:
                    await cards.first.wait_for(state="visible", timeout=15000)
                except Exception:
                    logger.info("[APEC] No cards found on page %s", page_number)
                    if page_number == 1:
                        return {"found_raw_offers": []}
                    break

                count = await cards.count()
                result_url = self.page.url

                for i in range(count):
                    if len(found_job_entities) >= worker_job_limit:
                        break

                    cards = self.page.locator(self.CARD_SELECTOR)
                    card = cards.nth(i)
                    await card.scroll_into_view_if_needed()
                    await human_delay(400, 1000)

                    raw_company, raw_title, raw_location = await self.get_raw_job_data(card)

                    if not raw_title:
                        continue

                    fast_hash = self._generate_fast_hash(
                        raw_company if raw_company else "No Name",
                        raw_title,
                        str(user_id),
                    )

                    if fast_hash in ignored_hashes:
                        continue

                    # RETRY: card.click() + networkidle as one unit
                    click_success = False
                    for attempt in range(3):
                        try:
                            cards = self.page.locator(self.CARD_SELECTOR)
                            card = cards.nth(i)
                            await human_click(card)
                            await self.page.wait_for_load_state("networkidle")
                            click_success = True
                            break
                        except Exception:
                            if attempt == 2:
                                logger.warning("[APEC] Card click failed after 3 attempts. Skipping.")
                                break
                            await asyncio.sleep(2 ** attempt)

                    if not click_success:
                        continue

                    try:
                        await self.page.wait_for_selector('div[class="col-lg-8 border-L"]', state="visible", timeout=10000)
                    except Exception:
                        pass

                    await human_delay(1500, 3500)

                    try:
                        desc_element = self.page.locator('div[class="col-lg-8 border-L"]')
                        if await desc_element.count() > 0:
                            job_desc = await desc_element.inner_text()
                        else:
                            job_desc = await self.page.locator("body").inner_text()
                    except Exception:
                        job_desc = ""

                    try:
                        await self.page.wait_for_selector('a[class="btn btn-primary ml-0"]', state="visible", timeout=5000)
                    except Exception:
                        await self.nav_back(result_url)
                        continue

                    apply_btn = self.page.locator('a[class="btn btn-primary ml-0"]')

                    if await apply_btn.count() > 0:
                        href = await apply_btn.get_attribute("href")

                        if href and "to=int" in href:
                            full_offer_url = f"https://www.apec.fr{href}"

                            await self.page.goto(full_offer_url, wait_until="networkidle")
                            await human_delay(800, 2000)

                            try:
                                await self.page.wait_for_selector('button[title="Postuler"]', state="visible", timeout=15000)
                            except Exception:
                                await self.nav_back(result_url)
                                continue

                            postule_btn = self.page.locator('button[title="Postuler"]')

                            if await postule_btn.count() > 0:
                                await human_click(postule_btn)
                                await self.page.wait_for_load_state("networkidle")
                                form_url = self.page.url
                                await self.page.wait_for_selector('#formUpload, .form-check.uploadFile.profil-selection', state="attached", timeout=60000)

                                offer = JobOffer(
                                    url=full_offer_url,
                                    form_url=form_url,
                                    search_id=search_id,
                                    user_id=state["user"].id,
                                    company_name=raw_company if raw_company else "No Name",
                                    job_title=raw_title,
                                    location=raw_location,
                                    job_board=JobBoard.APEC,
                                    status=ApplicationStatus.FOUND,
                                    job_desc=job_desc,
                                )

                                found_job_entities.append(offer)

                    await self.nav_back(result_url)

                if len(found_job_entities) >= worker_job_limit:
                    break

                if not await self._handle_apec_pagination(page_number):
                    break
                page_number += 1

        except Exception:
            logger.exception("[APEC] Scraping error")
            return {"error": "A critical error occurred while scanning the job listings. We have safely halted the process."}

        if not found_job_entities:
            return {"found_raw_offers": []}

        logger.info("[APEC] Scraping complete. Returning %s jobs.", len(found_job_entities))
        return {"found_raw_offers": found_job_entities}

    # --- NODE 7: Submit Applications ---
    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state, "Submitting Applications")
        logger.info("[APEC] Submitting applications")

        jobs_to_process = state.get("processed_offers", [])
        user = state["user"]
        assigned_submit_limit = state.get("worker_job_limit", 5)

        apec_jobs = [job for job in jobs_to_process if job.job_board == JobBoard.APEC and job.status == ApplicationStatus.APPROVED]

        if not apec_jobs:
            logger.info("[APEC] No approved APEC jobs in submission queue")
            return {"status": "no_apec_jobs_to_submit"}

        successful_submissions = []

        i = 0
        for offer in apec_jobs:
            if len(successful_submissions) >= assigned_submit_limit:
                logger.info("[APEC] Reached assigned submission limit (%s)", assigned_submit_limit)
                break

            try:
                # RETRY: form entry as one critical unit
                form_loaded = False
                for attempt in range(3):
                    try:
                        await self.page.goto(offer.form_url, wait_until='networkidle', timeout=90000)
                        await human_delay(1500, 3500)
                        await self.page.wait_for_selector('button[title="Postuler"]', state="visible", timeout=60000)
                        await human_click(self.page.locator('button[title="Postuler"]'))
                        await self.page.wait_for_load_state("networkidle")
                        await self.page.wait_for_selector('#formUpload, .form-check.uploadFile.profil-selection', state="attached", timeout=90000)
                        form_loaded = True
                        break
                    except Exception:
                        if attempt == 2:
                            logger.warning("[APEC] Form failed to load after 3 attempts. Skipping.")
                            break
                        await asyncio.sleep(2 ** attempt)

                if not form_loaded:
                    i += 1
                    continue

                if user.resume_path:
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"

                    await self.page.wait_for_selector('#formUpload input[type="file"]', state="attached", timeout=15000)
                    await self.page.locator('#formUpload input[type="file"]').first.set_input_files({
                        "name": human_name,
                        "mimeType": "application/pdf",
                        "buffer": resume_bytes,
                    })
                    await human_delay(1000, 2000)

                    # has_saved_resume = await self.page.locator('.form-check.uploadFile.profil-selection').count() > 0

                    # if has_saved_resume:
                    #     await human_click(self.page.locator('label.choice-highlight.import-cv'))
                    #     await self.page.wait_for_selector('#formUpload input[type="file"]', state="visible", timeout=10000)
                    #     await self.page.locator('#formUpload input[type="file"]').first.set_input_files({
                    #         "name": human_name,
                    #         "mimeType": "application/pdf",
                    #         "buffer": resume_bytes
                    #     })
                    # else:
                    #     await self.page.locator('#formUpload input[type="file"]').first.set_input_files({
                    #         "name": human_name,
                    #         "mimeType": "application/pdf",
                    #         "buffer": resume_bytes
                    #     })
                    # await human_delay(1000, 2000)

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
                        await self.page.wait_for_selector('label:has-text("Saisir directement ma lettre de motivation")', state="visible")
                        await human_click(self.page.locator('input[formcontrolname="choixLm"]').last)
                        await self.page.wait_for_selector('textarea[formcontrolname="lmTexteSaisie"]', state="visible", timeout=10000)

                        if offer.cover_letter:
                            textarea = self.page.locator('textarea[formcontrolname="lmTexteSaisie"]')
                            await textarea.fill(offer.cover_letter)
                            await textarea.dispatch_event('input')

                    else:
                        await self.page.wait_for_selector('a[aria-controls="collapseThree"]', state="visible")
                        anchor = self.page.locator('a[aria-controls="collapseThree"]').first
                        anchor_label = self.page.locator('div[id="headingThree"]').first

                        await human_click(anchor_label)
                        await self.page.locator('#collapseThree').wait_for(state="visible", timeout=10000)

                        val = await anchor.get_attribute('aria-expanded')
                        if val != 'true':
                            await anchor_label.click()
                            await self.page.locator('#collapseThree').wait_for(state="visible", timeout=10000)

                        if offer.cover_letter:
                            await self.page.locator('#comment').fill(offer.cover_letter)
                            await self.page.locator('#comment').dispatch_event('input')

                except Exception:
                    logger.warning("[APEC] Could not fill cover letter")

                # D. Additional Data
                try:
                    await self.page.wait_for_selector('a[aria-controls="#collapse_additionalData"]', state="visible")
                    anchor_sec = self.page.locator('a[aria-controls="#collapse_additionalData"]').first
                    anchor_sec_label = self.page.locator('div[id="heading_additionalData"]').first

                    if await anchor_sec.get_attribute('aria-expanded') != 'true':
                        await human_click(anchor_sec_label)
                        await self.page.wait_for_selector('ng-select[formcontrolname="idNiveauFormation"]', state="visible", timeout=30000)

                    if hasattr(user, 'study_level') and user.study_level:
                        await self.page.locator('ng-select[formcontrolname="idNiveauFormation"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.study_level}")').first.click()
                        await human_delay(300, 700)

                    if hasattr(user, 'major') and user.major:
                        await self.page.locator('ng-select[formcontrolname="idDiscipline"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.major}")').first.click()
                        await human_delay(300, 700)

                    if hasattr(user, 'school_type') and user.school_type:
                        await self.page.locator('ng-select[formcontrolname="idNatureFormation"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.school_type}")').first.click()
                        await human_delay(300, 700)

                    if hasattr(user, 'graduation_year') and user.graduation_year:
                        await self.page.locator('ng-select[formcontrolname="anneeObtention"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.graduation_year}")').first.click()

                except Exception:
                    logger.warning("[APEC] Could not fill additional data (optional)")

                # F. Submit — NO retry (duplicate submission risk)
                await human_delay(1500, 3500)
                submit_btn = self.page.locator('button[title="Envoyer ma candidature"]')
                if await submit_btn.is_visible():
                    await submit_btn.click()

                    try:
                        await self.page.wait_for_selector('div[class="notification-title"]', state="visible", timeout=45000)
                        logger.info("[APEC] Application submitted")
                        offer.status = ApplicationStatus.SUBMITTED
                        successful_submissions.append(offer)
                    except Exception:
                        logger.warning("[APEC] Submission failed — confirmation not received")
                        continue
                else:
                    logger.warning("[APEC] Submit button not visible")

            except Exception:
                logger.exception("[APEC] Submission failed for %s", offer.url)

            i += 1

        if not successful_submissions:
            return {"error": "All application attempts failed. The job board may have updated its application form structure."}

        logger.info("[APEC] Successfully submitted %s applications", len(successful_submissions))
        return {"submitted_offers": successful_submissions}

    # --- NODE 9: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up")
        logger.info("[APEC] Cleanup")
        await self.force_cleanup()
        return {}

    # =========================================================================
    # GRAPH
    # =========================================================================

    def route_action_intent(self, state: JobApplicationState):
        intent = state.get("action_intent", "SCRAPE")
        if intent == "SUBMIT":
            return "start_with_session"
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
                "start_with_session": "start_with_session",
            },
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