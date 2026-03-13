# auto_apply_app/infrastructure/agent/workers/wttj_worker.py
import json
import asyncio
import pdfplumber
from langgraph.graph import StateGraph, END, START
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage


# 1. imports from Domain
from auto_apply_app.domain.value_objects import ApplicationStatus, JobBoard
from auto_apply_app.domain.entities.job_offer import JobOffer

# 2. Imports from Infrastructure
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.application.use_cases.agent_use_cases import ProcessAgentResultsUseCase, SaveJobApplicationsUseCase



class WelcomeToTheJungleWorker:
    # 2. Injection: We strictly inject the brain (LLM) and the tool (Processor)
    def __init__(self, 
                 llm: BaseChatModel, 
                 results_processor: ProcessAgentResultsUseCase, 
                 results_saver: SaveJobApplicationsUseCase
                 ):
        self.llm = llm
        self.results_processor = results_processor
        self.results_saver = results_saver
        
        # Internal Browser State
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None
        self.playwright: Playwright = None
        self.base_url = "https://www.welcometothejungle.com/fr"


    # --- HELPER: Cookie Handling ---
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
            # If we get here, it means the ID was never found after 5s.
            # That's GOOD! It means no popup appeared. We continue safely.
            print("info: No cookie popup detected (or already gone).")
            
    # --- HELPER: Get Job attributes ---
    async def _get_job_attribute(self, selector: str, default_value: str = None):
        try:
            # 1. Wait for attachment rather than full visibility
            # 2. Add a specific timeout so it doesn't hang for 30s
            await self.page.wait_for_selector(selector, state='attached', timeout=5000)
            
            # 3. Use .first in case the selector matches multiple items
            text = await self.page.locator(selector).first.inner_text()
            
            return text.strip() 
        except Exception:
            # Debug: Uncomment this to see why it's actually failing
            # print(f"Log: Failed to get {selector}: {e}")
            return default_value


    # --- HELPER: Resume Extraction ---
    def _extract_resume(self, resume_path: str) -> str:
        text = ""
        try:
            with pdfplumber.open(resume_path) as pdf:
                for p in pdf.pages:
                    text += p.extract_text() + "\n"
        except Exception as e:
            print(f"Error reading resume: {e}")
        return text

    # --- NODE 1: start_session ---
    async def start_session(self, state: JobApplicationState):
        """
        Boots up the browser.
        """
        print(f"--- [WTTJ] Starting session for {state['user'].firstname} {state['user'].lastname} ")
        
        self.playwright = await async_playwright().start()
        # headless=False so you can watch it work
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        
        return {"status": "session_started"}
    
    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        print("--- [WTTJ] Navigating ---")
        try:
            await self.page.goto(self.base_url)
            await self._handle_cookies()           

        except Exception as e:
            print(f"Nav Error: {e}")
            
        return {"status": "on_homepage"}
    

    # --- NODE 3: Login ---
    async def request_login(self, state: JobApplicationState):
        if state.get("is_login"):
            return {"status": "already_logged_in"}

        print("--- [WTTJ] Requesting Login ---")
        try:
            # WTTJ specific login button
            await self.page.get_by_test_id("not-logged-visible-login-button").click()

            print("⚠ ACTION REQUIRED: Manual login required (waiting 60s)...")
            await asyncio.sleep(60)
                
            # Verify login success by looking for menu
            await self.page.get_by_test_id("menu-jobs").click(timeout=5000)
           
        except Exception as e:
            print(f"Login Error: {e}")
            
        return {
            "status": "login_step_complete",
            "is_loggin": True
        }

    # --- NODE 4: Search (Interaction Based) ---
    async def search_jobs(self, state: JobApplicationState):
        # 1. INTEGRATION: Get title from Entity
        search_entity = state["job_search"]
        job_title = search_entity.job_title

        print(f"--- [WTTJ] Searching for: {job_title} ---")
        
        try:
            # 2. Type and Enter (Your logic)
            # Fixed: fill() takes a string, removed the brackets []
            await self.page.get_by_test_id("jobs-home-search-field-query").fill(job_title)
            await self.page.keyboard.press("Enter")
            
            # 3. Wait for results
            # Kept your timeout, but added a specific selector wait to be safe
            await self.page.wait_for_timeout(5000)
            
            # If the specific result list wrapper appears, we know we are good
            try:
                await self.page.wait_for_selector('li[data-testid="search-results-list-item-wrapper"]', timeout=3000)
            except Exception:
                pass # Just proceed, maybe 0 results
                
            await self._handle_cookies()
            
        except Exception as e:
            print(f"Search Error: {e}")
            # We don't return "failed" because 0 results is a valid state for the next node
            
        return {
            "status": "on search page",
            "current_url": self.page.url
        }


    # --- NODE 5: Scrape Jobs (Integrated) ---
    async def get_matched_jobs(self, state: JobApplicationState):
        print("--- [WTTJ] Scraping Jobs ---")
        
        # Buffer for Domain Entities
        found_job_entities = [] 
        
        search_url = self.page.url
        cards_locator = self.page.get_by_test_id("search-results-list-item-wrapper")
        
        try:
            # Wait for list to populate
            await cards_locator.first.wait_for(timeout=5000)
            count = await cards_locator.count()
            
            # Limit for testing/batching
            limit = min(count, 3) 

            for i in range(limit):
                print(f"Processing card {i+1}/{limit}")
                
                # 1. Re-locate elements to avoid staleness
                cards = self.page.get_by_test_id("search-results-list-item-wrapper")
                card = cards.nth(i)

                # 3. Enter Job Details
                await card.click()
                await self.page.wait_for_load_state("networkidle")
                await self._handle_cookies()
                
                
                
                # 2. Extract Basic Info (Before Click - Optional but safer)   
                current_url = self.page.url                         
                raw_title = await self._get_job_attribute('h2[class="sc-izXThL gznKxO  wui-text"]', "Title Not Found")
                raw_company = await self._get_job_attribute('span[class="sc-izXThL efppjl  wui-text"]', "Company Name Not Found")
                raw_location = await self._get_job_attribute('span[class="sc-iZxruM itestC"]', "Location Not Found")  
                
                # 4. Check for Apply Button     
                apply_btn = self.page.locator('[data-testid="job_bottom-button-apply"]').first
                
                if await apply_btn.count() > 0:
                    try:
                        # Handle potential popup (External vs Internal check)
                        async with self.page.expect_popup(timeout=3000) as popup_info:
                            await apply_btn.click()
                        
                        # CASE A: Popup Opened -> External Site
                        new_page = await popup_info.value
                        print(f"External form detected (Ignoring): {new_page.url}")
                        await new_page.close()
                        
                    except Exception:
                        # CASE B: No Popup -> Internal Form
                        print(f"Internal form found: {current_url}")
                        
                        # --- ARCHITECTURE INTEGRATION ---
                        # We must create the Entity here                  
                        

                        offer = JobOffer(
                            url=current_url,
                            form_url=current_url,
                            search_id=state["job_search"].id,
                            company_name=raw_company, # Scraped or generic
                            job_title=raw_title,
                            location=raw_location,
                            job_board=JobBoard.WTTJ,
                            status=ApplicationStatus.FOUND,
                            followup_date=None
                        )
                        
                        found_job_entities.append(offer)
                
                # 5. Navigate back to process next card
                print("🔙 Returning to search results...")
                await self.page.goto(search_url, wait_until="networkidle")
                await self._handle_cookies()

        except Exception as e:
            print(f"Scraping Error: {e}")

        # Return the entities to the state buffer
        # Note: We use 'found_raw_offers' as defined in your State TypedDict
        return {"found_raw_offers": found_job_entities}



    # --- NODE 6: Analyze (Optimized Flow) ---
    async def analyze_jobs(self, state: JobApplicationState):
        print("--- [WTTJ] Analyzing & Ranking Jobs ---")
        
        user_id = state["user"].id
        search_id = state["job_search"].id
        raw_offers = state["found_raw_offers"]

        # 1. OPTIMIZATION: Filter & Persist Raw Jobs FIRST
        # We call the tool immediately. It saves new jobs as "FOUND" and ignores existing ones.
        # It returns the list of jobs that were actually processed (the new ones).
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

        processed_offers = []

        # 3. LLM Loop (Only on new jobs)
        for offer in jobs_to_analyze:
            print(f"🤖 Analyzing: {offer.job_title}")
            try:
                # A. Navigation & Scrape
                await self.page.goto(offer.url, wait_until="domcontentloaded")
                await self._handle_cookies()
                
                try:
                    # Optimized selector
                    desc_el = self.page.locator("div#the-position-section")
                    print(f"[Debug] job desc within div#the-position-section: {await desc_el.count()}")
                    if await desc_el.count() == 0: 
                        desc_el = self.page.locator("main")
                    job_desc = await desc_el.inner_text()
                except Exception:
                    print("[Debug] Error when fetching job desc within div#the-position-section")
                    job_desc = ""

                # B. Validation Check
                if len(job_desc) < 50:
                    print("⏩ Description too short, skipping.")
                    continue

                # C. LLM Call
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

                prompt = HumanMessage(content=f"""
                Job Description: {job_desc}
                Resume: {resume_text}        
                
                """)
                print("[Debug] Sending LLM request...")
                response = await self.llm.ainvoke([system_message, prompt])

                clean_json = response.content[0]["text"]
                data = json.loads(clean_json)   
                cover_letter = data.get("cover_letter", "")
                ranking = int(data.get("ranking", 5))
                print(f"LLM Response - Ranking: {ranking} - Cover Letter Length: {len(cover_letter)}")
                
                # E. Update Entity
                offer.cover_letter = cover_letter
                offer.ranking = int(ranking)
                
                processed_offers.append(offer)

            except Exception as e:
                print(f"Analysis Error {offer.url}: {e}")
                continue

        if len(processed_offers) > 0:
            # Reorder in descending order according to the ranking
            processed_offers.sort(key=lambda x: x.ranking, reverse=True )
            print(f"Analysis complete: {len(processed_offers)} offers processed.\n\n{processed_offers}")
            return {"found_raw_offers": processed_offers}
        else:
            print("No offers processed after analysis.")
            return {"found_raw_offers": []}
    

    # --- NODE 7: Submit & Save ---
    async def submit_applications(self, state: JobApplicationState):
        print("--- [WTTJ] Submitting Applications ---")
        
        # 1. Get Inputs from State
        jobs_to_submit = state.get("found_raw_offers", [])
        user = state["user"] # The User Entity

        if not jobs_to_submit:
            print("No jobs in submission queue.")
            return {"status": "no_jobs"}

        successful_submissions = []
        for offer in jobs_to_submit:
            print(f"📝 Applying to: {offer.job_title}")
            try:
                # A. Navigate to Job (or refresh if already there)
                # WTTJ form is usually a modal on the same URL
                await self.page.goto(offer.url)
                await self._handle_cookies()
                
                # B. Open Form Drawer
                # We reuse the logic to find the button
                await self.page.wait_for_selector('[data-testid="job_bottom-button-apply"]', state="attached") # Wait for potential animations
                apply_btn = self.page.locator('[data-testid="job_bottom-button-apply"]').first
                if await apply_btn.count() == 0:
                    print(f"❌ Apply button not found for {offer.url}")
                    continue
                    
                await apply_btn.click()
                await self._handle_cookies()
                
                # C. Fill Form (Using User Entity Data)
                # Note: We use the Entity attributes (user.first_name), not dictionary keys
                await self.page.get_by_test_id("apply-form-field-firstname").fill(user.firstname)
                await self.page.get_by_test_id("apply-form-field-lastname").fill(user.lastname)
                
                # Phone might be optional or null in your entity, handle safely
                if user.phone_number: 
                    await self.page.get_by_test_id("apply-form-field-phone").fill(user.phone_number)
                
                
                await self.page.get_by_test_id("apply-form-field-subtitle").fill(user.current_position) 

                # D. File Upload
                # We use the path stored in the User Entity
                if user.resume_path:
                    await self.page.get_by_test_id("apply-form-field-resume").set_input_files(user.resume_path)
                
                # E. Cover Letter (From the JobOffer Entity, generated by LLM)
                await self.page.get_by_test_id("apply-form-field-cover_letter").fill(offer.cover_letter)
                
                # F. Consent Checkbox
                checkbox = self.page.get_by_test_id("apply-form-consent")
                if await checkbox.count() > 0 and not await checkbox.is_checked():
                    await checkbox.check()
                
                # G. Human verification (Your 300s sleep)
                print("⏳ Sleeping 30s for manual verification (Production: Remove this)...")
                await asyncio.sleep(30) # Reduced to 30s for testing
                
                # H. Submit (Clicking the final button) 
                await self.page.wait_for_selector('[data-testid="apply-form-submit"]', state="attached")
                submit_btn = self.page.locator('[data-testid="apply-form-submit"]')
                if await submit_btn.is_visible():
                    await submit_btn.click() 
                    await self.page.wait_for_timeout(2000) # Wait for submission processing
                    print("✅Application submitted")
                    
                    # Update Status
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                else:
                    print("❌ Submit button not visible.")

            except Exception as e:
                print(f"❌ Submission failed for {offer.url}: {e}")
                # We do NOT add it to 'successful_submissions', so it won't be saved to DB.
                continue

        # 2. SAVE TO DATABASE (The "Writer" Use Case)
        # We only persist the applications that actually worked.
        if successful_submissions:
            print(f"💾 Saving {len(successful_submissions)} successful applications...")
            save_result = await self.results_saver.execute(successful_submissions)
            
            if save_result.is_success:
                print(f"🎉 Success: {save_result.value}")
            else:
                print(f"⚠ DB Save Error: {save_result.error.message}")
                
        return {"status": "batch_complete"}
    

    # --- NODE 8: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        print("--- [WTTJ] Cleanup ---")
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        return {"status": "finished"}

    # --- GRAPH BUILDER ---
    def get_graph(self):
        workflow = StateGraph(JobApplicationState)
        
        workflow.add_node("start", self.start_session)
        workflow.add_node("nav", self.go_to_job_board)
        workflow.add_node("login", self.request_login)
        workflow.add_node("search", self.search_jobs)
        workflow.add_node("scrape", self.get_matched_jobs)
        workflow.add_node("analyze", self.analyze_jobs)
        workflow.add_node("submit", self.submit_applications)
        workflow.add_node("cleanup", self.cleanup)
        
        workflow.add_edge(START, "start")
        workflow.add_edge("start", "nav")
        workflow.add_edge("nav", "login")
        workflow.add_edge("login", "search")
        workflow.add_edge("search", "scrape")
        workflow.add_edge("scrape", "analyze")
        workflow.add_edge("analyze", "submit")
        workflow.add_edge("submit", "cleanup")
        workflow.add_edge("cleanup", END)
        
        return workflow.compile()