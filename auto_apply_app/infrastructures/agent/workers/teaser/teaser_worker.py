# auto_apply_app/infrastructures/agent/workers/teaser/teaser_worker.py
import hashlib
import logging
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


class JobTeaserWorker:

    CARD_SELECTOR = 'ul[data-testid="job-ads-wrapper"] > li'

    TEASER_CONTRACT_NAME_MAP = {
        "CDI":         "cdi",
        "CDD":         "cdd",
        "Stage":       "internship",
        "Alternance":  "alternating",
        "freelance":   "freelance",
    }

    def __init__(
        self,
        get_ignored_hashes: GetIgnoredHashesUseCase,
        encryption_service: EncryptionServicePort,
        file_storage: FileStoragePort,
        is_agent_killed_for_search: IsAgentKilledForSearchUseCase,
    ):
        self.get_ignored_hashes = get_ignored_hashes
        self.encryption_service = encryption_service
        self.base_url = "https://www.jobteaser.com/fr"
        self.file_storage = file_storage
        self.is_agent_killed_for_search = is_agent_killed_for_search

        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        self._progress_callback = None
        self._source_name = "JOBTEASER"

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_llm(self, preferences: UserPreferences) -> BaseChatModel:
        return ChatGoogleGenerativeAI(
            api_key=self.api_keys.get("gemini"),
            model="gemini-3-pro-preview",
            temperature=preferences.llm_temperature,
        )

    async def _emit(
        self,
        state: JobApplicationState,
        stage: str,
        status: str = "in_progress",
        error: str = None,
        error_code: str = None,
    ):
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
                "error_code": error_code or ("SYSTEMERROR" if error else None),
                "search_id": search_id,
            })
        except Exception:
            logger.exception("[JOBTEASER] Progress emit failed")

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
            logger.info("[JOBTEASER] Session saved for user %s", user_id)

    def _get_auth_state_path(self, user_id: str) -> str | None:
        path = self._get_session_file_path(user_id)
        if os.path.exists(path):
            return path
        return None

    async def _handle_cookies(self):
        try:
            agree_button_selector = '#didomi-notice-agree-button'
            await self.page.wait_for_selector(agree_button_selector, state='visible', timeout=5000)
            await self.page.click(agree_button_selector)
            await self.page.wait_for_timeout(1000)
        except Exception:
            logger.debug("[JOBTEASER] No cookie popup detected")

    async def force_cleanup(self):
        logger.info("[JOBTEASER] Force cleanup initiated")
        try:
            if self.page:
                await self.page.close()
        except Exception:
            logger.exception("[JOBTEASER] Page close error")
        try:
            if self.context:
                await self.context.close()
        except Exception:
            logger.exception("[JOBTEASER] Context close error")
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            logger.exception("[JOBTEASER] Browser close error")
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            logger.exception("[JOBTEASER] Playwright stop error")
        logger.info("[JOBTEASER] Force cleanup complete")

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
        except Exception:
            logger.exception("[JOBTEASER] Error reading resume")
        return text

    async def get_raw_job_data(self, card: Locator) -> tuple[str, str, str]:
        raw_company, raw_title, raw_location = "", "", ""

        try:
            company_el = card.locator('[data-testid="jobad-card-company-name"]').first
            raw_company = (await company_el.text_content() or "").strip()
        except Exception:
            logger.warning("[JOBTEASER] Company scrape failed")

        try:
            title_link = card.locator('h3 a').first
            raw_title = (await title_link.text_content() or "").strip()
        except Exception:
            logger.warning("[JOBTEASER] Title scrape failed")

        try:
            location_el = card.locator('[data-testid="jobad-card-location"] span').first
            raw_location = (await location_el.text_content() or "").strip()
        except Exception:
            logger.warning("[JOBTEASER] Location scrape failed")

        return raw_company if raw_company else "No name", raw_title, raw_location

    async def _handle_teaser_pagination(self, page_number: int) -> bool:
        try:
            nav = self.page.locator('nav[data-testid="job-ads-pagination"]')
            if await nav.count() == 0:
                logger.info("[JOBTEASER] No pagination nav. Single page of results.")
                return False

            next_control = nav.locator('> *:last-child')
            if await next_control.count() == 0:
                logger.info("[JOBTEASER] No next control found.")
                return False

            tag_name = await next_control.evaluate("el => el.tagName.toLowerCase()")
            if tag_name == "button":
                logger.info("[JOBTEASER] Next button disabled. Reached last page.")
                return False

            if tag_name != "a":
                logger.warning("[JOBTEASER] Unexpected last pagination element: <%s>", tag_name)
                return False

            logger.info("[JOBTEASER] Moving to page %s", page_number + 1)
            await human_delay(1500, 3500)

            for attempt in range(3):
                try:
                    await next_control.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=45000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[JOBTEASER] Pagination failed after 3 attempts")
                        return False
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            return True

        except Exception:
            logger.exception("[JOBTEASER] Pagination error")
            return False

    async def _apply_filters(self, contract_types: list[ContractType], location: str = ""):
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
                            logger.warning("[JOBTEASER] No mapping for contract '%s', skipping.", contract.value)
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
                                await self.page.wait_for_load_state("domcontentloaded")
                        except Exception:
                            logger.warning("[JOBTEASER] Could not check contract '%s'", contract.value)

                    await self.page.keyboard.press("Enter")
                    await self.page.wait_for_load_state("domcontentloaded")
                except Exception:
                    logger.exception("[JOBTEASER] Contract filter step failed")

            # ---------- 2. LOCATION ----------
            if location and location.strip():
                try:
                    await self.page.wait_for_selector('input#location-filter', state="visible", timeout=10000)
                    location_input = self.page.locator('input#location-filter')
                    await human_click(location_input)
                    await human_delay(200, 500)
                    await human_type(location_input, location.strip())
                    await human_delay(800, 1500)

                    first_suggestion = self.page.locator(
                        'div[class*="LocationFilter_main"] div[class*="Dropdown_main"] button'
                    ).first
                    await first_suggestion.wait_for(state="visible", timeout=30000)
                    await human_click(first_suggestion)
                    await self.page.wait_for_load_state("domcontentloaded")
                except Exception:
                    logger.exception("[JOBTEASER] Location filter step failed")

            # ---------- 3. SECONDARY FILTERS MODAL ----------
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
                except Exception:
                    if attempt == 2:
                        logger.exception("[JOBTEASER] Could not open secondary filters modal")
                        raise
                    await asyncio.sleep(2 ** attempt)

            try:
                simplifiee_id = "job-ads-candidacy-type-filter-INTERNAL"
                checkbox = self.page.locator(f'input#{simplifiee_id}')
                if await checkbox.count() > 0 and await checkbox.get_attribute("aria-checked") == "false":
                    await human_delay(300, 700)
                    await self.page.locator(f'label[for="{simplifiee_id}"]').click()
            except Exception:
                logger.warning("[JOBTEASER] Could not select 'Candidature simplifiée'")

            await human_delay(800, 1800)

            for attempt in range(3):
                try:
                    apply_btn = self.page.locator(
                        'button[data-testid="job-ads-secondary-filters-apply-button"]'
                    )
                    await apply_btn.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="attached", timeout=15000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[JOBTEASER] Filter modal submit failed after 3 attempts")
                        raise
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()

        except Exception:
            logger.exception("[JOBTEASER] Error applying filters")
            raise

    async def nav_back(self, url: str) -> bool:
        try:
            if await self.page.locator('a[title="Retourner aux résultats"]').count() > 0:
                await self.page.locator('a[title="Retourner aux résultats"]').click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
                await self._handle_cookies()
                await human_delay(800, 2000)
                return True
        except Exception:
            pass

        for attempt in range(3):
            try:
                await self.page.goto(url, wait_until="networkidle")
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
                await self._handle_cookies()
                await human_delay(800, 2000)
                return True
            except Exception:
                if attempt == 2:
                    logger.warning("[JOBTEASER] Could not return to search results after 3 attempts")
                    try:
                        await self.page.reload(wait_until="networkidle")
                        await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                        await self._handle_cookies()
                        return True
                    except Exception:
                        return False
                await asyncio.sleep(2 ** attempt)
        return False

    async def route_node_exit(self, state: JobApplicationState) -> str:
        if state.get("error"):
            logger.warning("[JOBTEASER] Circuit breaker tripped: %s", state["error"])
            return "error"

        user_id = state["user"].id
        search_id = state["job_search"].id

        killed_result = await self.is_agent_killed_for_search.execute(user_id, search_id)
        if killed_result.is_success and killed_result.value:
            logger.info("[JOBTEASER] Kill switch detected for search %s. Aborting gracefully.", search_id)
            return "error"

        return "continue"

    def route_action_intent(self, state: JobApplicationState):
        intent = state.get("action_intent", "SCRAPE")
        if intent == "SUBMIT":
            return "start_with_session"
        return "start"

    async def _is_killed(self, state: JobApplicationState) -> bool:
        """Helper to quickly check if the kill switch was activated during a heavy loop."""
        user_id = state["user"].id
        search_id = state["job_search"].id
        killed_result = await self.is_agent_killed_for_search.execute(user_id, search_id)
        return killed_result.is_success and killed_result.value

    # =========================================================================
    # NODES
    # =========================================================================

    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser")
        logger.info("[JOBTEASER] Starting session")

        preferences = state["preferences"]

        fingerprint = state.get("user_fingerprint")

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless= preferences.browser_headless,
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
            logger.exception("[JOBTEASER] Session error")
            await self._emit(state, stage="Failed", status="error", error="Failed to start the secure browsing session.", error_code="BROWSER_START_FAILED")
            return {"error": "Failed to start the secure browsing session.", "error_code": "BROWSER_START_FAILED"}

    async def start_session_with_auth(self, state: JobApplicationState):
        await self._emit(state, "Initializing Secure Browser")
        logger.info("[JOBTEASER] Booting browser (session injection)")
        user_id = str(state["user"].id)

        fingerprint = state.get("user_fingerprint")

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless= state["preferences"].browser_headless,
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
                    await self._handle_cookies()
                    await self.page.wait_for_selector(
                        'span[class*="Greeting_firstWord"]',
                        state="attached",
                        timeout=45000,
                    )
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[JOBTEASER] Auth boot failed after 3 attempts")
                        await self._emit(state, stage="Failed", status="error", error="Failed to reach JobTeaser.", error_code="JOB_BOARD_UNAVAILABLE")
                        return {"error": "Failed to reach JobTeaser after multiple attempts.", "error_code": "JOB_BOARD_UNAVAILABLE"}
                    await asyncio.sleep(2 ** attempt)

            await human_warmup(self.page, self.base_url)

            offres_link = self.page.locator(
                'a[class*="Nav_app-Nav__link"]:has-text("Offres")'
            ).first
            await offres_link.wait_for(state="visible", timeout=15000)
            await human_click(offres_link)
            # await self.page.wait_for_load_state("networkidle", timeout=90000)
            await self.page.wait_for_selector(
                'input[id="job-ads-autocomplete-keyword-search"]',
                state="visible",
                timeout=90000,
            )

            search_entity = state["job_search"]
            job_title = search_entity.job_title
            contract_types = getattr(search_entity, 'contract_types', [])
            location = getattr(search_entity, 'location', "")

            try:
                search_field = self.page.locator('input[id="job-ads-autocomplete-keyword-search"]')

                await human_click(search_field)
                await human_delay(200, 500)

                await human_type(search_field, job_title)
                await human_delay(800, 1500)

                first_suggestion = self.page.locator(
                    '[id^="job-ads-autocomplete-suggestion-"]'
                ).first
                await first_suggestion.wait_for(state="visible", timeout=8000)
                await human_click(first_suggestion)
                await self.page.wait_for_load_state("networkidle")

                await self._apply_filters(contract_types, location)

                #await self.page.wait_for_load_state("networkidle")

                try:
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="attached", timeout=30000)
                    logger.info("[JOBTEASER] Dummy search complete — session warmed up")
                except Exception:
                    logger.info("[JOBTEASER] Dummy search ran but no cards found. Continuing.")

                return {}
            except Exception:
                logger.exception("[JOBTEASER] Initial search failed during session boot")
                return {}

        except Exception:
            logger.exception("[JOBTEASER] Browser auth init error")
            await self._emit(state, stage="Failed", status="error", error="Failed to initialize browser with session.", error_code="BROWSER_AUTH_FAILED")
            return {"error": "Failed to initialize JobTeaser browser with session.", "error_code": "BROWSER_AUTH_FAILED"}

    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board")
        logger.info("[JOBTEASER] Navigating")
        try:
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=90000)
                    await self._handle_cookies()
                    await self.page.wait_for_selector('button[id="UnloggedUserDropdownButton"]', state="visible", timeout=30000)
                    break
                except Exception:
                    if attempt == 2:
                        await self._emit(state, stage="Failed", status="error", error="Could not reach JobTeaser.", error_code="JOB_BOARD_UNAVAILABLE")
                        return {"error": "Could not reach JobTeaser. The job board might be down or undergoing maintenance.", "error_code": "JOB_BOARD_UNAVAILABLE"}
                    await asyncio.sleep(2 ** attempt)

            await human_warmup(self.page, self.base_url)
            return {}
        except Exception:
            logger.exception("[JOBTEASER] Navigation error")
            await self._emit(state, stage="Failed", status="error", error="Could not reach JobTeaser.", error_code="JOB_BOARD_UNAVAILABLE")
            return {"error": "Navigation failed.", "error_code": "JOB_BOARD_UNAVAILABLE"}

    async def request_login(self, state: JobApplicationState):
        await self._emit(state, "Authenticating")

        prefs = state["preferences"]
        creds = state.get("credentials")
        user_id = str(state["user"].id)

        logger.info("[JOBTEASER] Login phase")

        await self._handle_cookies()

        if prefs.is_full_automation and creds["jobteaser"]:
            login_plain = None
            pass_plain = None

            try:
                for attempt in range(3):
                    try:
                        await human_click(self.page.locator('button#UnloggedUserDropdownButton'))
                        await self.page.wait_for_selector('a[data-testid="signinLink"]', state="visible", timeout=10000)

                        await human_click(self.page.locator('a[data-testid="signinLink"]'))
                        await self.page.wait_for_load_state("networkidle")

                        connect_btn = self.page.locator('a[href*="/users/auth/connect"]')
                        await self.page.wait_for_selector('a[href*="/users/auth/connect"]', state="visible", timeout=15000)
                        await human_click(connect_btn)
                        await self.page.wait_for_load_state("networkidle")

                        await self.page.wait_for_selector('input#email', state="visible", timeout=60000)
                        break
                    except Exception:
                        if attempt == 2:
                            await self._emit(state, stage="Failed", status="error", error="Could not reach the login form.", error_code="LOGIN_MODAL_FAILED")
                            return {"error": "Login failed. Could not reach the login form.", "error_code": "LOGIN_MODAL_FAILED"}
                        await self.page.reload(wait_until="networkidle")
                        await asyncio.sleep(2 ** attempt)

                login_plain = await self.encryption_service.decrypt(creds["jobteaser"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["jobteaser"].password_encrypted)

                for attempt in range(3):
                    try:
                        await human_delay(300, 700)
                        await human_type(self.page.locator('input#email'), login_plain)

                        await human_delay(400, 900)

                        await human_type(self.page.locator('input#passwordInput'), pass_plain)

                        await human_delay(600, 1500)

                        submit_btn = self.page.locator('form[data-e2e="sign-in-form"] button[type="submit"]')
                        await submit_btn.click()
                        await self.page.wait_for_load_state("networkidle", timeout=60000)
                        break
                    except Exception:
                        if attempt == 2:
                            await self._emit(state, stage="Failed", status="error", error="Could not submit credentials.", error_code="LOGIN_SUBMIT_FAILED")
                            return {"error": "Login failed. Could not submit credentials.", "error_code": "LOGIN_SUBMIT_FAILED"}
                        await asyncio.sleep(2 ** attempt)

                for attempt in range(3):
                    try:
                        await self.page.wait_for_selector('span[class*="Greeting_firstWord"]', state="attached", timeout=60000)
                        break
                    except Exception:
                        if attempt == 2:
                            await self._emit(state, stage="Failed", status="error", error="Please check your JobTeaser credentials.", error_code="INVALID_CREDENTIALS")
                            return {"error": "Login failed. Please check your JobTeaser credentials in your settings.", "error_code": "INVALID_CREDENTIALS"}
                        await asyncio.sleep(2 ** attempt)

                offres_link = self.page.locator(
                    'a[class*="Nav_app-Nav__link"]:has-text("Offres")'
                ).first
                await offres_link.wait_for(state="visible", timeout=15000)
                await human_click(offres_link)
                #await self.page.wait_for_load_state("networkidle")

                logger.info("[JOBTEASER] Auto-login successful")
                await self._save_auth_state(user_id)
                return {}

            except Exception:
                logger.exception("[JOBTEASER] Auto-login failed")
                await self._emit(state, stage="Failed", status="error", error="Please check your JobTeaser credentials.", error_code="INVALID_CREDENTIALS")
                return {"error": "Failed to log into JobTeaser. Please check your credentials.", "error_code": "INVALID_CREDENTIALS"}

            finally:
                if login_plain is not None:
                    del login_plain
                if pass_plain is not None:
                    del pass_plain

        else:
            try:
                await self.page.locator('button#UnloggedUserDropdownButton').click()
                await self.page.locator('a[data-testid="signinLink"]').click()
                logger.info("[JOBTEASER] ACTION REQUIRED: Manual login required (waiting 90s)")
                await asyncio.sleep(90)
                await self._save_auth_state(user_id)
                return {}
            except Exception:
                logger.exception("[JOBTEASER] Manual login error")
                await self._emit(state, stage="Failed", status="error", error="Manual login timed out.", error_code="LOGIN_TIMEOUT")
                return {"error": "Manual login timed out.", "error_code": "LOGIN_TIMEOUT"}

    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state, "Searching for Jobs")

        search_entity = state["job_search"]
        job_title = search_entity.job_title
        contract_types = getattr(search_entity, 'contract_types', [])
        location = getattr(search_entity, 'location', "")

        logger.info("[JOBTEASER] Starting search")

        try:
            await human_warmup(self.page, self.base_url)

            for attempt in range(3):
                try:
                    search_input_selector = 'input[id="job-ads-autocomplete-keyword-search"]'
                    await self.page.wait_for_selector(search_input_selector, state="visible", timeout=90000)
                    search_field = self.page.locator(search_input_selector)

                    await human_click(search_field)
                    await human_delay(200, 500)

                    await human_type(search_field, job_title)
                    await human_delay(800, 1500)

                    first_suggestion = self.page.locator(
                        '[id^="job-ads-autocomplete-suggestion-"]'
                    ).first
                    await first_suggestion.wait_for(state="visible", timeout=8000)
                    await human_click(first_suggestion)
                    await self.page.wait_for_load_state("networkidle")
                    break
                except Exception:
                    if attempt == 2:
                        await self._emit(state, stage="Failed", status="error", error="We encountered an issue applying your search filters.", error_code="SEARCH_FILTERS_FAILED")
                        return {"error": f"Failed to execute search for '{job_title}' on JobTeaser.", "error_code": "SEARCH_FILTERS_FAILED"}
                    await self.page.reload(wait_until="networkidle")
                    await asyncio.sleep(2 ** attempt)

            await self._apply_filters(contract_types, location)

            #await self.page.wait_for_load_state("networkidle")

            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="attached", timeout=90000)
                logger.info("[JOBTEASER] Search results loaded")
            except Exception:
                return {"error": "No new matching jobs were found for this search today.", "error_code": "NO_JOBS_FOUND"}

            await self._handle_cookies()
            return {}

        except Exception:
            logger.exception("[JOBTEASER] Search error")
            await self._emit(state, stage="Failed", status="error", error="We encountered an issue applying your search filters.", error_code="SEARCH_FILTERS_FAILED")
            return {"error": f"Failed to execute search for '{job_title}' on JobTeaser.", "error_code": "SEARCH_FILTERS_FAILED"}

    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data")
        logger.info("[JOBTEASER] Scraping jobs")

        user_id = state["user"].id
        search_id = state["job_search"].id
        found_job_entities = []

        worker_job_limit = 1 or state.get("worker_job_limit", 5)

        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=30)
        if not hash_result.is_success:
            logger.warning("[JOBTEASER] Could not fetch ignored hashes: %s", hash_result.error.message)
            ignored_hashes = set()
        else:
            ignored_hashes = hash_result.value

        logger.info("[JOBTEASER] Target: %s jobs. Ignored hashes: %s", worker_job_limit, len(ignored_hashes))

        page_number = 1
        max_pages = 20

        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:

                # 🚨 INJECTED KILL CHECK: Before processing a new page
                if await self._is_killed(state):
                    logger.info("[JOBTEASER] Kill switch detected. Halting pagination.")
                    return {"error": "Agent has been stopped.", "error_code": "AGENT_STOPPED", "found_raw_offers": found_job_entities}

                logger.info("[JOBTEASER] Processing page %s", page_number)

                try:
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
                except Exception:
                    if page_number == 1:
                        logger.info("[JOBTEASER] No results found on page 1")
                        return {"found_raw_offers": []}
                    break

                cards = self.page.locator(self.CARD_SELECTOR)
                count = await cards.count()
                result_url = self.page.url

                for i in range(count):
                    # 🚨 INJECTED KILL CHECK: Before clicking a new card
                    if await self._is_killed(state):
                        logger.info("[JOBTEASER] Kill switch detected. Halting card processing.")
                        return {"error": "Agent has been stopped.", "error_code": "AGENT_STOPPED", "found_raw_offers": found_job_entities}

                    if len(found_job_entities) >= worker_job_limit:
                        break

                    try:
                        card = self.page.locator(self.CARD_SELECTOR).nth(i)

                        await card.scroll_into_view_if_needed()
                        await human_delay(400, 1000)

                        raw_company, raw_title, raw_location = await self.get_raw_job_data(card)

                        if not raw_title:
                            continue

                        fast_hash = self._generate_fast_hash(raw_company, raw_title, str(user_id))
                        if fast_hash in ignored_hashes:
                            continue

                        click_success = False
                        for attempt in range(3):
                            try:
                                card = self.page.locator(self.CARD_SELECTOR).nth(i)
                                title_link = card.locator('h3 a').first
                                await human_click(title_link)
                                await self.page.wait_for_load_state("networkidle")
                                await self.page.wait_for_selector(
                                    'button[data-testid="jobad-DetailView__CandidateActions__Buttons_apply_internal_candidacy"]',
                                    state="attached",
                                    timeout=40000,
                                )
                                click_success = True
                                break
                            except Exception:
                                if attempt == 2:
                                    logger.warning("[JOBTEASER] Card click failed after 3 attempts. Skipping.")
                                    break
                                await asyncio.sleep(2 ** attempt)

                        if not click_success:
                            continue

                        current_url = self.page.url

                        await human_delay(1500, 3500)

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

                        apply_btn = self.page.locator(
                            'button[data-testid="jobad-DetailView__CandidateActions__Buttons_apply_internal_candidacy"]'
                        ).first

                        if await apply_btn.count() > 0:
                            try:
                                await human_click(apply_btn)
                                await self.page.wait_for_load_state("networkidle", timeout=30000)
                                form_url = self.page.url

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
                            except Exception:
                                logger.warning("[JOBTEASER] Apply click failed for %s", raw_title)
                        else:
                            logger.warning("[JOBTEASER] No internal apply button on detail page. Skipping.")

                        if not await self.nav_back(result_url):
                            break

                    except Exception:
                        logger.exception("[JOBTEASER] Error on card %s", i)
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

        except Exception:
            logger.exception("[JOBTEASER] Fatal scraping error")
            await self._emit(state, stage="Failed", status="error", error="A critical error occurred while scanning the job listings.", error_code="SCRAPING_FAILED")
            return {"error": "A critical error occurred while scanning JobTeaser.", "error_code": "SCRAPING_FAILED"}

        if not found_job_entities:
            return {"found_raw_offers": []}

        logger.info("[JOBTEASER] Scraping complete. Returning %s jobs.", len(found_job_entities))
        return {"found_raw_offers": found_job_entities}

    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state, "Submitting Applications")
        logger.info("[JOBTEASER] Submitting applications")

        jobs_to_process = state.get("processed_offers", [])
        user = state["user"]

        assigned_submit_limit = state.get("worker_job_limit", 5)

        teaser_jobs = [
            job for job in jobs_to_process
            if job.job_board == JobBoard.JOBTEASER and job.status == ApplicationStatus.APPROVED
        ]

        if not teaser_jobs:
            logger.info("[JOBTEASER] No approved JobTeaser jobs in submission queue")
            return {"status": "no_teaser_jobs_to_submit"}

        successful_submissions = []
        i = 0
        for offer in teaser_jobs:

            # 🚨 INJECTED KILL CHECK: Before starting the next submission
            if await self._is_killed(state):
                logger.info("[JOBTEASER] Kill switch detected. Halting submissions.")
                return {"error": "Agent has been stopped.", "error_code": "AGENT_STOPPED", "submitted_offers": successful_submissions}

            if len(successful_submissions) >= assigned_submit_limit:
                logger.info("[JOBTEASER] Reached assigned submission limit (%s)", assigned_submit_limit)
                break

            try:
                form_opened = False
                for attempt in range(3):
                    try:
                        await self.page.goto(offer.url, wait_until="commit", timeout=60000)
                        await self._handle_cookies()
                        await human_delay(1500, 3500)

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

                        await human_click(apply_btn)

                        await self.page.wait_for_selector(
                            'form#application-flow-form',
                            state="visible",
                            timeout=15000,
                        )
                        form_opened = True
                        break
                    except Exception:
                        if attempt == 2:
                            logger.warning("[JOBTEASER] Form failed to load after 3 attempts. Skipping.")
                            break
                        await asyncio.sleep(2 ** attempt)

                if not form_opened:
                    i += 1
                    continue

                if user.resume_path:
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"

                    resume_input = self.page.locator('input#resume_0[type="file"]')
                    await resume_input.wait_for(state="attached", timeout=10000)
                    await resume_input.set_input_files({
                        "name": human_name,
                        "mimeType": "application/pdf",
                        "buffer": resume_bytes,
                    })
                    await human_delay(1500, 2500)

                cover_textarea = self.page.locator('textarea[name="coverLetterContent"]')
                if await cover_textarea.count() > 0:
                    if offer.cover_letter:
                        await human_delay(400, 900)
                        await cover_textarea.fill(offer.cover_letter)
                    else:
                        logger.warning("[JOBTEASER] Form requires cover letter but none generated. Skipping %s.", offer.job_title)
                        i += 1
                        continue

                await human_delay(1500, 3500)

                submit_btn = self.page.locator(
                    'button[data-testid="jobad-DetailView__ApplicationFlow__Buttons__apply_button"]'
                )
                await submit_btn.wait_for(state="attached", timeout=10000)

                try:
                    await self.page.wait_for_selector(
                        'button[data-testid="jobad-DetailView__ApplicationFlow__Buttons__apply_button"]:not([disabled])',
                        state="visible",
                        timeout=15000,
                    )
                except Exception:
                    logger.warning("[JOBTEASER] Submit button stayed disabled for %s. Form may be incomplete.", offer.job_title)
                    continue

                await submit_btn.click()

                try:
                    await self.page.wait_for_selector(
                        'aside[data-testid="jobad-DetailView__Heading__already_applied"]',
                        state="visible",
                        timeout=45000,
                    )
                    logger.info("[JOBTEASER] Application submitted for %s", offer.job_title)
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                except Exception:
                    logger.warning("[JOBTEASER] Submission of %s failed — confirmation not received", offer.url)
                    continue

            except Exception:
                logger.exception("[JOBTEASER] Submission failed for %s", offer.url)

            i += 1

        if not successful_submissions:
            await self._emit(state, stage="Failed", status="error", error="All application attempts failed.", error_code="SUBMISSION_FAILED")
            return {"error": "All JobTeaser application attempts failed. Forms may have changed.", "error_code": "SUBMISSION_FAILED"}

        logger.info("[JOBTEASER] Successfully submitted %s applications", len(successful_submissions))
        return {"submitted_offers": successful_submissions}

    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up")
        await self.force_cleanup()

        # 🚨 Fail-soft cleanup: if the circuit breaker was tripped, scrub the error
        # from state so the master can keep partial worker results from the happy path.
        if state.get("error"):
            return {
                "error": "",
                "error_code": ""
            }

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