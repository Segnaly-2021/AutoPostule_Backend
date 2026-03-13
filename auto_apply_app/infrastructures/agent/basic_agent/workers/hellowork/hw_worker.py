import json
import asyncio
from langgraph.graph import StateGraph, END, START
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright
import pdfplumber

# --- DOMAIN IMPORTS ---
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import JobBoard, ApplicationStatus

# --- INFRA & APP IMPORTS ---
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.application.use_cases.agent_use_cases import ProcessAgentResultsUseCase, SaveJobApplicationsUseCase

class HelloWorkWorker:
    # 1. INJECTION: Dependencies come from the Container
    def __init__(self, 
                 llm: BaseChatModel, 
                 results_processor: ProcessAgentResultsUseCase, 
                 results_saver: SaveJobApplicationsUseCase
                ):
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None
        self.playwright: Playwright = None
        self.base_url = "https://www.hellowork.com/fr-fr/"
        
        self.llm = llm
        self.results_processor = results_processor
        self.results_saver = results_saver

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
    
    # --- HELPER: Cookie Handling ---
    async def _handle_cookies(self):
        print("--- [HW] Checking for cookies...")
        try:
            # Handle HelloWork specific cookie banner
            await self.page.wait_for_selector('[id="hw-cc-notice-continue-without-accepting-btn"]', state="attached", timeout=3000)   
            cookie_btn = self.page.locator('[id="hw-cc-notice-continue-without-accepting-btn"]')
            is_visible = False
            if await cookie_btn.count() > 0:
                is_visible = True
                await cookie_btn.click()
            print("info: Cookie popup handled by accepting it.")
        except Exception:
            if is_visible:
                await self.page.wait_for_selector('div[class="hw-cc-main"]', state='attached', timeout=2000)
                count = await self.page.evaluate("""() => {
                    const overlays = document.querySelectorAll('.hw-cc-main');
                    let removed = 0;
                    overlays.forEach(el => {
                        el.remove();
                        removed++;
                    });
                    return removed;
                }""")
                print(f"{count} Axeptio overlay(s) removed from the DOM.")
            else:
                # If we get here, it means the ID was never found after 5s.
                # That's GOOD! It means no popup appeared. We continue safely.
                print("info: No cookie popup detected (or already gone).")
                
        

    # --- NODE 1: Start Session ---
    async def start_session(self, state: JobApplicationState):
        print(f"--- [HW] Starting session for {state['user'].firstname} {state['user'].lastname} ")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security', 
                # Add other args from your original file if needed
            ]
        )
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        return {"status": "session_started"}
    


    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        print("--- [HW] Navigating to HelloWork ---")
        try:
            await self.page.goto(self.base_url)
            await self._handle_cookies()  
            return {
                "current_url": self.base_url, 
                "status": "on_homepage"
            }          
            
        except Exception as e:
            print(f"Nav Error: {e}")

    # --- NODE 3: Login ---
    async def login(self, state: JobApplicationState):
        if state.get("is_login"):
            return {"status": "already_logged_in"}
        
        print("--- [HW] Requesting Login ---")
        try:
            # Open Menu
            await self.page.locator('[data-cy="headerAccountMenu"]').click()
            # Click Login
            await self.page.locator('[data-cy="headerAccountLogIn"]').click()
            
            print("⚠ ACTION REQUIRED: Please log in manually within 60 seconds...")
           
            await asyncio.sleep(60)
            
            # Navigate back to home/search area after login to ensure clean state
            await self.page.locator('a[href="/fr-fr"]').first.click()            
            
            return {"is_login": True}
        except Exception as e:
            print(f"Login Error: {e}")
            return {"status": "login_failed"}
        

   
    # --- NODE 4: Search ---
    async def search_jobs(self, state: JobApplicationState):
        # USE ENTITY
        search_entity = state["job_search"]
        job_title = search_entity.job_title
        
        print(f"--- [HW] Searching for: {job_title} ---")
        try:
            # HelloWork specific search input ID
            await self.page.locator('input[id="k"]').fill(job_title)
            await self.page.keyboard.press("Enter")

            await self.page.wait_for_timeout(5000)

            
            # If the specific result list wrapper appears, we know we are good
            try:
                await self.page.wait_for_selector('li[data-testid="search-results-list-item-wrapper"]', timeout=3000)
            except Exception:
                pass # Just proceed, maybe 0 results
                
            #await self._handle_cookies()
            
        except Exception as e:
            print(f"Search Error: {e}")
        return {
            "status": "search_complete",
            "current_url": self.page.url
        }

# --- NODE 5: Scrape Jobs (Integrated) ---
    async def get_matched_jobs(self, state: JobApplicationState):
        print("--- [HW] Scraping Jobs ---")
        
        # Buffer for Domain Entities
        found_job_entities = []
        
        search_url = self.page.url
        
        # HelloWork specific card selector
        cards_locator = self.page.locator('[data-id-storage-target="item"]')
        
        try:
            # Wait for list to populate
            await cards_locator.first.wait_for(timeout=5000)
            count = await cards_locator.count()
            
            # Limit for testing/batching
            limit = min(count, 7)

            for i in range(limit):
                print(f"Processing card {i+1}/{limit}")
                
                # 1. Re-locate elements to avoid staleness
                cards = self.page.locator('[data-id-storage-target="item"]')
                card = cards.nth(i)
                
                # 2. Click & Load Details
                await card.click()
                await self.page.wait_for_load_state("networkidle")
                
                
                current_url = self.page.url
                
                # 3. Extract Metadata (On Details Page)
                
                raw_title = await self._get_job_attribute('[data-cy="jobTitle"]', "Title Not Found")                
                raw_company = await self._get_job_attribute('[class="tw-typo-s sm:tw-typo-m tw-link-underline"]', 'Company Name Not Found')                                
                raw_location = await self._get_job_attribute('[class="tw-tag-grey-s tw-readonly"]', 'France')
                
                # 4. Check for Apply Button
                apply_btn = self.page.locator('[data-cy="applyButton"]').first
                
                if await apply_btn.count() > 0:
                    try:
                        # Handle potential popup (External vs Internal check)
                        # Expecting a popup means it's an external redirect
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
                        offer = JobOffer(
                            url=current_url,
                            # For HelloWork, the form is usually on the same page or a modal
                            form_url=current_url,
                            search_id=state["job_search"].id,
                            company_name=raw_company,
                            job_title=raw_title,
                            location=raw_location,
                            job_board=JobBoard.HELLOWORK,
                            status=ApplicationStatus.FOUND,
                            followup_date=None
                        )
                                             
                        found_job_entities.append(offer)
                
                # 5. Navigate back to process next card
                print("🔙 Returning to search results...")
                await self.page.goto(search_url, wait_until="networkidle")
                await self.page.wait_for_timeout(2000)

        except Exception as e:
            print(f"Scraping Error: {e}")

        # Return the entities to the state buffer
        return {"found_raw_offers": found_job_entities}

    # --- NODE 6: Analyze (Optimized Flow) ---
    async def analyze_jobs(self, state: JobApplicationState):
        print("--- [HW] Analyzing & Ranking Jobs ---")
        
        user_id = state["user"].id
        search_id = state["job_search"].id
        raw_offers = state["found_raw_offers"]

        # 1. OPTIMIZATION: Filter & Persist Raw Jobs FIRST
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
                #await self._handle_cookies()
                
                try:
                    # HelloWork Description Selector
                    # Often in #offer-panel or generic container
                    desc_el = self.page.locator('section[class="tw-peer tw-flex tw-flex-col tw-w-full tw-border-b tw-border-b-grey-100 tw-pb-8 sm:tw-pb-10"]')
                    if await desc_el.count() == 0: 
                         desc_el = self.page.locator('div[id="offer-panel"]')
                    job_desc = await desc_el.inner_text()
                except Exception:
                    job_desc = ""

                # B. Validation Check
                if len(job_desc) < 50:
                    print("⏩ Description too short, skipping.")
                    continue

                # C. LLM Call (Your Exact System Message)
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
                
                prompt = HumanMessage(f"""
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
            return {
                "status": "on_analysis", 
                "found_raw_offers": [],
                "processed_offers": processed_offers
            }
        else:
            print("No offers processed after analysis.")
            return {
                "status": "on_analysis", 
                "processed_offers": []
            }


    # --- NODE 7: Submit & Save (HelloWork) ---
    async def submit_applications(self, state: JobApplicationState):
        print("--- [HW] Submitting Applications ---")
        
        # 1. Get Inputs from State
        jobs_to_submit = state["processed_offers"]
        user = state["user"] # The User Entity

        if not jobs_to_submit:
            print("No jobs in submission queue.")
            return {"status": "no_jobs"}

        successful_submissions = []
        i = 0
        for offer in jobs_to_submit:
            print(f"📝 Applying to job offer ({i+1}/{len(jobs_to_submit)}): {offer.job_title}")
            try:
                # A. Navigation
                # For HelloWork, offer.url is usually the application page itself
                await self.page.goto(offer.form_url, wait_until="domcontentloaded")  
                await self.page.wait_for_timeout(2000)              

                # B. Fill Identity (Using User Entity)
                # HelloWork uses specific 'name' attributes
                await self.page.locator('input[name="Firstname"]').fill(user.firstname)
                await self.page.locator('input[name="LastName"]').fill(user.lastname)
                
                # C. Upload Resume
                if user.resume_path:
                    # HelloWork specific uploader
                    await self.page.locator('[data-cy="cv-uploader-input"]').set_input_files(user.resume_path)
                
                # D. Cover Letter (If generated)
                if offer.cover_letter:
                    # Click to expand the text area
                    await self.page.locator('[data-cy="motivationFieldButton"]').click()
                    await self.page.wait_for_timeout(1000)
                    # Fill the text area
                    await self.page.locator('textarea[name="MotivationLetter"]').fill(offer.cover_letter)

                # E. Human Verification / Simulation
                print("⏳ Sleeping 30s for manual verification/CAPTCHA...")
                await asyncio.sleep(30) 
                
                # F. Submit
                submit_btn = self.page.locator('[data-cy="submitButton"]')
                
                if await submit_btn.is_visible():
                    await submit_btn.click() # Uncomment to actually submit
                    await self.page.wait_for_timeout(5000)  # Wait for submission to process
                    print("Job application submitted")
                    
                    # Update Status & Add to success list
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                else:
                    print("❌ Submit button not visible.")
                i += 1
            except Exception as e:
                i += 1
                print(f"❌ Submission failed for {offer.url}: {e}")
                # We do NOT add to successful_submissions, so it won't be saved.
                continue

        # 2. SAVE TO DATABASE (The "Writer" Use Case)
        # Only persist jobs that were successfully processed
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
        print("--- [HW] Cleanup ---")
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        return {"status": "finished"}

    # --- GRAPH BUILDER ---
    def get_graph(self):
        workflow = StateGraph(JobApplicationState)
        
        # Add Nodes
        workflow.add_node("start", self.start_session)
        workflow.add_node("nav", self.go_to_job_board)
        workflow.add_node("login", self.login)
        workflow.add_node("search", self.search_jobs)
        workflow.add_node("scrape", self.get_matched_jobs)
        workflow.add_node("analyze", self.analyze_jobs)
        workflow.add_node("submit", self.submit_applications)
        workflow.add_node("cleanup", self.cleanup)
        
        # Add Edges
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