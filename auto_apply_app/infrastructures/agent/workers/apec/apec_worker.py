# auto_apply_app/infrastructures/agent/workers/apec/apec_worker.py
import asyncio
import hashlib
import logging
import os
import random
import time
from datetime import datetime
from typing import Optional
from langgraph.graph import StateGraph, END
from playwright_stealth import Stealth
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright, Locator
import pdfplumber


# --- DOMAIN IMPORTS ---
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import ContractType, JobBoard, ApplicationStatus

# --- INFRA & APP IMPORTS ---
from auto_apply_app.application.use_cases.agent_state_use_cases import (
    IsAgentKilledForSearchUseCase,
    HeartbeatAgentForSearchUseCase,
)
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.infrastructures.agent.pacing import HumanPacing, Tier
from auto_apply_app.infrastructures.agent.fingerprint_alignment import (
    align_fingerprint_to_browser,
)
from auto_apply_app.infrastructures.agent.stage_codes import StageCode
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
    human_hover,
    human_warmup,
    human_scroll,
    human_read_page,
    human_idle_drift,
    human_scan_list,
    human_long_pause,
    should_take_break,
    plan_card_visit_order,
    should_hover_without_clicking,
    reset_mouse_state,
)

# Pacing now lives in agent/pacing.py, shared with WTTJ and HelloWork.
# APEC_HUMAN_PACE / APEC_HUMAN_BUDGET_S still override the global
# AGENT_HUMAN_PACE / AGENT_HUMAN_BUDGET_S for this board alone.

logger = logging.getLogger(__name__)


def _chromium_launch_kwargs(headless: bool) -> dict:
    """Launch options for APEC's browser.

    APEC only, for now: APEC_BROWSER_CHANNEL swaps Playwright's bundled
    Chromium for a real installed browser ("chrome" / "msedge"). A stock Chrome
    build carries things the open-source Chromium bundle simply does not —
    proprietary codecs, real branding, the widevine module — and those are
    cheap for a board to check.

    Unset by default, and it has to stay that way for deploys: the worker image
    (Dockerfile.worker) installs Chromium only, so a channel that isn't present
    fails at launch. Set it in a local .env to test, not in the Cloud Run env.
    """
    kwargs = {
        "headless": headless,
        "args": ['--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage'],
    }
    channel = os.getenv("APEC_BROWSER_CHANNEL", "").strip()
    if channel:
        kwargs["channel"] = channel
    return kwargs


class ApecWorker(HumanPacing):

    # Reading band for this board: 50-150s.
    _READ_BAND_S = (50.0, 150.0)
    _READ_LONG_CHARS = 7200

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
        heartbeat: HeartbeatAgentForSearchUseCase,
        session_store=None,
    ):
        # Static Dependencies
        self.get_ignored_hashes = get_ignored_hashes
        self.encryption_service = encryption_service
        self.base_url = "https://www.apec.fr/"
        self.file_storage = file_storage
        self.is_agent_killed_for_search = is_agent_killed_for_search
        self.heartbeat = heartbeat
        # C-2: durable GCS session cache. None -> local-file only (pre-C-2 behavior).
        self.session_store = session_store

        # Runtime State (Lazy Initialization)
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Progress callback (set per-run by master)
        self._progress_callback = None
        self._source_name = "APEC"
        # Canonical board key. Indexes this worker's slice of the per-board
        # fingerprint and proxy maps the master builds.
        self._board_key = "apec"
        # The persona this run is wearing. Cookie jars are keyed on it, so a
        # jar can never be replayed under a different device.
        self._fingerprint_id: Optional[str] = None

        # Current user id for print logging (set at node entry)
        self._uid = "unknown"

        # Human pacing (shared mixin; APEC_* env vars override the global knobs)
        self._init_pacing("APEC")

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _plog(self, task: str, user_id=None):
        """Strategic print logging: [Worker for user_id] : task"""
        uid = user_id if user_id is not None else self._uid        
        print(f"[{self._source_name} for {uid}] : {task}", flush=True)

    

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

    async def route_node_exit(self, state: JobApplicationState) -> str:
        if state.get("error"):
            logger.warning("[APEC] Circuit breaker tripped: %s", state["error"])
            self._plog(f"circuit breaker tripped -> routing to cleanup ({state['error']})")
            return "error"

        user_id = state["user"].id
        search_id = state["job_search"].id

        killed_result = await self.is_agent_killed_for_search.execute(user_id, search_id)
        if killed_result.is_success and killed_result.value:
            logger.info("[APEC] Kill switch detected for search %s. Aborting gracefully.", search_id)
            self._plog(f"kill switch detected for search {search_id} -> aborting gracefully")
            return "error"

        return "continue"

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
            logger.exception("[APEC] Progress emit failed")

    def _generate_fast_hash(self, company_name: str, job_title: str, user_id: str) -> str:
        c = str(company_name).replace(" ", "").lower().strip()
        t = str(job_title).replace(" ", "").lower().strip()
        u = str(user_id).strip()
        b = "apec"
        raw_string = f"{c}_{t}_{b}_{u}"
        return hashlib.md5(raw_string.encode()).hexdigest()

    def _get_session_file_path(self, user_id: str) -> str:
        """Local path for this run's cookie jar.

        Scoped by persona: the jar belongs to the device that acquired it, so
        rotating to another device cannot pick up the previous one's cookies.
        Falls back to "nofp" only when no fingerprint resolved at all.
        """
        directory = os.path.join(os.getcwd(), "tmp", "sessions")
        os.makedirs(directory, exist_ok=True)
        fp = self._fingerprint_id or "nofp"
        return os.path.join(directory, f"{user_id}_apec_{fp}_session.json")

    async def _save_auth_state(self, user_id: str):
        if self.context:
            path = self._get_session_file_path(user_id)
            await self.context.storage_state(path=path)
            logger.info("[APEC] Session saved for user %s", user_id)
            self._plog("session cookies saved to disk", user_id)
            # C-2: mirror the refreshed session to GCS (best-effort, never fatal).
            if self.session_store:
                await self.session_store.save_from_local(user_id, "apec", self._fingerprint_id or "nofp", path)

    def _get_auth_state_path(self, user_id: str) -> str | None:
        path = self._get_session_file_path(user_id)
        if os.path.exists(path):
            return path
        return None

    async def _handle_cookies(self):
        try:
            await self.page.wait_for_selector('button:has-text("Refuser tous les cookies")', state='attached', timeout=15000)
            cookie_btn = self.page.locator('button:has-text("Refuser tous les cookies")')
            if await cookie_btn.count() > 0:
                self._plog("cookie banner detected -> refusing all cookies")
                await human_delay(300, 800)
                #await human_click(cookie_btn)
                await cookie_btn.click()
        except Exception:
            logger.debug("[APEC] No cookies popup")

    async def force_cleanup(self):
        logger.info("[APEC] Force cleanup initiated")
        self._plog("force cleanup initiated")

        # Before the page goes: the input backend is registered against it, and
        # the Xvfb it may be driving outlives the browser unless stopped here.
        await self._close_display()

        try:
            if self.page:
                reset_mouse_state(self.page)
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
        self._plog("force cleanup complete -> browser fully closed")

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
            self._plog("card content never became ready -> returning empty data")
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
    # Back-button anchor on the interstitial APEC lands on after a browser Back
    # from an offer. It is not the results page; it is the page that links to it.
    RESULTS_BACK_LINK = 'a[class="backButton d-flex"]'

    async def nav_back(self, at_redir_page: bool = False, has_back_button: bool = False):
        """Return to the results list.

        Two independent steps, because APEC puts up to two pages between an offer
        and the list it came from.

        `at_redir_page` says the current page is a redirect interstitial rather
        than the offer, so one step back is spent leaving it. If the back link is
        still not visible afterwards, a second step back is taken — the depth of
        that stack varies with how the offer was opened.

        `has_back_button` says the page we land on carries
        `a[class="backButton d-flex"]`, which is what actually returns to the
        results list; it is clicked through the OS-level pointer like any other
        element.

        Both default to False, and the card check at the end is the arbiter
        either way — it is what decides whether the results are really back.
        """
        if at_redir_page:
            self._plog("navigating back to results page (browser back)")
            await self.page.go_back(wait_until="networkidle")
            await asyncio.sleep(random.uniform(3.0, 5.0))  # settle after the Back
            try:
                self._plog("Checking if back button is visible")
                back_link = self.page.locator(self.RESULTS_BACK_LINK)
                await back_link.wait_for(state="visible", timeout=10000)
            except Exception:
                self._plog("Back button not found after browser back -> getting one page back in history") 
                await self.page.go_back(wait_until="networkidle")   


        if has_back_button:
            # The Back button lands on the interstitial, not the results. The
            # link out of it is what actually returns to the list.
            try:
                back_link = self.page.locator(self.RESULTS_BACK_LINK)
                await back_link.wait_for(state="visible", timeout=35000)
                self._plog("following the back link to the results list")
                await self._click(back_link)
                await self.page.wait_for_load_state("networkidle")
            except Exception:
                # No interstitial this time — either Back already landed on the
                # results, or the page changed shape. The card check below is the
                # real arbiter either way, and it has its own recovery.
                self._plog("no back link found -> checking whether the results are already up")
                logger.info("[APEC] No '%s' after browser back", self.RESULTS_BACK_LINK)

       

        try:
            await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=45000)
            await human_delay(1000, 2500)
            await self._handle_cookies()
        except Exception:
            logger.warning("[APEC] Cards didn't reappear after going back. Waiting again...")
            self._plog("cards missing after nav back -> waiting once more")
            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
            except Exception:
                logger.warning("[APEC] Cards still not visible. Page state may be broken.")
                self._plog("cards still missing -> page state may be broken")
            await self._handle_cookies()

    # --- HELPER: Handle Pagination ---
    async def _handle_apec_pagination(self, page_number: int) -> bool:
        try:
            next_button = self.page.locator(
                'nav[aria-label="Page navigation"] li[class="page-item"]'
            )

            if await next_button.count() == 0:
                logger.info("[APEC] No next button found. Reached last page.")
                self._plog(f"no next button on page {page_number} -> reached last page")
                return False

            butt_child = next_button.locator("a")
            if await butt_child.count() == 0:
                logger.info("[APEC] Next button inactive. Reached last page.")
                self._plog(f"next button inactive on page {page_number} -> reached last page")
                return False

            logger.info("[APEC] Moving to page %s...", page_number + 1)
            self._plog(f"pagination -> moving to page {page_number + 1}")
            await human_delay(2500, 5500)

            for attempt in range(3):
                try:
                    #await human_click(next_button)
                    await next_button.click()
                    await self.page.wait_for_load_state("domcontentloaded")
                    await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=10000)
                    # Settle on the new page before scanning it.
                    await human_idle_drift(self.page)
                    break
                except Exception as e:
                    detail = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                    if attempt == 2:
                        logger.exception("[APEC] Pagination failed after 3 attempts")
                        self._plog(f"pagination click failed after 3 attempts -> stopping pagination ({detail})")
                        return False
                    self._plog(f"pagination click attempt {attempt + 1} failed -> retrying ({detail})")
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            return True

        except Exception:
            logger.exception("[APEC] Pagination error")
            self._plog("unexpected pagination error -> stopping pagination")
            return False

    # --- HELPER: Apply APEC Advanced Filters ---
    async def _apply_filters(self, state: JobApplicationState, job_title: str, contract_types: list[ContractType], min_salary: int):
        logger.info("[APEC] Applying advanced filters")
        self._plog(f"opening advanced search to filter on '{job_title}'")
        try:
            for attempt in range(3):
                try:
                    await self.page.wait_for_selector('a[id="advancedSearch"]', state="visible", timeout=45000)
                    break
                except Exception:
                    if attempt == 2:
                        logger.warning("[APEC] advancedSearch not found after 3 attempts. Direct nav...")
                        self._plog("advanced search link not found -> falling back to direct URL")
                        await self.page.goto(self.ADVANCED_SEARCH_URL, wait_until="networkidle", timeout=60000)
                    else:
                        await asyncio.sleep(2 ** attempt)

            #await human_click(self.page.locator('a[id="advancedSearch"]'))
            await self.page.locator('a[id="advancedSearch"]').click()
            await self.page.wait_for_load_state("networkidle", timeout=90000)

            await self.page.wait_for_selector('input[id="keywords"]', state="visible", timeout=15000)
            self._plog(f"typing keywords: '{job_title}'")
            await self._micro()
            await human_type(self.page.locator('input[id="keywords"]'), job_title)

            # A person glances down the rest of the form before filling it in.
            await human_delay(600, 1400)
            await human_scroll(self.page, distance=random.randint(150, 350))
            await self._pause(state, 0.8, 2.2, tier=Tier.SCROLL)

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
                        self._plog("angular search form never rendered -> aborting filters")
                        raise
                    self._plog(f"angular form not rendered (attempt {attempt + 1}) -> reloading")
                    await self.page.reload(wait_until="networkidle")
                    await asyncio.sleep(2 ** attempt)

            if contract_types:
                for contract in contract_types:
                    val = contract_map.get(str(contract.value), None)
                    if val:
                        self._plog(f"selecting contract type: {contract.value}")
                        await human_delay(700, 1600)
                        await self._select(
                            self.page.locator('select[formcontrolname="typesContrat"]'), val
                        )
                        break

            if min_salary > 0:
                salary_input = self.page.locator('apec-slider input.pull-left')
                await self._scroll_to(salary_input)
                if await salary_input.count() > 0:
                    salary_k = str(min_salary // 1000) if min_salary >= 1000 else str(min_salary)
                    self._plog(f"setting minimum salary: {salary_k}k")
                    await human_delay(700, 1600)
                    await human_type(salary_input, salary_k)

            # Look over the filters before firing the search.
            await self._pause(state, 2.0, 5.0, tier=Tier.NAV)

            # Submit from the keyboard, the way someone who just typed the
            # keywords would: focus the field and hit Enter. Reaching across the
            # page to click the button is the rarer behaviour of the two, and it
            # is a mouse trajectory we would otherwise have to justify.
            # The button stays as the fallback — Enter is not guaranteed to
            # submit this Angular form, and a search that never fires is worse
            # than a click.
            submitted = False
            try:
                self._plog("submitting search with Enter from the keywords field")
                keywords = self.page.locator('input[id="keywords"]')
                await self._click(keywords, hesitation=False)
                await self._micro()
                await self._press("Enter", locator=keywords)
                await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
                submitted = True
            except Exception:
                self._plog("Enter did not submit the form -> falling back to the RECHERCHER button")

            if not submitted:
                for attempt in range(3):
                    try:
                        # await human_click(self.page.locator('button:has-text("RECHERCHER")'))
                        await self.page.locator('button:has-text("RECHERCHER")').click()
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(2 ** attempt)

            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_selector(self.CARD_SELECTOR, state="visible", timeout=15000)
            self._plog("filters applied -> results page loaded")
            await self._settle(state, "results loaded")

        except Exception:
            logger.exception("[APEC] Error applying filters")
            self._plog("error while applying filters -> raising")
            raise

    async def _get_job_attribute(self, card: Locator, selector: str, default_value: str = None):
        try:
            content = await card.locator(selector).inner_text()
            return content.strip()
        except Exception:
            return default_value


    async def _is_session_valid(self) -> bool:
        """The logout <li> only renders for an authenticated user.
        Present -> session live. Absent -> expired."""
        try:
            await self.page.wait_for_selector(
                'li[id="header-logout"]', state="attached", timeout=45000
            )
            self._plog("session check: logout button present -> session VALID")
            return True
        except Exception:
            self._plog("session check: no logout button -> session EXPIRED")
            return False

    
    async def _perform_auto_login(self, state: JobApplicationState) -> bool:
        """Full-automation credential login, mirroring request_login's logic.
        Assumes the browser/page are booted and on the APEC homepage.
        Returns True on success and re-saves the session."""
        creds = state.get("credentials")
        user_id = state["user"].id

        if not (creds and creds.get("apec")):
            self._plog("re-login: no stored APEC credentials -> cannot auto-login")
            return False

        login_plain = None
        pass_plain = None
        try:
            login_plain = await self.encryption_service.decrypt(creds["apec"].login_encrypted)
            pass_plain = await self.encryption_service.decrypt(creds["apec"].password_encrypted)
            self._plog("re-login: credentials decrypted")

            # Open login modal
            self._plog("re-login: opening login modal")
            for attempt in range(3):
                try:
                    await self.page.wait_for_selector('li[id="header-monespace"]', state="visible", timeout=30000)
                    # await human_click(self.page.locator('li[id="header-monespace"]'))
                    await self.page.locator('li[id="header-monespace"]').click()
                    await self.page.wait_for_selector('input[id="emailid"]', state="visible", timeout=15000)
                    break
                except Exception:
                    if attempt == 2:
                        self._plog("re-login: login modal never opened")
                        return False
                    await self.page.reload(wait_until="networkidle")
                    await asyncio.sleep(2 ** attempt)

            # Fill credentials + submit
            self._plog("re-login: typing credentials")
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
                    # await human_click(self.page.locator('button[type="submit"][value="Login"]').first)
                    await self.page.locator('button[type="submit"][value="Login"]').first.click()
                    self._plog("re-login: credentials submitted")
                    break
                except Exception:
                    if attempt == 2:
                        self._plog("re-login: credential submission failed")
                        return False
                    await asyncio.sleep(2 ** attempt)

            # Proof of login
            self._plog("re-login: waiting for candidat URL confirmation")
            for attempt in range(3):
                try:
                    await self.page.wait_for_url("**/candidat**", timeout=30000)
                    await self.page.goto(f"{self.base_url}candidat.html", wait_until="networkidle", timeout=90000)
                    break
                except Exception:
                    if attempt == 2:
                        self._plog("re-login: confirmation never received -> bad credentials?")
                        return False
                    await asyncio.sleep(2 ** attempt)

            await self._save_auth_state(str(user_id))
            self._plog("re-login successful -> session re-saved")
            return True

        except Exception:
            logger.exception("[APEC] Re-login failed")
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

    # --- NODE 1: Start Session (SCRAPE track) ---
    async def start_session(self, state: JobApplicationState):
        await self._emit(state, "Initializing Browser", stage_code=StageCode.INITIALIZING_BROWSER, progress_percent=self._progress(state, "start"))
        await self._beat(state)
        logger.info("[APEC] Starting session")
        self._uid = str(state["user"].id)
        self._start_pacing(state.get("run_token"))
        self._plog("NODE start_session -> launching stealth browser (SCRAPE track)")
        self._plog(f"browser: {os.getenv('APEC_BROWSER_CHANNEL') or 'bundled chromium'}")
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
                    _chromium_launch_kwargs(preferences.run_browser_headless), fingerprint
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
            # No other context.route on this worker, so registration order is
            # unconstrained here -- unlike HelloWork, see the note there.
            await self._conserve_bandwidth(self.context)

            # await self.context.add_init_script("""
            #     // Override webdriver flag
            #     Object.defineProperty(navigator, 'webdriver', {
            #         get: () => undefined,
            #     });

            #     // Fake plugins
            #     Object.defineProperty(navigator, 'plugins', {
            #         get: () => [1, 2, 3, 4, 5],
            #     });

            #     // Fake chrome.runtime
            #     if (!window.chrome) {
            #         window.chrome = {};
            #     }

            #     if (!window.chrome.runtime) {
            #         window.chrome.runtime = {};
            #     }
            # """)

            # Stealth FIRST, fingerprint SECOND. playwright_stealth registers its
            # own WebGL getParameter patch hardcoded to "Intel Inc." / "Intel Iris
            # OpenGL Engine"; init scripts run in registration order, so applying
            # it after ours silently replaced the persona's GPU on every run.
            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)

            if fingerprint:
                await self.context.add_init_script(fingerprint.to_init_script())
            self.page = await self.context.new_page()
            await self._mount_input()

            self._plog("browser session ready")
            return {}
        except Exception:
            logger.exception("[APEC] Session error")
            self._plog("browser session failed to start")
            return {"error": "Failed to start the secure browsing session. Our servers might be under heavy load, please try again."}

    async def start_session_with_auth(self, state: JobApplicationState):
        await self._emit(state, "Initializing Secure Browser", stage_code=StageCode.INITIALIZING_BROWSER, progress_percent=self._progress(state, "start_with_session"))
        await self._beat(state)
        logger.info("[APEC] Booting browser (session injection)")
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
                    _chromium_launch_kwargs(state["preferences"].run_browser_headless),
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
                    user_id, "apec", self._fingerprint_id or "nofp",
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
                logger.info("[APEC] Found saved session for user. Injecting cookies.")
                self._plog("saved session found -> injecting cookies")
                context_kwargs["storage_state"] = session_path
            else:
                logger.info("[APEC] No session found. Booting fresh context.")
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
            # No other context.route on this worker, so registration order is
            # unconstrained here -- unlike HelloWork, see the note there.
            await self._conserve_bandwidth(self.context)

            # Stealth FIRST, fingerprint SECOND. playwright_stealth registers its
            # own WebGL getParameter patch hardcoded to "Intel Inc." / "Intel Iris
            # OpenGL Engine"; init scripts run in registration order, so applying
            # it after ours silently replaced the persona's GPU on every run.
            stealth = Stealth()
            await stealth.apply_stealth_async(self.context)

            if fingerprint:
                await self.context.add_init_script(fingerprint.to_init_script())
            self.page = await self.context.new_page()
            await self._mount_input()

            self._plog("navigating to apec.fr homepage")
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=90000)
                    await self.page.wait_for_selector('div[class="card-title"] h2:has-text("Je suis candidat")', state="visible", timeout=45000)
                    break
                except Exception as e:
                    detail = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                    if attempt == 2:
                        logger.exception("[APEC] Auth boot failed after 3 attempts")
                        self._plog(f"homepage unreachable after 3 attempts -> aborting ({detail})")
                        return {"error": "Failed to reach APEC after multiple attempts."}
                    self._plog(f"homepage load attempt {attempt + 1} failed -> retrying ({detail})")
                    await asyncio.sleep(2 ** attempt)

            await self._handle_cookies()
            await human_warmup(self.page, self.base_url)

            # --- SESSION VALIDITY GATE ---
            if await self._is_session_valid():
                # Valid session: enter the candidate search area via the homepage card.
                self._plog("session valid -> clicking 'Je suis candidat'")
                # await human_click(self.page.locator('div[class="card-title"] h2:has-text("Je suis candidat")'))
                await self.page.locator('div[class="card-title"] h2:has-text("Je suis candidat")').click()
            else:
                # Expired session: re-authenticate using request_login's full-auto logic.
                self._plog("session expired -> re-authenticating via auto-login")
                await self.page.goto(self.base_url, wait_until="networkidle", timeout=90000)
                if not await self._perform_auto_login(state):
                    self._plog("auto-login fallback failed -> aborting SUBMIT track")
                    try:
                        os.remove(self._get_session_file_path(user_id))
                    except OSError:
                        pass
                    return {"error": "Your APEC session has expired and we couldn't reconnect automatically. Please check your APEC credentials in your settings."}
                # _perform_auto_login lands on candidat.html already logged in;
                # NO "Je suis candidat" click needed here.

            # --- BOTH PATHS CONVERGE: apply filters + load results ---
            search_entity = state["job_search"]
            job_title = search_entity.job_title
            contract_types = getattr(search_entity, 'contract_types', [])
            min_salary = getattr(search_entity, 'min_salary', 0)

            try:
                await self._apply_filters(state, job_title, contract_types, min_salary)

                try:
                    await self.page.wait_for_selector(self.CARD_SELECTOR, timeout=25000)
                    logger.info("[APEC] Search results loaded")
                    self._plog("search results loaded (auth track)")
                except Exception:
                    logger.info("[APEC] No results found after applying filters")
                    self._plog("no results visible after filters (auth track)")

                return {}
            except Exception:
                logger.exception("[APEC] Search error during auth track")
                self._plog("search failed during auth track -> continuing anyway")
                return {}

        except Exception:
            logger.exception("[APEC] Browser auth initialization error")
            self._plog("browser auth initialization failed")
            return {"error": "Failed to initialize browser with session."}

    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        await self._emit(state, "Navigating to Job Board", stage_code=StageCode.NAVIGATING, progress_percent=self._progress(state, "nav"))
        await self._beat(state)
        logger.info("[APEC] Navigating to board")
        self._plog("NODE go_to_job_board -> navigating to apec.fr")
        try:
            for attempt in range(3):
                try:
                    await self.page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                    await self._handle_cookies()
                    await self.page.wait_for_selector('li[id="header-monespace"]', state="visible", timeout=30000)
                    break
                except Exception as e:
                    # The reason, not just the fact. This block covers the goto,
                    # the cookie widget and a selector wait — three unrelated
                    # failures that read identically from outside, while
                    # "unreachable" names only the first. The same shape on WTTJ
                    # cost an afternoon of ruling out the site, the selector and
                    # the proxy, all of which were fine.
                    detail = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                    if attempt == 2:
                        self._plog(f"apec.fr unreachable after 3 attempts -> aborting ({detail})")
                        logger.warning("[APEC] Navigation failed 3x", exc_info=True)
                        return {"error": "Could not reach APEC.fr. The job board might be down or undergoing maintenance."}
                    self._plog(f"navigation attempt {attempt + 1} failed -> retrying ({detail})")
                    await asyncio.sleep(2 ** attempt)

            self._plog("homepage loaded -> performing human warmup")
            await human_warmup(self.page, self.base_url)
            await self._arrival_browse(state, "apec.fr homepage")
            return {}
        except Exception:
            logger.exception("[APEC] Nav error")
            self._plog("unexpected navigation error -> aborting")
            return {"error": "Could not reach APEC.fr. The job board might be down or undergoing maintenance."}

    # --- NODE 3: Login ---
    async def request_login(self, state: JobApplicationState):
        await self._emit(state, "Authenticating", stage_code=StageCode.AUTHENTICATING, progress_percent=self._progress(state, "login"))
        await self._beat(state)

        prefs = state["preferences"]
        creds = state.get("credentials")
        user_id = state["user"].id

        logger.info("[APEC] Login phase")
        self._plog("NODE request_login -> entering login phase")

        if prefs.is_full_automation and creds["apec"]:
            logger.info("[APEC] Full automation: attempting auto-login")
            self._plog("full automation mode -> attempting auto-login")

            login_plain = None
            pass_plain = None

            try:
                login_plain = await self.encryption_service.decrypt(creds["apec"].login_encrypted)
                pass_plain = await self.encryption_service.decrypt(creds["apec"].password_encrypted)
                self._plog("credentials decrypted")

                # RETRY UNIT 1: Open login modal
                self._plog("opening login modal")
                for attempt in range(3):
                    try:
                        await self.page.wait_for_selector('li[id="header-monespace"]', state="visible", timeout=30000)
                        await human_click(self.page.locator('li[id="header-monespace"]'))
                        await self.page.wait_for_selector('input[id="emailid"]', state="visible", timeout=15000)
                        break
                    except Exception as e:
                        detail = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                        if attempt == 2:
                            self._plog(f"login modal never opened -> aborting login ({detail})")
                            logger.warning("[APEC] Login modal failed 3x", exc_info=True)
                            return {"error": "Login failed. Could not open the login modal."}
                        self._plog(f"login modal attempt {attempt + 1} failed -> reloading and retrying ({detail})")
                        await self.page.reload(wait_until="networkidle")
                        await asyncio.sleep(2 ** attempt)

                # Nobody starts typing the instant a modal renders. LOGIN tier:
                # people enter a saved password quickly, so this stays short even
                # at high pace.
                await self._pause(state, 1.5, 4.0, tier=Tier.LOGIN)

                # RETRY UNIT 2: Fill credentials with HUMAN typing
                self._plog("typing credentials")
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
                        await human_click(self.page.locator('button[type="submit"][value="Login"]').first)

                        await self.page.wait_for_url("**/candidat**", timeout=30000) # confirming auth before attepting again!
                        self._plog("credentials submitted")
                        break
                    except Exception as e:
                        # This one had no per-attempt log at all, and the final
                        # message named no cause — so a wrong password, a slow
                        # redirect and a challenge page all aborted the login
                        # identically.
                        detail = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                        if attempt == 2:
                            self._plog(f"credential submission failed after 3 attempts -> aborting login ({detail})")
                            logger.warning("[APEC] Credential submission failed 3x", exc_info=True)
                            return {"error": "Login failed. Could not submit credentials."}
                        self._plog(f"credential submission attempt {attempt + 1} failed -> retrying ({detail})")
                        await asyncio.sleep(2 ** attempt)

                # RETRY UNIT 3: Proof of login
                self._plog("waiting for login confirmation (candidat URL)")
                for attempt in range(3):
                    try:
                        await self.page.wait_for_url("**/candidat**", timeout=30000)
                        await self.page.goto(f"{self.base_url}candidat.html", wait_until="networkidle", timeout=90000)
                        break
                    except Exception:
                        if attempt == 2:
                            self._plog("login confirmation never received -> bad credentials?")
                            return {"error": "Login failed. Please check your APEC credentials in your settings."}
                        await asyncio.sleep(2 ** attempt)

                logger.info("[APEC] Auto-login successful")
                self._plog("auto-login successful")
                await self._save_auth_state(str(user_id))
                await self._settle(state, "post-login")
                return {}

            except Exception:
                logger.exception("[APEC] Auto-login failed")
                self._plog("auto-login crashed with unexpected error")
                return {"error": "Login failed. Please check your APEC credentials in your settings."}

            finally:
                if login_plain is not None:
                    del login_plain
                if pass_plain is not None:
                    del pass_plain

        else:
            logger.info("[APEC] Semi-automation: requesting user action")
            self._plog("semi-automation mode -> waiting for manual login (90s)")
            try:
                await human_click(self.page.locator('a[aria-label="Mon espace"]'))
                logger.info("[APEC] ACTION REQUIRED: Please log in manually within 60 seconds")
                # Heartbeat-safe: a bare sleep here beats nothing for 90s, which
                # is over half the AGENT_HEARTBEAT_STALE_SECONDS budget.
                await human_long_pause(90, 90, on_tick=lambda: self._beat(state), tick_every=30.0)
                self._plog("manual login window elapsed -> verifying access")
                await human_click(self.page.locator('[aria-label="menu"]'))
                await human_click(self.page.locator('[href="/candidat.html"]'))
                await self._save_auth_state(user_id)
                self._plog("manual login confirmed")
                return {}
            except Exception:
                logger.exception("[APEC] Manual login error")
                self._plog("manual login timed out or failed")
                return {"error": "Login timed out. We didn't detect a successful login within the allowed time."}

    # --- NODE 4: Search ---
    async def search_jobs(self, state: JobApplicationState):
        await self._emit(state, "Searching for Jobs", stage_code=StageCode.SEARCHING, progress_percent=self._progress(state, "search"))
        await self._beat(state)
        search_entity = state["job_search"]
        job_title = search_entity.job_title
        contract_types = getattr(search_entity, 'contract_types', [])
        min_salary = getattr(search_entity, 'min_salary', 0)

        logger.info("[APEC] Starting search process")
        self._plog(f"NODE search_jobs -> searching '{job_title}' (min salary: {min_salary})")
        try:
            await self._handle_cookies()
            await human_warmup(self.page, self.base_url)
            await self._apply_filters(state, job_title, contract_types, min_salary)

            try:
                await self.page.wait_for_selector(self.CARD_SELECTOR, timeout=5000)
                logger.info("[APEC] Search results loaded")
                self._plog("search results loaded")
                # Skim the result list before opening anything — the last beat
                # before the scrape loop takes over.
                await self._arrival_browse(state, "results page")
            except Exception:
                logger.info("[APEC] No results found after applying filters")
                self._plog("no results found for this search")
                return {
                    "error": "No new matching jobs were found for this search today. We'll try again tomorrow!"
                }

        except Exception:
            logger.exception("[APEC] Search error")
            self._plog("search filters failed -> layout may have changed")
            return {
                "error": "We encountered an issue applying your search filters. The job board may have updated its layout."
            }

        return {}

    # --- NODE 5: Scrape Jobs ---
    async def get_matched_jobs(self, state: JobApplicationState):
        await self._emit(state, "Extracting Job Data", stage_code=StageCode.EXTRACTING_DATA, progress_percent=self._progress(state, "scrape"))
        await self._beat(state)
        logger.info("[APEC] Scraping jobs")

        user_id = state["user"].id
        self._uid = str(user_id)
        search_id = state["job_search"].id
        found_job_entities = []

        # Every posting this run has already looked at, by identity hash rather
        # than by position. Two things make position useless on its own: the
        # visit order is shuffled, and nav_back rebuilds the DOM, so the same
        # posting can surface at a different index on a later sweep. Spans
        # pages — the same job often appears again a page or two later.
        seen_jobs = []

        worker_job_limit = min(state.get("worker_job_limit", 10), 12)

        self._plog(f"NODE get_matched_jobs -> scraping starts (target: {worker_job_limit} jobs)")

        hash_result = await self.get_ignored_hashes.execute(user_id=user_id, days=30)
        if not hash_result.is_success:
            logger.warning("[APEC] Could not fetch ignored hashes: %s", hash_result.error.message)
            self._plog("could not fetch ignored hashes -> deduplication disabled for this run")
            ignored_hashes = set()
        else:
            ignored_hashes = hash_result.value

        logger.info("[APEC] Loaded %s ignored hashes. Target: %s new jobs.", len(ignored_hashes), worker_job_limit)
        self._plog(f"loaded {len(ignored_hashes)} ignored hashes for deduplication")

        page_number = 1
        max_pages = 20

        try:
            while len(found_job_entities) < worker_job_limit and page_number <= max_pages:
                
                # 🚨 INJECTED KILL CHECK: Before processing a new page
                await self._beat(state)
                if await self._is_killed(state):
                    logger.info("[APEC] Kill switch detected. Halting pagination.")
                    self._plog(f"kill switch detected mid-scrape -> returning {len(found_job_entities)} offers found so far")
                    return {"error": "Agent has been stopped.", "error_code": "AGENT_STOPPED", "found_raw_offers": found_job_entities}



                logger.info("[APEC] Processing page %s", page_number)
                self._plog(f"processing results page {page_number}")

                cards = self.page.locator(self.CARD_SELECTOR)
                try:
                    await cards.first.wait_for(state="visible", timeout=15000)
                except Exception:
                    logger.info("[APEC] No cards found on page %s", page_number)
                    self._plog(f"no job cards visible on page {page_number}")
                    if page_number == 1:
                        return {"found_raw_offers": []}
                    break

                # A real user scans the results before clicking the first one.
                await human_scan_list(self.page, 3.0 * self._pace, 7.0 * self._pace, pace=self._pace)

                count = await cards.count()
                self._plog(f"found {count} cards on page {page_number}")
                result_url = self.page.url

                # Nobody works a results page top to bottom.
                order, passed_over = plan_card_visit_order(count)
                if passed_over:
                    self._plog(
                        f"visiting {count} cards out of order "
                        f"({len(passed_over)} circled back to at the end)"
                    )
                else:
                    self._plog(f"visiting {count} cards out of order")

                for position, i in enumerate(order):
                    if len(found_job_entities) >= worker_job_limit:
                        break

                    # 🚨 INJECTED KILL CHECK: Before processing a new page
                    await self._beat(state)
                    if await self._is_killed(state):
                        logger.info("[APEC] Kill switch detected. Halting pagination.")
                        self._plog(f"kill switch detected mid-scrape -> returning {len(found_job_entities)} offers found so far")
                        return {"error": "Agent has been stopped.", "error_code": "AGENT_STOPPED", "found_raw_offers": found_job_entities}

                    cards = self.page.locator(self.CARD_SELECTOR)
                    card = cards.nth(i)
                    await self._scroll_to(card)
                    await human_delay(900, 2200)

                    raw_company, raw_title, raw_location = await self.get_raw_job_data(card)

                    if not raw_title:
                        self._plog(f"card #{i + 1}: no title extracted -> skipping")
                        # Free noise: this card is discarded either way, so a
                        # lingering hover costs the user nothing.
                        if should_hover_without_clicking(probability=0.25):
                            try:
                                await human_hover(card, duration_ms=random.randint(500, 1300))
                            except Exception:
                                pass
                        continue

                    self._plog(f"card #{i + 1} ({position + 1} of {count} visited): "
                               f"'{raw_title}' @ {raw_company or 'No Name'}")

                    fast_hash = self._generate_fast_hash(
                        raw_company if raw_company else "No Name",
                        raw_title,
                        str(user_id),
                    )

                    if fast_hash in seen_jobs:
                        self._plog(f"card #{i + 1}: already opened earlier this run -> skipping")
                        continue

                    if fast_hash in ignored_hashes:
                        self._plog(f"card #{i + 1}: already seen in last 30 days -> skipping")
                        seen_jobs.append(fast_hash)
                        if should_hover_without_clicking(probability=0.25):
                            self._plog(f"card #{i + 1}: behavioral noise -> hovering before moving on")
                            try:
                                await human_hover(card, duration_ms=random.randint(500, 1300))
                            except Exception:
                                pass
                        continue

                    # Committed to this one. Recorded before the click so a card
                    # that fails to open is not retried on the deferred sweep.
                    seen_jobs.append(fast_hash)

                    # Sometimes linger on a card before committing to it. Passing
                    # over a card is now handled up front by the deferred sweep,
                    # so this is purely the pause before opening one.
                    if should_hover_without_clicking(probability=0.30):
                        try:
                            await human_hover(card, duration_ms=random.randint(400, 1100))
                        except Exception:
                            pass

                    # RETRY: card.click() + networkidle as one unit
                    click_success = False
                    for attempt in range(3):
                        try:
                            cards = self.page.locator(self.CARD_SELECTOR)
                            card = cards.nth(i)
                            #await human_click(card)
                            await card.click()
                            await self.page.wait_for_selector('div[class="col-lg-8 border-L"]', state="visible", timeout=600000)                
                            await self.page.wait_for_load_state("networkidle")
                            click_success = True
                            break
                        except Exception:
                            if attempt == 2:
                                logger.warning("[APEC] Card click failed after 3 attempts. Skipping.")
                                self._plog(f"card #{i + 1}: click failed 3 times -> skipping")
                                break
                            await asyncio.sleep(2 ** attempt)

                    if not click_success:
                        continue

                    self._plog(f"opened offer detail page for '{raw_title}'")

                    try:
                        await self.page.wait_for_selector('div[class="col-lg-8 border-L"]', state="visible", timeout=10000)
                    except Exception:
                        pass

                    # Let the page settle, then pull the description...
                    await human_delay(800, 1800)

                    try:
                        desc_element = self.page.locator('div[class="col-lg-8 border-L"]')
                        if await desc_element.count() > 0:
                            job_desc = await desc_element.inner_text()
                        else:
                            job_desc = await self.page.locator("body").inner_text()
                    except Exception:
                        job_desc = ""

                    # ...and only then spend time "reading" it. A long offer
                    # should hold attention longer than a short one — a fixed
                    # dwell time on every page is itself a tell.
                    desc_len = len(job_desc) if job_desc else 0
                    read_min, read_max = self._read_bounds(desc_len)
                    self._plog(f"reading offer page (~{desc_len} chars) for {read_min:.0f}-{read_max:.0f}s")
                    await human_read_page(
                        self.page,
                        min_seconds=read_min,
                        max_seconds=max(read_min, read_max),
                        pace=self._pace,
                    )

                    try:
                        await self.page.wait_for_selector('a[class="btn btn-primary ml-0"]', state="visible", timeout=10000)
                    except Exception:
                        self._plog("no apply button on this offer -> going back to results")
                        await self.nav_back(has_back_button=True)
                        continue

                    apply_btn = self.page.locator('a[class="btn btn-primary ml-0"]')

                    if await apply_btn.count() > 0:
                        href = await apply_btn.get_attribute("href")

                        if href and "to=int" in href:
                            self._plog("internal APEC application detected -> opening offer URL")
                            full_offer_url = f"https://www.apec.fr{href}"

                            await self._pause(state, 1.5, 4.0, tier=Tier.CARD)                            
                            #await human_click(apply_btn)
                            await apply_btn.click()
                            await human_delay(1500, 3500)

                            try:
                                await self.page.wait_for_selector('button[title="Postuler"]', state="visible", timeout=60000)
                            except Exception:
                                self._plog("'Postuler' button never appeared -> going back to results")
                                await self.nav_back(at_redir_page=True, has_back_button=True)
                                continue

                            postule_btn = self.page.locator('button[title="Postuler"]')

                            if await postule_btn.count() > 0:
                                #await human_click(postule_btn)
                                await postule_btn.click()
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
                                await self.nav_back(at_redir_page=True, has_back_button=True)
                        else:
                            self._plog("external application -> not supported, skipping offer")
                            await self.nav_back(has_back_button=True)

                    # Breathe between cards — back-to-back offers is the single
                    # most obvious pattern in the request timing, and this is the
                    # one wait deliberately allowed to run long: up to the
                    # two-minute pause ceiling, against the six seconds a machine
                    # would take. Widened from (3, 8) when the rest of the run was
                    # cut, because trimming everything uniformly would have taken
                    # the most valuable wait down with the rest.
                    await self._pause(state, 6.0, 30.0, tier=Tier.CARD)
                    await self._distracted_stop(state)
                    await self._maybe_break(state, f"after card {i + 1}")

                if len(found_job_entities) >= worker_job_limit:
                    self._plog(f"job limit reached ({worker_job_limit}) -> stopping scrape")
                    break

                if not await self._handle_apec_pagination(page_number):
                    break
                await self._maybe_break(state, f"between pages {page_number}")
                page_number += 1

        except Exception:
            logger.exception("[APEC] Scraping error")
            self._plog("critical scraping error -> halting process")
            return {"error": "A critical error occurred while scanning the job listings. We have safely halted the process."}

        if not found_job_entities:
            self._plog("scraping finished with 0 new offers")
            return {"found_raw_offers": []}

        logger.info("[APEC] Scraping complete. Returning %s jobs.", len(found_job_entities))
        self._plog(f"scraping complete -> returning {len(found_job_entities)} new offers")
        return {"found_raw_offers": found_job_entities}

    # --- NODE 7: Submit Applications ---
    async def submit_applications(self, state: JobApplicationState):
        await self._emit(state, "Submitting Applications", stage_code=StageCode.SUBMITTING, progress_percent=self._progress(state, "submit"))
        await self._beat(state)
        logger.info("[APEC] Submitting applications")

        jobs_to_process = state.get("processed_offers", [])
        user = state["user"]
        self._uid = str(user.id)
        assigned_submit_limit = state.get("worker_job_limit", 5)

        apec_jobs = [job for job in jobs_to_process if job.job_board == JobBoard.APEC and job.status == ApplicationStatus.APPROVED]

        self._plog(f"NODE submit_applications -> {len(apec_jobs)} approved APEC offers in queue (limit: {assigned_submit_limit})")

        if not apec_jobs:
            logger.info("[APEC] No approved APEC jobs in submission queue")
            self._plog("nothing to submit -> exiting node")
            return {"status": "no_apec_jobs_to_submit"}

        successful_submissions = []

        i = 0
        for offer in apec_jobs:
            
            # 🚨 INJECTED KILL CHECK: Before starting the next submission
            await self._beat(state)
            if await self._is_killed(state):
                logger.info("[APEC] Kill switch detected. Halting submissions.")
                self._plog(f"kill switch detected mid-submission -> returning {len(successful_submissions)} submitted so far")
                return {"error": "Agent has been stopped.", "error_code": "AGENT_STOPPED", "submitted_offers": successful_submissions}

            if len(successful_submissions) >= assigned_submit_limit:
                logger.info("[APEC] Reached assigned submission limit (%s)", assigned_submit_limit)
                self._plog(f"submission limit reached ({assigned_submit_limit}) -> stopping")
                break

            self._plog(f"processing application {i + 1}/{len(apec_jobs)}: '{offer.job_title}' @ {offer.company_name}")

            try:
                # RETRY: form entry as one critical unit
                form_loaded = False
                self._plog(f"loading application form for offer: '{offer.job_title}' in {offer.form_url}")
                for attempt in range(3):
                    try:
                        await self.page.goto(offer.form_url, wait_until='networkidle', timeout=90000)
                        await human_delay(1500, 3500)
                        try:
                            await self.page.wait_for_selector('#formUpload, .form-check.uploadFile.profil-selection', state="attached", timeout=90000)
                        except Exception:
                            pass

                        if await self.page.locator('#formUpload input[type="file"]').count() > 0 or await self.page.locator('.form-check.uploadFile.profil-selection').count() > 0:
                            form_loaded = True
                            break
                        else:
                            self._plog("form not directly reachable -> clicking 'Postuler' first")
                            await self.page.wait_for_selector('button[title="Postuler"]', state="visible", timeout=60000)
                            # await human_click(self.page.locator('button[title="Postuler"]')) 
                            await self.page.locator('button[title="Postuler"]').click()                       
                            await self.page.wait_for_selector('#formUpload, .form-check.uploadFile.profil-selection', state="attached", timeout=90000)
                            form_loaded = True   # ← THE FIX
                            self._plog("form loaded after clicking 'Postuler'")
                            break
                        
                        
                    except Exception as e:
                        detail = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                        if attempt == 2:
                            logger.warning("[APEC] Form failed to load after 3 attempts. Skipping.", exc_info=True)
                            self._plog(f"form failed to load after 3 attempts -> skipping offer ({detail})")
                            break
                        self._plog(f"form load attempt {attempt + 1} failed -> retrying ({detail})")
                        await asyncio.sleep(2 ** attempt)

                if not form_loaded:
                    i += 1
                    continue

                self._plog("application form loaded")

                if user.resume_path:
                    self._plog("downloading resume from storage")
                    resume_bytes = await self.file_storage.download_file(user.resume_path)
                    human_name = user.resume_file_name or f"{user.firstname}_{user.lastname}_CV.pdf"

                    # The "Importer un CV" radio is the SECOND choixCV radio — the one inside
                    # the .uploadFile block. profil-selection is NOT a reliable anchor: it's a
                    # dynamic marker Angular moves to whichever choice is active.
                    import_block = self.page.locator('.form-check.uploadFile')
                    import_radio = import_block.locator('input[type="radio"][formcontrolname="choixCV"]')

                    # Select it. Clicking the LABEL is what actually drives the Angular handler
                    # here — the radio itself is styled/proxied through the label.
                    self._plog("selecting 'Importer un CV' option")
                    label_clicked = False
                    try:
                        # await human_click(self.page.locator('label.choice-highlight.import-cv'))
                        await self.page.locator('label.choice-highlight.import-cv').click()
                        label_clicked = True
                    except Exception:
                        try:
                            await self._check(import_radio)
                            label_clicked = True
                        except Exception:
                            logger.warning("[APEC] Could not select 'Importer un CV'")
                            self._plog("could not select 'Importer un CV' option")

                    # Wait for the upload widget to actually become visible. This is the real
                    # signal the switch worked — the .form-apec div drops its hidden attribute.
                    try:
                        await self.page.wait_for_selector(
                            '#formUpload input[type="file"]', state="visible", timeout=10000
                        )
                    except Exception:
                        logger.warning("[APEC] Upload widget did not become visible after selecting import")
                        self._plog("upload widget never became visible after selecting import")

                    # Confirm the import radio is now the active choice.
                    is_active = await import_block.evaluate(
                        "el => el.classList.contains('profil-selection')"
                    )
                    if not is_active:
                        logger.warning("[APEC] 'Importer un CV' not marked active (profil-selection); "
                                    "old CV will likely be submitted")
                        self._plog("WARNING: import radio not active -> profile CV may be submitted instead")

                    # Now the file input is visible — set the file.
                    self._plog(f"uploading resume: {human_name}")
                    file_input = self.page.locator('#formUpload input[type="file"]').first
                    await self._upload(
                        file_input,
                        {
                            "name": human_name,
                            "mimeType": "application/pdf",
                            "buffer": resume_bytes,
                        },
                    )
                    # Angular does not always pick the file up from the input's
                    # own change; this nudge stays.
                    await file_input.dispatch_event("change")
                    await human_delay(1800, 4000)

                    # Verify the upload label updated to our file.
                    try:
                        lbl = (await self.page.locator('#formUpload label').first.inner_text()).strip()
                        logger.info("[APEC] Upload widget shows: %s", lbl)
                        self._plog(f"upload widget now shows: {lbl}")
                    except Exception:
                        pass

                    try:
                        save_checkbox = self.page.locator('input[formcontrolname="isCvSave"]')
                        if await save_checkbox.count() > 0 and await save_checkbox.is_checked():
                            self._plog("unchecking 'save CV to profile' checkbox")
                            await self._uncheck(save_checkbox)
                    except Exception:
                        pass

                    

                   
                # C. Cover Letter
                try:
                    has_radio_version = await self.page.locator('input[formcontrolname="choixLm"]').count() > 0

                    if has_radio_version:
                        self._plog("filling cover letter (radio/textarea version)")
                        await self.page.wait_for_selector('label:has-text("Saisir directement ma lettre de motivation")', state="visible")
                        # await human_click(self.page.locator('input[formcontrolname="choixLm"]').last)
                        await self.page.locator('input[formcontrolname="choixLm"]').last.click()
                        await self.page.wait_for_selector('textarea[formcontrolname="lmTexteSaisie"]', state="visible", timeout=10000)

                        if offer.cover_letter:
                            textarea = self.page.locator('textarea[formcontrolname="lmTexteSaisie"]')
                            # await human_click(textarea)
                            await textarea.click()
                            await human_delay(700, 1600)
                            await self._type(textarea, offer.cover_letter)
                            await textarea.dispatch_event('input')
                            # Re-read what was just written.
                            await human_delay(2000, 5000)
                            await human_scroll(self.page, distance=random.randint(150, 300))

                    else:
                        self._plog("filling cover letter (collapse/comment version)")
                        await self.page.wait_for_selector('a[aria-controls="collapseThree"]', state="visible")
                        anchor = self.page.locator('a[aria-controls="collapseThree"]').first
                        anchor_label = self.page.locator('div[id="headingThree"]').first

                        #await human_click(anchor_label)
                        await anchor_label.click()
                        await self.page.locator('#collapseThree').wait_for(state="visible", timeout=10000)

                        val = await anchor.get_attribute('aria-expanded')
                        if val != 'true':
                            # await human_click(anchor_label)
                            await anchor_label.click()
                            await self.page.locator('#collapseThree').wait_for(state="visible", timeout=10000)

                        if offer.cover_letter:
                            comment = self.page.locator('#comment')
                            # await human_click(comment)
                            await comment.click()
                            await human_delay(700, 1600)
                            await self._type(comment, offer.cover_letter)
                            await comment.dispatch_event('input')
                            await human_delay(2000, 5000)
                            await human_scroll(self.page, distance=random.randint(150, 300))

                    self._plog("cover letter filled")

                except Exception:
                    logger.warning("[APEC] Could not fill cover letter")
                    self._plog("could not fill cover letter -> continuing without it")

                # D. Additional Data
                try:
                    self._plog("filling additional data section (education)")
                    await self.page.wait_for_selector('a[aria-controls="#collapse_additionalData"]', state="visible")
                    anchor_sec = self.page.locator('a[aria-controls="#collapse_additionalData"]').first
                    anchor_sec_label = self.page.locator('div[id="heading_additionalData"]').first

                    if await anchor_sec.get_attribute('aria-expanded') != 'true':
                        # await human_click(anchor_sec_label)
                        await anchor_sec_label.click()
                        await self.page.wait_for_selector('ng-select[formcontrolname="idNiveauFormation"]', state="visible", timeout=30000)

                    if hasattr(user, 'study_level') and user.study_level:
                        # await human_click(self.page.locator('ng-select[formcontrolname="idNiveauFormation"]'))
                        await self.page.locator('ng-select[formcontrolname="idNiveauFormation"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        # await human_click(self.page.locator(f'.ng-option-label:has-text("{user.study_level}")').first)
                        await self.page.locator(f'.ng-option-label:has-text("{user.study_level}")').first.click()
                        await human_delay(600, 1400)

                    if hasattr(user, 'major') and user.major:
                        # await human_click(self.page.locator('ng-select[formcontrolname="idDiscipline"]'))
                        await self.page.locator('ng-select[formcontrolname="idDiscipline"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        # await human_click(self.page.locator(f'.ng-option-label:has-text("{user.major}")').first)
                        await self.page.locator(f'.ng-option-label:has-text("{user.major}")').first.click()
                        await human_delay(600, 1400)

                    if hasattr(user, 'school_type') and user.school_type:
                        # await human_click(self.page.locator('ng-select[formcontrolname="idNatureFormation"]'))
                        await self.page.locator('ng-select[formcontrolname="idNatureFormation"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        # await human_click(self.page.locator(f'.ng-option-label:has-text("{user.school_type}")').first)
                        await self.page.locator(f'.ng-option-label:has-text("{user.school_type}")').first.click()
                        await human_delay(600, 1400)

                    if hasattr(user, 'graduation_year') and user.graduation_year:
                        # await human_click(self.page.locator('ng-select[formcontrolname="anneeObtention"]'))
                        await self.page.locator('ng-select[formcontrolname="anneeObtention"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        # await human_click(self.page.locator(f'.ng-option-label:has-text("{user.graduation_year}")').first)
                        await self.page.locator(f'.ng-option-label:has-text("{user.graduation_year}")').first.click()

                    self._plog("additional data filled")

                except Exception:
                    logger.warning("[APEC] Could not fill additional data (optional)")
                    self._plog("could not fill additional data (optional) -> continuing")

                # F. Submit — NO retry (duplicate submission risk)
                # Read the application back before sending it.
                submit_btn = self.page.locator('button[title="Envoyer ma candidature"]')
                if await submit_btn.is_visible():
                    self._plog("clicking 'Envoyer ma candidature' (no retry: duplicate risk)")
                    # No stray-window retry: a repeated submit is an application
                    # sent twice. The window is still closed, just not re-clicked.
                    #await human_click(submit_btn, retry_on_popup=False)
                    await submit_btn.click()
                

                    try:
                        await self.page.wait_for_selector('section[id="comp_cover"] h1:has-text("candidature a")', state="visible", timeout=45000)
                        logger.info("[APEC] Application submitted")
                        self._plog(f"application SUBMITTED: '{offer.job_title}' @ {offer.company_name} ({len(successful_submissions) + 1}/{assigned_submit_limit})")
                        offer.status = ApplicationStatus.SUBMITTED
                        successful_submissions.append(offer)
                        await self._emit(
                            state,
                            f"Submitted: {offer.job_title}",
                            stage_code=StageCode.SUBMITTING,
                            count_band="submit",
                                    count_done=len(successful_submissions),
                                    count_total=len([j for j in apec_jobs
                                                     if j.status == ApplicationStatus.APPROVED]),
                        )
                    except Exception:
                        logger.warning("[APEC] Submission failed — confirmation not received")
                        self._plog("submission NOT confirmed -> no confirmation banner appeared")
                        continue
                else:
                    logger.warning("[APEC] Submit button not visible")
                    self._plog("submit button not visible -> skipping offer")

            except Exception:
                logger.exception("[APEC] Submission failed for %s", offer.url)
                self._plog(f"submission crashed for '{offer.job_title}' -> moving to next offer")

            # Nobody fires off applications back to back. Shorter than it was,
            # and now the ONLY thing pacing this loop: the review read that used
            # to precede the send is gone, so without this the node is fill,
            # submit, next form, at whatever speed the forms load.
            await self._pause(state, 15.0, 25.0, tier=Tier.SUBMIT)
            await self._maybe_break(state, "between submissions")

            i += 1

        if not successful_submissions:
            self._plog("all submission attempts failed")
            return {"error": "All application attempts failed. The job board may have updated its application form structure."}

        logger.info("[APEC] Successfully submitted %s applications", len(successful_submissions))
        self._plog(f"submission node done -> {len(successful_submissions)} applications submitted")
        return {"submitted_offers": successful_submissions}

    # --- NODE 9: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        await self._emit(state, "Cleaning Up", stage_code=StageCode.CLEANING_UP, progress_percent=self._progress(state, "cleanup"))
        logger.info("[APEC] Cleanup")
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
            return {"error": "", "error_code": ""}

        return {}

    # =========================================================================
    # GRAPH
    # =========================================================================

    def route_action_intent(self, state: JobApplicationState):
        intent = state.get("action_intent", "SCRAPE")
        self._plog(f"routing intent: {intent}", user_id=state["user"].id)
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