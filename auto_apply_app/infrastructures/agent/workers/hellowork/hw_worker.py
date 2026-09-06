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
from auto_apply_app.infrastructures.agent.stage_codes import StageCode
from auto_apply_app.application.use_cases.agent_state_use_cases import (
    IsAgentKilledForSearchUseCase,
    HeartbeatAgentForSearchUseCase,
)
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort
from auto_apply_app.application.use_cases.agent_use_cases import GetIgnoredHashesUseCase

# Human behavior helpers
from auto_apply_app.infrastructures.agent.human_behavior import (
    human_delay,
    human_type,
    human_click,
    human_hover,
    human_warmup,
    human_scroll,
    human_read_page,
    human_idle_drift,
    human_scan_list,
    human_long_pause,
    should_skip_card,
    should_hover_without_clicking,
    reset_mouse_state,
)
from auto_apply_app.infrastructures.agent.pacing import HumanPacing, Tier
from auto_apply_app.infrastructures.agent.fingerprint_alignment import (
    align_fingerprint_to_browser,
)

logger = logging.getLogger(__name__)


class HelloWorkWorker(HumanPacing):

    # Reading band for this board: 30-90s.
    _READ_BAND_S = (30.0, 90.0)
    _READ_LONG_CHARS = 5400

    CARD_SELECTOR = 'li[data-id-storage-target="item"]'

    def __init__(
        self,
        get_ignored_hashes: GetIgnoredHashesUseCase,
        encryption_service: EncryptionServicePort,
        file_storage: FileStoragePort,
        is_agent_killed_for_search: IsAgentKilledForSearchUseCase,
        heartbeat: HeartbeatAgentForSearchUseCase,
        session_store=None,
    ):
        self.get_ignored_hashes = get_ignored_hashes
        self.encryption_service = encryption_service
        self.base_url = "https://www.hellowork.com/fr-fr/"
        self.file_storage = file_storage
        self.is_agent_killed_for_search = is_agent_killed_for_search
        self.heartbeat = heartbeat
        # C-2: durable GCS session cache. None -> local-file only (pre-C-2 behavior).
        self.session_store = session_store

        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        self._progress_callback = None
        self._source_name = "HELLOWORK"
        # Canonical board key. Indexes this worker's slice of the per-board
        # fingerprint and proxy maps the master builds.
        self._board_key = "hellowork"
        # The persona this run is wearing. Cookie jars are keyed on it, so a
        # jar can never be replayed under a different device.
        self._fingerprint_id: Optional[str] = None

        # Human pacing. This worker had no budget guard and no heartbeat while
        # idle, so slowing it down without the mixin would have got it declared
        # dead at AGENT_HEARTBEAT_STALE_SECONDS.
        self._init_pacing("HELLOWORK")

        # Current user id for print logging (set at node entry)
        self._uid = "unknown"

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _plog(self, task: str, user_id=None):
        """Strategic print logging: [Worker for user_id] : task"""
        uid = user_id if user_id is not None else self._uid
        print(f"[{self._source_name} for {uid}] : {task}", flush=True)

    def _get_session_file_path(self, user_id: str) -> str:
        """Local path for this run's cookie jar.

        Scoped by persona: the jar belongs to the device that acquired it, so
        rotating to another device cannot pick up the previous one's cookies.
        Falls back to "nofp" only when no fingerprint resolved at all.
        """
        directory = os.path.join(os.getcwd(), "tmp", "sessions")
        os.makedirs(directory, exist_ok=True)
        fp = self._fingerprint_id or "nofp"
        return os.path.join(directory, f"{user_id}_hellowork_{fp}_session.json")

    async def _save_auth_state(self, user_id: str):
        if self.context:
            path = self._get_session_file_path(user_id)
            await self.context.storage_state(path=path)
            logger.info("[HW] Session saved for user %s", user_id)
            self._plog("session cookies saved to disk", user_id)
            # C-2: mirror the refreshed session to GCS (best-effort, never fatal).
            if self.session_store:
                await self.session_store.save_from_local(user_id, "hellowork", self._fingerprint_id or "nofp", path)

    def _get_auth_state_path(self, user_id: str) -> str | None:
        path = self._get_session_file_path(user_id)
        if os.path.exists(path):
            return path
        return None

    async def _emit(
        self,
        state: JobApplicationState,
        stage: str,
        status: str = "in_progress",
        error: str = None,
        error_code: str = None,
        stage_code: str = None,
        progress_percent: int = None,
        count_band: str = None,
        count_done: int = None,
        count_total: int = None,
    ):
        if not self._progress_callback:
            return
        try:
            search_id = str(state["job_search"].id) if "job_search" in state else ""
            await self._progress_callback({
                "source": self._source_name.upper(),
                "stage": stage,
                "stage_code": stage_code,
                "node": self._source_name.lower(),
                "status": "error" if error else status,
                "error": error,
                "error_code": error_code or ("SYSTEMERROR" if error else None),
                "search_id": search_id,
                "progress_percent": progress_percent,
                # Raw counts for the counting bands. AgentRunner sums these
                # across boards and rewrites progress_percent, because this
                # worker only knows its own share of the total.
                "count_band": count_band,
                "count_done": count_done,
                "count_total": count_total,
                "progress_track": ("submit" if state.get("action_intent") == "SUBMIT"
                                   else "launch"),
                "is_premium": getattr(
                    getattr(state.get("subscription"), "account_type", None),
                    "name", "") == "PREMIUM",
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
            self._plog("card content never became ready -> returning empty data")
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

    async def close_google_auth_popup(self):
        try:
            popup_selector = 'button[data-cy="closeHWOneTap"]'
            await self.page.wait_for_selector(popup_selector, state="visible", timeout=15000)
            await human_click(self.page.locator(popup_selector))
            logger.info("[HW] Closed Google auth popup")
            self._plog("google auth popup detected -> closed")
        except Exception:
            logger.debug("[HW] No Google auth popup to close")

    async def force_cleanup(self):
        logger.info("[HW] Force cleanup initiated")
        self._plog("force cleanup initiated")

        # Before the page goes: the input backend is registered against it, and
        # the Xvfb it may be driving outlives the browser unless stopped here.
        await self._close_display()

        try:
            if self.page:
                # _last_mouse_pos is keyed by id(page) and nothing evicts it, so
                # entries pile up across runs and a recycled id would hand a new
                # page some other page's stale cursor origin.
                reset_mouse_state(self.page)
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
        self._plog("force cleanup complete -> browser fully closed")

    async def _get_job_attribute(self, selector: str, default_value: str = None):
        try:
            await self.page.wait_for_selector(selector, state='attached', timeout=5000)
            text = await self.page.locator(selector).first.inner_text()
            return text.strip()
        except Exception:
            return default_value

    async def _neutralize_pagination_input(self):
        """Removes the rogue mobile pagination input that auto-submits the search form on blur,
        causing unexpected page jumps during card iteration."""
        try:
            await self.page.evaluate("""() => {
                document.querySelectorAll(
                    'input[data-toggle-attribute-attribute-param="name"][data-toggle-attribute-value-param="p"]'
                ).forEach(el => el.remove());
            }""")
        except Exception:
            logger.debug("[HW] Could not neutralize pagination input (page may have navigated)")

    async def _block_tracking(self):
        """Blocks analytics, tracking, and tag manager requests to prevent network noise
        and ensure load states resolve predictably."""
        async def _route_handler(route):
            url = route.request.url
            if any(d in url for d in [
                "googletagmanager.com", "google-analytics.com", "doubleclick.net",
                "t.hellowork.com", "piano-analytics", "screeb"
            ]):
                await route.abort()
            else:
                await route.continue_()
        await self.context.route("**/*", _route_handler)

    async def _nav_back_to_search(self, search_url: str) -> bool:
        self._plog("navigating back to search results")
        for attempt in range(3):
            try:
                await self.page.goto(search_url, wait_until="domcontentloaded")
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
                await self._neutralize_pagination_input()
                await human_delay(800, 2000)
                return True
            except Exception:
                if attempt == 2:
                    logger.warning("[HW] Could not return to search results after 3 attempts")
                    self._plog("could not return to search results after 3 attempts")
                    return False
                self._plog(f"nav back attempt {attempt + 1} failed -> retrying")
                await asyncio.sleep(2 ** attempt)
        return False

    async def _apply_filters(self, contract_types: list[ContractType], min_salary: int):
        try:
            logger.info("[HW] Applying search filters")
            self._plog("opening filter panel")

            FILTER_LABEL_SELECTOR = 'div[class="layout-inner-grid"] label[for="allFilters"][data-cy="serpFilters"]:has-text(" Filtres ")'

            for attempt in range(3):
                try:
                    await self.page.wait_for_selector(FILTER_LABEL_SELECTOR, state="visible", timeout=20000)
                    all_filters_label = self.page.locator(FILTER_LABEL_SELECTOR).first
                    await all_filters_label.click()
                    await self.page.wait_for_selector('input#toggle-salary', state="attached", timeout=10000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[HW] Could not open filter panel after 3 attempts")
                        self._plog("filter panel never opened -> aborting filters")
                        raise
                    self._plog(f"filter panel attempt {attempt + 1} failed -> retrying")
                    await asyncio.sleep(2 ** attempt)

            if contract_types:
                if ContractType.FREELANCE in contract_types:
                    contract_types.append(ContractType.INDEPENDENT)  # Treat "Independant" as a synonym for "Freelance" on HelloWork

                for contract in contract_types:
                    try:
                        checkbox_selector = f'input[id="c-{str(contract.value)}"]'
                        checkbox = self.page.locator(checkbox_selector)
                        if await checkbox.count() > 0 and not await checkbox.is_checked():
                            self._plog(f"checking contract type: {contract.value}")
                            await human_delay(300, 700)
                            contract_label = self.page.locator(f'label[for="c-{str(contract.value)}"]')
                            await contract_label.click()
                    except Exception:
                        pass

            if min_salary > 0:
                toggle_salary = self.page.locator('input#toggle-salary')
                if await toggle_salary.count() > 0:
                    self._plog(f"setting minimum salary: {min_salary}")
                    await human_delay(300, 700)
                    toggle_salary_label = self.page.locator('label[for="toggle-salary"]')
                    await toggle_salary_label.click()
                    await self.page.wait_for_selector('input#msa:not([disabled])', timeout=3000)
                    # Typed, not injected. Setting .value from JS and hand-firing
                    # change/input leaves no key events and no focus — the page
                    # sees a number appear in a field nobody touched.
                    await self._type(self.page.locator('input#msa'), str(min_salary))

            await human_delay(800, 1800)

            submit_filters_btn = self.page.locator('[data-cy="offerNumberButton"]')
            if await submit_filters_btn.is_visible():
                self._plog("submitting filters")
                for attempt in range(3):
                    try:
                        await submit_filters_btn.click()
                        await self.page.wait_for_load_state("domcontentloaded")
                        await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=40000)
                        await self._neutralize_pagination_input()
                        self._plog("filters applied -> results refreshed")
                        break
                    except Exception:
                        if attempt == 2:
                            logger.exception("[HW] Filter submit failed after 3 attempts")
                            self._plog("filter submit failed after 3 attempts -> aborting")
                            raise
                        await asyncio.sleep(2 ** attempt)

        except Exception:
            logger.exception("[HW] Error applying filters")
            self._plog("error while applying filters -> raising")
            raise

    async def _handle_hw_pagination(self, page_number: int) -> bool:
        try:
            next_button = self.page.locator(
                'button[name="p"]:has(svg > use[href$="#right"])'
            ).first

            if await next_button.count() == 0:
                logger.info("[HW] No next button found. Reached last page.")
                self._plog(f"no next button on page {page_number} -> reached last page")
                return False

            is_disabled = await next_button.get_attribute("aria-disabled")
            if is_disabled == "true":
                logger.info("[HW] Next button disabled. Reached last page.")
                self._plog(f"next button disabled on page {page_number} -> reached last page")
                return False

            logger.info("[HW] Moving to page %s", page_number + 1)
            self._plog(f"pagination -> moving to page {page_number + 1}")
            await human_delay(1500, 3500)

            for attempt in range(3):
                try:
                    await self._click(next_button)
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
                    await self._neutralize_pagination_input()
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[HW] Pagination failed after 3 attempts")
                        self._plog("pagination click failed after 3 attempts -> stopping pagination")
                        return False
                    self._plog(f"pagination click attempt {attempt + 1} failed -> retrying")
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            return True

        except Exception:
            logger.exception("[HW] Pagination error")
            self._plog("unexpected pagination error -> stopping pagination")
            return False

    async def _handle_cookies(self):
        is_visible = False
        try:
            await self.page.wait_for_selector('button[id="hw-cc-notice-continue-without-accepting-btn"]', state="attached", timeout=3000)
            cookie_btn = self.page.locator('button[id="hw-cc-notice-continue-without-accepting-btn"]')
            if await cookie_btn.count() > 0:
                is_visible = True
                self._plog("cookie banner detected -> continuing without accepting")
                await human_delay(300, 800)
                await self._click(cookie_btn)
        except Exception:
            if is_visible:
                self._plog("cookie banner click failed -> removing overlay via JS")
                await self.page.wait_for_selector('div[class="hw-cc-main"]', state='attached', timeout=2000)
                await self.page.evaluate("""() => {
                    const overlays = document.querySelectorAll('.hw-cc-main');
                    overlays.forEach(el => el.remove());
                }""")

    async def route_node_exit(self, state: JobApplicationState) -> str:
        if state.get("error"):
            logger.warning("[HW] Circuit breaker tripped: %s", state["error"])
            self._plog(f"circuit breaker tripped -> routing to cleanup ({state['error']})")
            return "error"

        user_id = state["user"].id
        search_id = state["job_search"].id

        killed_result = await self.is_agent_killed_for_search.execute(user_id, search_id)
        if killed_result.is_success and killed_result.value:
            logger.info("[HW] Kill switch detected for search %s. Aborting gracefully.", search_id)
            self._plog(f"kill switch detected for search {search_id} -> aborting gracefully")
            return "error"

        return "continue"

    def route_action_intent(self, state: JobApplicationState):
        intent = state.get("action_intent", "SCRAPE")
        self._plog(f"routing intent: {intent}", user_id=state["user"].id)
        if intent == "SUBMIT":
            return "start_with_session"
        return "start"

    async def _is_killed(self, state: JobApplicationState) -> bool:
        """Helper to quickly check if the kill switch was activated during a heavy loop."""
        user_id = state["user"].id
        search_id = state["job_search"].id
        killed_result = await self.is_agent_killed_for_search.execute(user_id, search_id)
        return killed_result.is_success and killed_result.value

    async def _beat(self, state: JobApplicationState):
        """Mark the agent alive. Fail-soft: never blocks or aborts a node."""
        try:
            await self.heartbeat.execute(state["job_search"].id)
        except Exception:
            pass

    async def _is_session_valid(self) -> bool:
        """HelloWork renders summary[data-cy="headerAccountMenu"] ('Se connecter')
        ONLY when logged out. Logged-in users get the initials-avatar summary with
        no such data-cy. So: marker present -> expired; marker absent -> valid.

        We test for the logged-OUT marker (not the avatar) because the avatar is
        populated by an async account-data fetch and can lag on a fresh load,
        whereas the anonymous marker renders synchronously. state='attached'
        because the marker carries a 'hidden' class on narrow viewports."""
        try:
            await self.page.wait_for_selector(
                'a[data-cy="headerAccountLogOut"]', state="attached", timeout=45000
            )
            self._plog("session check: 'Deconnexion' marker present -> session VALID")
            return True
        except Exception:
            self._plog("session check: no 'Deconnexion' marker -> session EXPIRED")
            return False

    async def _perform_auto_login(self, state: JobApplicationState) -> bool:
        """Full-automation credential login, mirroring request_login's logic.
        Assumes the browser/page are booted and on the HelloWork homepage with
        the logged-out account menu present. Returns True on success and
        re-saves the session. Full-automation only."""
        creds = state.get("credentials")
        user_id = str(state["user"].id)

        if not (creds and creds.get("hellowork")):
            self._plog("re-login: no stored HelloWork credentials -> cannot auto-login")
            return False

        login_plain = None
        pass_plain = None
        try:
            # Open login modal
            self._plog("re-login: opening login modal")
            for attempt in range(3):
                try:
                    await human_click(self.page.locator('[data-cy="headerAccountMenu"]'))
                    await human_click(self.page.locator('[data-cy="headerAccountLogIn"]'))
                    await self.page.wait_for_selector('input[name="email2"]', state="visible", timeout=30000)
                    break
                except Exception:
                    if attempt == 2:
                        self._plog("re-login: login modal never opened")
                        return False
                    await self.page.reload(wait_until="domcontentloaded")
                    await asyncio.sleep(2 ** attempt)

            login_plain = await self.encryption_service.decrypt(creds["hellowork"].login_encrypted)
            pass_plain = await self.encryption_service.decrypt(creds["hellowork"].password_encrypted)
            self._plog("re-login: credentials decrypted")

            # Fill + submit
            self._plog("re-login: typing credentials")
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
                    await self._click(self.page.locator('button[type="button"][class="profile-button"]'))
                    await self.page.wait_for_selector('a[data-cy="cpMenuDashboard"]', state="attached", timeout=90000)
                    self._plog("re-login: credentials submitted -> dashboard menu detected")
                    break
                except Exception:
                    if attempt == 2:
                        self._plog("re-login: credential submission failed")
                        return False
                    await asyncio.sleep(2 ** attempt)

            # Return to homepage to confirm + stabilize
            self._plog("re-login: returning to homepage to verify")
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
                    await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=20000)
                    break
                except Exception:
                    if attempt == 2:
                        self._plog("re-login: homepage verification failed")
                        return False
                    await asyncio.sleep(2 ** attempt)

            await self._save_auth_state(user_id)
            self._plog("re-login successful -> session re-saved")
            return True

        except Exception:
            logger.exception("[HW] Re-login failed")
            self._plog("re-login crashed with unexpected error")
            return False

        finally:
            if login_plain is not None:
                del login_plain
            if pass_plain is not None:
                del pass_plain

    # =========================================================================
    # NODES
    # =========================================================================

    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser", stage_code=StageCode.INITIALIZING_BROWSER, progress_percent=self._progress(state, "start"))
        await self._beat(state)
        logger.info("[HW] Starting session")
        self._uid = str(state["user"].id)
        self._start_pacing(state.get("run_token"))
        self._plog("NODE start_session -> launching stealth browser (SCRAPE track)")
        preferences = state["preferences"]

        fingerprint = (state.get("user_fingerprints") or {}).get(self._board_key)
        proxy_config = (state.get("proxy_configs") or {}).get(self._board_key)
        # Record the persona BEFORE any session path is built — _get_session_file_path
        # reads it, and a stale value would point at another device's cookie jar.
        self._fingerprint_id = str(fingerprint.id) if fingerprint else None

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                **await self._browser_launch_kwargs(
                    {
                        "headless": preferences.run_browser_headless,
                        "args": ['--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage'],
                    },
                    fingerprint,
                )
            )
            fingerprint = align_fingerprint_to_browser(
                fingerprint, self.browser.version, self._plog
            )
            fingerprint = self._fit_to_display(fingerprint)

            context_kwargs = {}
            if fingerprint:
                self._plog("applying user fingerprint to browser context")
                context_kwargs.update(fingerprint.to_playwright_context_args())
            else:
                # No fingerprint resolved. Every such run shares one identity, so
                # this is a degraded path, not a normal one — log it loudly.
                self._plog("NO FINGERPRINT RESOLVED -> falling back to a shared default identity")
                logger.warning("[%s] running without a fingerprint", self._source_name)

            if proxy_config:
                self._plog("routing browser context through proxy")
                context_kwargs["proxy"] = {
                    "server": proxy_config["server"],
                    "username": proxy_config["username"],
                    "password": proxy_config["password"],
                }

            self.context = await self.browser.new_context(**context_kwargs)
            await self._suppress_popups(self.context)

            # Stealth FIRST, fingerprint SECOND. playwright_stealth registers its
            # own WebGL getParameter patch hardcoded to "Intel Inc." / "Intel Iris
            # OpenGL Engine"; init scripts run in registration order, so applying
            # it after ours silently replaced the persona's GPU on every run.
            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)

            if fingerprint:
                await self.context.add_init_script(fingerprint.to_init_script())
            await self._block_tracking()
            # After _block_tracking, never before: the most recently registered
            # route runs first, and _block_tracking ends its chain with
            # continue_(), which would starve this one of every request.
            await self._conserve_bandwidth(self.context)
            self.page = await self.context.new_page()
            await self._mount_input()
            self._plog("browser session ready (tracking blocked)")
            return {}
        except Exception:
            logger.exception("[HW] Session error")
            self._plog("browser session failed to start")
            await self._emit(state, stage="Failed", status="error", error="Failed to start the secure browsing session.", error_code="BROWSER_START_FAILED")
            return {"error": "Failed to start the secure browsing session.", "error_code": "BROWSER_START_FAILED"}

    async def start_session_with_auth(self, state: JobApplicationState):
        await self._emit(state, "Initializing Secure Browser", stage_code=StageCode.INITIALIZING_BROWSER, progress_percent=self._progress(state, "start_with_session"))
        await self._beat(state)
        logger.info("[HW] Booting browser (session injection)")
        user_id = str(state["user"].id)
        self._uid = user_id
        self._start_pacing(state.get("run_token"))
        self._plog("NODE start_session_with_auth -> booting browser (SUBMIT track)")

        fingerprint = (state.get("user_fingerprints") or {}).get(self._board_key)
        proxy_config = (state.get("proxy_configs") or {}).get(self._board_key)
        # Record the persona BEFORE any session path is built — _get_session_file_path
        # reads it, and a stale value would point at another device's cookie jar.
        self._fingerprint_id = str(fingerprint.id) if fingerprint else None

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                **await self._browser_launch_kwargs(
                    {
                        "headless": state["preferences"].run_browser_headless,
                        "args": ['--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage'],
                    },
                    fingerprint,
                )
            )
            fingerprint = align_fingerprint_to_browser(
                fingerprint, self.browser.version, self._plog
            )
            fingerprint = self._fit_to_display(fingerprint)

            # C-2: pull the durable session from GCS into the local path first, so
            # _get_auth_state_path finds it. No session / any error -> logs in fresh.
            if self.session_store:
                await self.session_store.load_to_local(
                    user_id, "hellowork", self._fingerprint_id or "nofp",
                    self._get_session_file_path(user_id)
                )
            session_path = self._get_auth_state_path(user_id)

            context_kwargs = {}
            if fingerprint:
                # One source for both tracks. The auth track used to bolt on
                # device_scale_factor/has_touch/is_mobile that the scrape track
                # lacked, so a single run presented two different contexts.
                context_kwargs.update(fingerprint.to_playwright_context_args())
            else:
                self._plog("NO FINGERPRINT RESOLVED -> falling back to a shared default identity")
                logger.warning("[%s] running without a fingerprint", self._source_name)

            if session_path:
                self._plog("saved session found -> injecting cookies")
                context_kwargs["storage_state"] = session_path
            else:
                self._plog("no saved session -> booting fresh context")

            if proxy_config:
                self._plog("routing browser context through proxy")
                context_kwargs["proxy"] = {
                    "server": proxy_config["server"],
                    "username": proxy_config["username"],
                    "password": proxy_config["password"],
                }

            self.context = await self.browser.new_context(**context_kwargs)
            await self._suppress_popups(self.context)

            # Stealth FIRST, fingerprint SECOND. playwright_stealth registers its
            # own WebGL getParameter patch hardcoded to "Intel Inc." / "Intel Iris
            # OpenGL Engine"; init scripts run in registration order, so applying
            # it after ours silently replaced the persona's GPU on every run.
            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)

            if fingerprint:
                await self.context.add_init_script(fingerprint.to_init_script())
            await self._block_tracking()
            # See the note at the other context site: must come after
            # _block_tracking, whose continue_() would otherwise end the chain.
            await self._conserve_bandwidth(self.context)
            self.page = await self.context.new_page()
            await self._mount_input()

            self._plog("navigating to hellowork.com homepage")
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=120000)
                    await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=60000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[HW] Auth boot failed after 3 attempts")
                        self._plog("homepage unreachable after 3 attempts -> aborting")
                        await self._emit(state, stage="Failed", status="error", error="Failed to reach HelloWork.", error_code="JOB_BOARD_UNAVAILABLE")
                        return {"error": "Failed to reach HelloWork after multiple attempts.", "error_code": "JOB_BOARD_UNAVAILABLE"}
                    self._plog(f"homepage load attempt {attempt + 1} failed -> retrying")
                    await asyncio.sleep(2 ** attempt)

            
            await self._handle_cookies()
            await self.close_google_auth_popup()
            await human_warmup(self.page, self.base_url)

            # --- SESSION VALIDITY GATE ---
            # HW's search box is public, so search succeeding does NOT prove auth.
            # The submit node fills an authenticated application form, so confirm
            # login here before proceeding.
            if not await self._is_session_valid():
                self._plog("session expired -> re-authenticating via auto-login")
                await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=120000)
                if not await self._perform_auto_login(state):
                    self._plog("auto-login fallback failed -> aborting SUBMIT track")
                    try:
                        os.remove(self._get_session_file_path(user_id))
                    except OSError:
                        pass
                    await self._emit(
                        state, stage="Failed", status="error",
                        error="Your HelloWork session has expired and we couldn't reconnect automatically. Please check your HelloWork credentials in your settings.",
                        error_code="INVALID_CREDENTIALS",
                    )
                    return {
                        "error": "Your HelloWork session has expired and we couldn't reconnect automatically. Please check your HelloWork credentials in your settings.",
                        "error_code": "INVALID_CREDENTIALS",
                    }
                self._plog("session recovered via fallback login")


            search_entity = state["job_search"]
            job_title = search_entity.job_title
            contract_types = getattr(search_entity, 'contract_types', [])
            min_salary = getattr(search_entity, 'min_salary', 0)
            location = getattr(search_entity, 'location', "")

            try:
                self._plog(f"typing search: '{job_title}'" + (f" in '{location}'" if location and location.strip() else ""))

                await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=15000)
                search_field = self.page.locator('input[id="k"]')
                # Click before typing, as APEC does (apec_worker.py:541). `clear()`
                # focuses the node inside the renderer but leaves the BROWSER's
                # focused widget alone, so under XTEST the keys could go to the
                # address bar instead. Only a real press moves focus to the page.
                await self._click(search_field, hesitation=False)
                await search_field.clear()
                await human_delay(200, 500)
                await human_type(search_field, job_title)

                if location and location.strip() != "":
                    await human_delay(300, 500)
                    await human_type(self.page.locator('input[id="l"]'), location)
                await human_delay(500, 1200)
                await self._press("Enter")

                await self.page.wait_for_load_state("domcontentloaded")
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=30000)
                await self._neutralize_pagination_input()
                await self._handle_cookies()
                self._plog("search results loaded (auth track)")

                if contract_types or min_salary > 0:
                    await self._apply_filters(contract_types, min_salary)

                return {}
            except Exception:
                logger.exception("[HW] Session initialization error")
                self._plog("search failed during auth track -> continuing anyway")
                return {}

        except Exception:
            logger.exception("[HW] Browser auth init error")
            self._plog("browser auth initialization failed")
            await self._emit(state, stage="Failed", status="error", error="Failed to initialize browser with session.", error_code="BROWSER_AUTH_FAILED")
            return {"error": "Failed to initialize HelloWork browser.", "error_code": "BROWSER_AUTH_FAILED"}

    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board", stage_code=StageCode.NAVIGATING, progress_percent=self._progress(state, "nav"))
        await self._beat(state)
        logger.info("[HW] Navigating to HelloWork")
        self._plog("NODE go_to_job_board -> navigating to hellowork.com")
        try:
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
                    await self._handle_cookies()
                    await self.close_google_auth_popup()
                    await self.page.wait_for_selector('[data-cy="headerAccountMenu"]', state="visible", timeout=30000)
                    break
                except Exception:
                    if attempt == 2:
                        self._plog("hellowork.com unreachable after 3 attempts -> aborting")
                        await self._emit(state, stage="Failed", status="error", error="Could not reach HelloWork.", error_code="JOB_BOARD_UNAVAILABLE")
                        return {"error": "Could not reach HelloWork. The job board might be down or undergoing maintenance.", "error_code": "JOB_BOARD_UNAVAILABLE"}
                    self._plog(f"navigation attempt {attempt + 1} failed -> retrying")
                    await asyncio.sleep(2 ** attempt)

            self._plog("homepage loaded -> performing human warmup")
            await human_warmup(self.page, self.base_url)
            await self._arrival_browse(state, "hellowork homepage")
            return {}
        except Exception:
            logger.exception("[HW] Navigation error")
            self._plog("unexpected navigation error -> aborting")
            await self._emit(state, stage="Failed", status="error", error="Could not reach HelloWork.", error_code="JOB_BOARD_UNAVAILABLE")
            return {"error": "Navigation failed.", "error_code": "JOB_BOARD_UNAVAILABLE"}

    async def request_login(self, state: JobApplicationState):
        await self._emit(state, "Authenticating", stage_code=StageCode.AUTHENTICATING, progress_percent=self._progress(state, "login"))
        await self._beat(state)
        prefs = state["preferences"]
        creds = state.get("credentials")
        user_id = str(state["user"].id)

        logger.info("[HW] Login phase")
        self._plog("NODE request_login -> entering login phase")

        if prefs.is_full_automation and creds["hellowork"]:
            self._plog("full automation mode -> attempting auto-login")
            login_plain = None
            pass_plain = None

            try:
                self._plog("opening login modal")
                for attempt in range(3):
                    try:
                        await human_click(self.page.locator('[data-cy="headerAccountMenu"]'))
                        await human_click(self.page.locator('[data-cy="headerAccountLogIn"]'))
                        await self.page.wait_for_selector('input[name="email2"]', state="visible", timeout=30000)
                        break
                    except Exception:
                        if attempt == 2:
                            self._plog("login modal never opened -> aborting login")
                            await self._emit(state, stage="Failed", status="error", error="Could not open the login modal.", error_code="LOGIN_MODAL_FAILED")
                            return {"error": "Login failed. Could not open the login modal.", "error_code": "LOGIN_MODAL_FAILED"}
                        self._plog(f"login modal attempt {attempt + 1} failed -> reloading and retrying")
                        await self.page.reload(wait_until="domcontentloaded")
                        await asyncio.sleep(2 ** attempt)

                login_plain = await self.encryption_service.decrypt(creds["hellowork"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["hellowork"].password_encrypted)
                self._plog("credentials decrypted")

                self._plog("typing credentials")
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
                        await self._click(self.page.locator('button[type="button"][class="profile-button"]'))
                        await self.page.wait_for_selector('a[data-cy="cpMenuDashboard"]', state="attached", timeout=90000)
                        self._plog("credentials submitted -> dashboard menu detected")
                        break
                    except Exception:
                        if attempt == 2:
                            self._plog("credential submission failed after 3 attempts -> aborting login")
                            await self._emit(state, stage="Failed", status="error", error="Could not submit credentials.", error_code="LOGIN_SUBMIT_FAILED")
                            return {"error": "Login failed. Could not submit credentials.", "error_code": "LOGIN_SUBMIT_FAILED"}
                        await asyncio.sleep(2 ** attempt)

                self._plog("returning to homepage to verify login")
                for attempt in range(3):
                    try:
                        await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
                        await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=20000)
                        break
                    except Exception:
                        if attempt == 2:
                            self._plog("login verification failed -> bad credentials?")
                            await self._emit(state, stage="Failed", status="error", error="Please check your HelloWork credentials.", error_code="INVALID_CREDENTIALS")
                            return {"error": "Login failed. Please check your HelloWork credentials in your settings.", "error_code": "INVALID_CREDENTIALS"}
                        await asyncio.sleep(2 ** attempt)

                await self._save_auth_state(user_id)
                logger.info("[HW] Auto-login successful")
                self._plog("auto-login successful")
                await self._settle(state, "post-login")
                return {}

            except Exception:
                logger.exception("[HW] Auto-login failed")
                self._plog("auto-login crashed with unexpected error")
                await self._emit(state, stage="Failed", status="error", error="Please check your HelloWork credentials.", error_code="INVALID_CREDENTIALS")
                return {"error": "Failed to log into HelloWork. Check credentials.", "error_code": "INVALID_CREDENTIALS"}

            finally:
                if login_plain is not None:
                    del login_plain
                if pass_plain is not None:
                    del pass_plain

        else:
            self._plog("semi-automation mode -> waiting for manual login (90s)")
            try:
                await self._click(self.page.locator('[data-cy="headerAccountMenu"]'))
                await self._click(self.page.locator('[data-cy="headerAccountLogIn"]'))
                logger.info("[HW] ACTION REQUIRED: Please log in manually within 90 seconds")
                # Heartbeat-safe: a bare sleep beats nothing for 90s, which is over half
                # the AGENT_HEARTBEAT_STALE_SECONDS budget.
                await human_long_pause(90, 90, on_tick=lambda: self._beat(state), tick_every=30.0)
                self._plog("manual login window elapsed -> verifying access")
                await self._click(self.page.locator('a[href="/fr-fr"]').first)
                await self._save_auth_state(user_id)
                self._plog("manual login confirmed")
                return {}
            except Exception:
                logger.exception("[HW] Manual login error")
                self._plog("manual login timed out or failed")
                await self._emit(state, stage="Failed", status="error", error="Manual login timed out.", error_code="LOGIN_TIMEOUT")
                return {"error": "Manual login timed out.", "error_code": "LOGIN_TIMEOUT"}

    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state, "Searching for Jobs", stage_code=StageCode.SEARCHING, progress_percent=self._progress(state, "search"))
        await self._beat(state)

        search_entity = state["job_search"]
        job_title = search_entity.job_title
        contract_types = getattr(search_entity, 'contract_types', [])
        min_salary = getattr(search_entity, 'min_salary', 0)
        location = getattr(search_entity, 'location', "")

        logger.info("[HW] Starting search")
        self._plog(f"NODE search_jobs -> searching '{job_title}'" + (f" in '{location}'" if location and location.strip() else ""))
        try:
            await human_warmup(self.page, self.base_url)

            for attempt in range(3):
                try:
                    await self.page.wait_for_selector('input[id="k"]', state="visible", timeout=15000)
                    search_field = self.page.locator('input[id="k"]')
                    # See the note at the other search-field site: click first so
                    # the browser's focus is on the page, not the omnibox.
                    await self._click(search_field, hesitation=False)
                    await search_field.clear()
                    await human_delay(200, 500)
                    await human_type(search_field, job_title)
                    if location and location.strip() != "":
                        await human_delay(300, 700)
                        await self.page.locator('input[id="l"]').clear()
                        await human_type(self.page.locator('input[id="l"]'), location)
                    await human_delay(500, 1200)

                    # Look over what was typed before firing the search.
                    #await self._pause(state, 0.8, 2.0, tier=Tier.NAV)
                    await self._press("Enter")
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=30000)
                    await self._neutralize_pagination_input()
                    break
                except Exception:
                    if attempt == 2:
                        logger.exception("[HW] Search failed after 3 attempts")
                        self._plog("search failed after 3 attempts -> aborting")
                        await self._emit(state, stage="Failed", status="error", error="We encountered an issue applying your search filters.", error_code="SEARCH_FILTERS_FAILED")
                        return {"error": "Failed to search HelloWork.", "error_code": "SEARCH_FILTERS_FAILED"}
                    self._plog(f"search attempt {attempt + 1} failed -> retrying")
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()

            if contract_types or min_salary > 0:
                await self._apply_filters(contract_types, min_salary)

            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                logger.info("[HW] Search results loaded")
                self._plog("search results loaded")
                # Skim the result list before opening anything — the last beat
                # before the scrape loop takes over.
                await self._arrival_browse(state, "results page")
            except Exception:
                self._plog("no results found for this search")
                return {"error": "No new matching jobs were found for this search today.", "error_code": "NO_JOBS_FOUND"}

            return {}
        except Exception:
            logger.exception("[HW] Search error")
            self._plog("search filters failed -> layout may have changed")
            await self._emit(state, stage="Failed", status="error", error="We encountered an issue applying your search filters.", error_code="SEARCH_FILTERS_FAILED")
            return {"error": "Failed to search HelloWork.", "error_code": "SEARCH_FILTERS_FAILED"}

    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data", stage_code=StageCode.EXTRACTING_DATA, progress_percent=self._progress(state, "scrape"))
        await self._beat(state)
        logger.info("[HW] Scraping jobs")

        user_id = state["user"].id
        self._uid = str(user_id)
        search_id = state["job_search"].id
        found_job_entities = []

        worker_job_limit = min(state.get("worker_job_limit", 10), 12)
        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=14)
        ignored_hashes = hash_result.value if hash_result.is_success else set()

        logger.info("[HW] Target: %s jobs. Ignored hashes: %s", worker_job_limit, len(ignored_hashes))
        self._plog(f"NODE get_matched_jobs -> scraping starts (target: {worker_job_limit} jobs, {len(ignored_hashes)} ignored hashes)")

        page_number = 1
        max_pages = 20

        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:

                # 🚨 INJECTED KILL CHECK: Before processing a new page
                await self._beat(state)
                if await self._is_killed(state):
                    logger.info("[HW] Kill switch detected. Halting pagination.")
                    self._plog(f"kill switch detected mid-scrape -> returning {len(found_job_entities)} offers found so far")
                    return {"error": "Agent has been stopped.", "error_code": "AGENT_STOPPED", "found_raw_offers": found_job_entities}

                logger.info("[HW] Processing page %s", page_number)
                # HelloWork previously went straight from page load to clicking a
                # card. Scan the list first, the way someone choosing between
                # results does.
                if self.page:
                    await human_scan_list(
                        self.page,
                        self._scaled(2.5, Tier.SCROLL),
                        self._scaled(6.0, Tier.SCROLL),
                        pace=self._pace,
                    )
                    await self._distracted_stop(state)
                self._plog(f"processing results page {page_number}")

                cards_locator = self.page.locator(self.CARD_SELECTOR)
                try:
                    await cards_locator.first.wait_for(state='visible', timeout=30000)
                except Exception:
                    if page_number == 1:
                        logger.info("[HW] No results found on page 1")
                        self._plog("no job cards visible on page 1 -> nothing to scrape")
                        return {"found_raw_offers": []}
                    self._plog(f"no job cards visible on page {page_number} -> stopping")
                    break

                count = await cards_locator.count()
                self._plog(f"found {count} cards on page {page_number}")
                search_url = self.page.url

                for i in range(count):

                    # 🚨 INJECTED KILL CHECK: Before clicking a new card
                    await self._beat(state)
                    if await self._is_killed(state):
                        logger.info("[HW] Kill switch detected. Halting card processing.")
                        self._plog(f"kill switch detected mid-page -> returning {len(found_job_entities)} offers found so far")
                        return {"error": "Agent has been stopped.", "error_code": "AGENT_STOPPED", "found_raw_offers": found_job_entities}

                    if len(found_job_entities) >= worker_job_limit:
                        break

                    try:
                        card = self.page.locator(self.CARD_SELECTOR).nth(i)

                        await self._scroll_to(card)
                        await human_delay(400, 1000)

                        raw_company, raw_title, raw_location = await self.get_raw_job_data(card)

                        if not raw_title:
                            self._plog(f"card {i + 1}/{count}: no title extracted -> skipping")
                            continue

                        self._plog(f"card {i + 1}/{count}: '{raw_title}' @ {raw_company or 'No Name'}")

                        fast_hash = self._generate_fast_hash(raw_company, raw_title, str(user_id))
                        if fast_hash in ignored_hashes:
                            self._plog(f"card {i + 1}/{count}: already seen in last 14 days -> skipping")
                            if not await self._nav_back_to_search(search_url):
                                break
                            continue

                        click_success = False
                        for attempt in range(3):
                            try:
                                card = self.page.locator(self.CARD_SELECTOR).nth(i)
                                link = card.locator('a[data-cy="offerTitle"]')
                                await self._scroll_to(link)
                                await human_delay(300, 800)
                                # DEBUG (stray-window investigation): a plain Playwright
                                # click, NOT human_click. The wheel scroll above is kept
                                # so the ONLY variable changed is the click itself.
                                # Revert to human_click once the cause is known.
                                await link.click()
                                await self.page.wait_for_load_state("domcontentloaded")
                                await self.page.wait_for_selector('div[id="content"]', state="visible", timeout=15000)
                                click_success = True
                                break
                            except Exception:
                                if attempt == 2:
                                    logger.warning("[HW] Card click failed after 3 attempts. Skipping.")
                                    self._plog(f"card {i + 1}/{count}: click failed 3 times -> skipping")
                                    break
                                await asyncio.sleep(2 ** attempt)

                        if not click_success:
                            continue

                        # URL sanity check — confirm we landed on a job detail page
                        current_url = self.page.url
                        if "/fr-fr/emplois/" not in current_url:
                            logger.warning("[HW] Unexpected URL after card click: %s", current_url)
                            self._plog(f"unexpected URL after card click ({current_url}) -> going back")
                            if not await self._nav_back_to_search(search_url):
                                break
                            continue

                        self._plog(f"opened offer detail page for '{raw_title}'")
                        await self._pause(state, 1.0, 2.5, tier=Tier.CARD)

                        try:
                            desc_el = self.page.locator('div[id="content"]')
                            if await desc_el.count() == 0:
                                desc_el = self.page.locator('div[data-id-storage-local-storage-key-param="visited-offers"]')
                            job_desc = await desc_el.inner_text()
                        except Exception:
                            job_desc = ""

                        # Read the offer in proportion to its length. HelloWork had
                        # no reading behaviour whatsoever — it opened a description
                        # and clicked straight through.
                        # desc_len = len(job_desc) if job_desc else 0
                        # read_min, read_max = self._read_bounds(desc_len)
                        # self._plog(f"reading offer (~{desc_len} chars) for {read_min:.0f}-{read_max:.0f}s")
                        # await human_read_page(
                        #     self.page,
                        #     min_seconds=read_min,
                        #     max_seconds=max(read_min, read_max),
                        #     pace=self._pace,
                        # )

                        moving_to_form_btn = self.page.locator('a[data-cy="applyButtonHeader"]').first
                        if await moving_to_form_btn.count() > 0:
                            self._plog("clicking apply button to probe form type")
                            click_ok = False
                            for attempt in range(3):
                                try:
                                    await moving_to_form_btn.click()
                                    await self.page.wait_for_load_state("domcontentloaded")
                                    click_ok = True
                                    break
                                except Exception:
                                    if attempt == 2:
                                        logger.warning("[HW] Apply button click failed after 3 attempts")
                                        self._plog("apply button click failed after 3 attempts -> skipping offer")
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
                                    self._plog("external application form detected -> skipping offer")
                                except Exception:
                                    self._plog(f"internal form confirmed at {self.page.url}")
                                    offer = JobOffer(
                                        url=current_url,
                                        form_url= self.page.url,
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
                                    # Progress advances on KEEPERS only. The loop is already
                                    # keeper-driven (while len(found) < worker_job_limit), so the
                                    # denominator is known before it starts.
                                    await self._emit(
                                        state,
                                        f"Found: {offer.job_title}",
                                        stage_code=StageCode.EXTRACTING_DATA,
                                        count_band="scrape",
                                    count_done=len(found_job_entities),
                                    count_total=state.get("max_jobs") or worker_job_limit,
                                    )
                                    self._plog(f"offer captured ({len(found_job_entities)}/{worker_job_limit}): '{raw_title}' @ {raw_company or 'No Name'}")
                        else:
                            self._plog("no apply button on this offer -> skipping")

                        if not await self._nav_back_to_search(search_url):
                            break

                        # Breathe between cards — back-to-back offers at a fixed
                        # interval is the clearest pattern in request timing.
                        await self._pause(state, 6.0, 30.0, tier=Tier.CARD)
                        await self._distracted_stop(state)
                        await self._maybe_break(state, f"after card {len(found_job_entities)}")

                    except Exception:
                        logger.exception("[HW] Error processing card %s on page %s", i, page_number)
                        self._plog(f"error processing card {i + 1} on page {page_number} -> going back to results")
                        if not await self._nav_back_to_search(search_url):
                            break
                        continue

                if len(found_job_entities) >= worker_job_limit:
                    self._plog(f"job limit reached ({worker_job_limit}) -> stopping scrape")
                    break

                if not await self._handle_hw_pagination(page_number):
                    break
                await self._maybe_break(state, f"between pages {page_number}")
                page_number += 1

        except Exception:
            logger.exception("[HW] Fatal scraping error")
            self._plog("critical scraping error -> halting process")
            await self._emit(state, stage="Failed", status="error", error="A critical error occurred while scanning the job listings.", error_code="SCRAPING_FAILED")
            return {"error": "A critical error occurred while scanning the job listings. We have safely halted the process.", "error_code": "SCRAPING_FAILED"}

        if not found_job_entities:
            self._plog("scraping finished with 0 new offers")
            return {"found_raw_offers": []}

        logger.info("[HW] Scraping complete. Returning %s jobs.", len(found_job_entities))
        self._plog(f"scraping complete -> returning {len(found_job_entities)} new offers")
        return {"found_raw_offers": found_job_entities}

    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state, "Submitting Applications", stage_code=StageCode.SUBMITTING, progress_percent=self._progress(state, "submit"))
        await self._beat(state)
        logger.info("[HW] Submitting applications")
        jobs_to_submit = state.get("processed_offers", [])
        user = state["user"]
        self._uid = str(user.id)

        assigned_submit_limit = state.get("worker_job_limit", 5)

        hw_jobs = [job for job in jobs_to_submit if job.job_board == JobBoard.HELLOWORK and job.status == ApplicationStatus.APPROVED]

        self._plog(f"NODE submit_applications -> {len(hw_jobs)} approved HW offers in queue (limit: {assigned_submit_limit})")

        if not hw_jobs:
            logger.info("[HW] No approved HW jobs in submission queue")
            self._plog("nothing to submit -> exiting node")
            return {"status": "no_hw_jobs_to_submit"}

        successful_submissions = []
        i = 0
        for offer in hw_jobs:

            # 🚨 INJECTED KILL CHECK: Before starting the next submission
            await self._beat(state)
            if await self._is_killed(state):
                logger.info("[HW] Kill switch detected. Halting submissions.")
                self._plog(f"kill switch detected mid-submission -> returning {len(successful_submissions)} submitted so far")
                return {"error": "Agent has been stopped.", "error_code": "AGENT_STOPPED", "submitted_offers": successful_submissions}

            if len(successful_submissions) >= assigned_submit_limit:
                logger.info("[HW] Reached assigned submission limit (%s)", assigned_submit_limit)
                self._plog(f"submission limit reached ({assigned_submit_limit}) -> stopping")
                break

            self._plog(f"processing application {i + 1}/{len(hw_jobs)}: '{offer.job_title}' @ {offer.company_name}")

            try:
                form_loaded = False
                self._plog("loading application form")
                for attempt in range(3):
                    try:
                        await self.page.goto(offer.form_url, wait_until="commit", timeout=60000)
                        self._plog(f"navigated to form URL: {offer.form_url}")
                        await human_delay(1500, 3500)

                        await self.page.wait_for_selector('input[name="Firstname"]', state="visible", timeout=90000)
                        form_loaded = True
                        break
                    except Exception:
                        if attempt == 2:
                            logger.warning("[HW] Form failed to load after 3 attempts. Skipping.")
                            self._plog("form failed to load after 3 attempts -> skipping offer")
                            break
                        self._plog(f"form load attempt {attempt + 1} failed -> retrying")
                        await asyncio.sleep(2 ** attempt)

                if not form_loaded:
                    i += 1
                    continue

                self._plog("application form loaded -> filling identity fields")
                await human_delay(400, 1000)
                await human_type(self.page.locator('input[name="Firstname"]'), user.firstname)
                await human_delay(300, 700)
                await human_type(self.page.locator('input[name="LastName"]'), user.lastname)

                if user.resume_path:
                    self._plog("downloading resume from storage")
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"
                    self._plog(f"uploading resume: {human_name}")
                    await self._upload(
                        self.page.locator('[data-cy="cv-uploader-input"]'),
                        {
                            "name": human_name,
                            "mimeType": "application/pdf",
                            "buffer": resume_bytes,
                        },
                    )
                    await human_delay(1000, 2000)

                if offer.cover_letter:
                    self._plog("filling cover letter")
                    await human_click(self.page.locator('[data-cy="motivationFieldButton"]'))
                    await self.page.wait_for_selector('textarea[name="MotivationLetter"]', state="visible", timeout=10000)
                    await self._type(self.page.locator('textarea[name="MotivationLetter"]'), offer.cover_letter)

                submit_btn = self.page.locator('[data-cy="submitButton"]')
                if await submit_btn.is_visible():
                    self._plog("clicking submit button (no retry: duplicate risk)")
                    # No stray-window retry: a repeated submit is an application
                    # sent twice. The window is still closed, just not re-clicked.
                    await self._click(submit_btn, retry_on_popup=False)

                    try:
                        notification = self.page.locator('[data-intersect-name-value="notification"]')
                        await notification.wait_for(state="attached", timeout=45000)

                        if await notification.count() > 0:
                            use_tag = notification.locator('svg use[href*="badges"]')
                            if await use_tag.count() > 0:
                                href_value = await use_tag.get_attribute("href")
                                if href_value and "error" in href_value.lower():
                                    logger.warning("[HW] Submission blocked by error badge")
                                    self._plog("submission BLOCKED -> error badge in notification")
                                    i += 1
                                    continue

                    except Exception:
                        logger.info("[HW] No notification appeared — assuming success")
                        self._plog("no notification appeared -> assuming success")
                        i += 1

                    logger.info("[HW] Application submitted")
                    self._plog(f"application SUBMITTED: '{offer.job_title}' @ {offer.company_name} ({len(successful_submissions) + 1}/{assigned_submit_limit})")
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                    await self._emit(
                        state,
                        f"Submitted: {offer.job_title}",
                        stage_code=StageCode.SUBMITTING,
                        count_band="submit",
                                    count_done=len(successful_submissions),
                                    count_total=len([j for j in jobs_to_submit
                                                     if j.status == ApplicationStatus.APPROVED]),
                    )
                else:
                    logger.warning("[HW] Submit button not visible")
                    self._plog("submit button not visible -> skipping offer")

            except Exception:
                logger.exception("[HW] Submission failed for %s", offer.url)
                self._plog(f"submission crashed for '{offer.job_title}' -> moving to next offer")

            # Nobody fires off applications back to back.
            # Shorter than it was, and now the ONLY thing pacing this
            # loop: the review read that used to precede the send is gone, so
            # without this the node is fill, submit, next form, at whatever
            # speed the forms load.
            await self._pause(state, 15.0, 25.0, tier=Tier.SUBMIT)
            await self._maybe_break(state, "between submissions")
            i += 1

        if not successful_submissions:
            self._plog("all submission attempts failed")
            await self._emit(state, stage="Failed", status="error", error="All application attempts failed.", error_code="SUBMISSION_FAILED")
            return {"error": "All application attempts failed. The job board may have updated its application form structure.", "error_code": "SUBMISSION_FAILED"}

        self._plog(f"submission node done -> {len(successful_submissions)} applications submitted")
        return {"submitted_offers": successful_submissions}

    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up", stage_code=StageCode.CLEANING_UP, progress_percent=self._progress(state, "cleanup"))
        self._plog("NODE cleanup -> closing browser session")
        await self.force_cleanup()

        # C-2: the durable copy lives in GCS, so drop the local session file (it holds
        # auth cookies). Only when a session_store is wired — in local-only mode the
        # local file IS the persistence and must survive between runs.
        if self.session_store:
            self.session_store.cleanup_local(self._get_session_file_path(str(state["user"].id)))

        # 🚨 Fail-soft cleanup: if the circuit breaker was tripped, scrub the error
        # from state so the master can keep partial worker results from the happy path.
        if state.get("error"):
            self._plog(f"fail-soft: scrubbing error from state ({state['error']})")
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