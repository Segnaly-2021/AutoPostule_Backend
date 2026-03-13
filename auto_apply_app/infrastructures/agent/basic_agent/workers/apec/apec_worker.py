import asyncio
import json
from langgraph.graph import StateGraph, END, START
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.language_models import BaseChatModel
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright, Locator
import pdfplumber

# --- DOMAIN IMPORTS ---
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import JobBoard, ApplicationStatus

# --- INFRA & APP IMPORTS ---
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.application.use_cases.agent_use_cases import ProcessAgentResultsUseCase, SaveJobApplicationsUseCase

class ApecWorker():
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
        self.base_url: str = "https://www.apec.fr/"
        
        # Injected Dependencies
        self.llm = llm
        self.results_processor = results_processor
        self.results_saver = results_saver


    # --- HELPER: Handle Cookies ---
    async def _handle_cookies(self):
        try: 
            # Handle Cookies
            await self.page.wait_for_selector('button:has-text("Refuser tous les cookies")', state='attached', timeout=5000)
            cookie_btn = self.page.locator('button:has-text("Refuser tous les cookies")')
            if await cookie_btn.count() > 0:
                await cookie_btn.click()
        except Exception:
            print("No Cookies popup")


    # --- HELPER: Resume Extraction ---
    def _extract_resume(self, resume_path: str) -> str:
        text = ""
        try:
            # Note: Ensure resume_path is a valid string/path before opening
            if not resume_path:
                return ""
            with pdfplumber.open(resume_path) as pdf:
                for p in pdf.pages:
                    text += p.extract_text() + "\n"
        except Exception as e:
            print(f"Error reading resume: {e}")
        return text
    

    # --- HELPER: Get Job attributes ---
    async def _get_job_attribute(self, card: Locator, selector: str, default_value: str=None):
        try:           
            content = await card.locator(selector).inner_text()
            return content.strip()
        except Exception:
            return default_value
    

    # --- NODE 1: Start Session ---
    async def start_session(self, state: JobApplicationState):
        print(f"--- [APEC] Starting session for {state['user'].firstname} {state['user'].lastname} ")
       
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        return {"status": "session_started"}
    


    # --- NODE 2: Navigation ---
    async def go_to_job_board(self, state: JobApplicationState):
        print("--- [APEC] Navigating to Board ---")
        try:
            await self.page.goto(self.base_url)
            await self._handle_cookies()

        except Exception as e:
            print(f"Nav Error: {e}")

        return {
            "status": "on_homepage",
            "current_url": self.base_url
        }
            
        

    # --- NODE 3: Login (Simplified for brevity) ---
    async def login(self, state: JobApplicationState):
        if state.get("is_login"):
            return {"status": "already_logged_in"}
        try: 

            print("--- [APEC] Requesting Login ---")
            # In a real app, inject credentials via self.config, don't ask user to type
            await self.page.locator('a[aria-label="Mon espace"]').click()
            print("⚠ ACTION REQUIRED: You have 60s to log in manually...")
            await asyncio.sleep(60) 

            # Head to job search page after login
            await self.page.locator('[aria-label="menu"]').click()
            await self.page.locator('[href="/candidat.html"]').click()
        except Exception as e:
            print(f"Loggin Error: {e}")

        return {
            
            "status": "login_step_complete",
            "is_loggin": True
        }
    


    # --- NODE 4: Search ---
    async def search_jobs(self, state: JobApplicationState):
        # USE ENTITY: Get title from JobSearch
        search_entity = state["job_search"]
        job_title = search_entity.job_title
        
        print(f"🔎 [APEC] Searching for: {job_title}")       
        try:
            # Wait for results to load
            await self.page.locator('input[id="keywords"]').fill(job_title)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(5000)

            # If the specific result list wrapper appears, we know we are good
            try:
                await self.page.wait_for_selector('div[class="card card-offer mb-20 card--clickable card-offer--qualified"]', timeout=3000)
            except Exception:
                print("⚠️ No results found.")
                pass # Just proceed, maybe 0 results
                
            await self._handle_cookies()
        except Exception as e:
            print(f"Search Error: {e}")            
            
        return {
            "status": "on search page",
            "current_url": self.page.url
        }

    # --- NODE 5: Scrape Jobs (Integrated) ---
    async def get_matched_jobs(self, state: JobApplicationState):
        print("--- [APEC] Scraping Jobs ---")
        
        found_job_entities = []
        result_url = self.page.url
        
        # APEC Card Selector
        card_selector = 'div[class="card card-offer mb-20 card--clickable card-offer--qualified"]'
        
        try:
            # Wait for list
            cards = self.page.locator(card_selector)
            try:
                await cards.first.wait_for(state="visible", timeout=5000)
            except Exception:
                print("No cards found.")
                return {"found_raw_offers": []}

            count = await cards.count()
            limit = min(count, 3) # Keep your limit
            
            for i in range(limit):
                print(f"Processing card {i+1}/{limit}")
                
                # 1. Re-locate elements to avoid staleness
                cards = self.page.locator(card_selector)
                card = cards.nth(i)

                
                # 2. Extract Metadata (Before Click - Safer/Faster)                             
                
                raw_title = await self._get_job_attribute(card, 'h2[class="card-title"]', "Title Not Found")
                raw_company = await self._get_job_attribute(card, 'p[class="card-offer__company"]', "Company Name Not Found")
                raw_location = await self._get_job_attribute(card, 'li:has(img[alt="localisation"])', "Location Not Found")  
                

                # 3. Click & Load Detail View (in the side panel or new view)
                await card.click()
                await self.page.wait_for_timeout(1000)
                
                
                # 4. Check for "Easy Apply" Button
                # Your logic: If button missing -> Already applied. If button has 'to=int' -> Internal.

                try:
                    # Check for "Easy Apply" (APEC internal form)
                    await self.page.wait_for_selector('a[class="btn btn-primary ml-0"]', state="visible", timeout=5000)
                except Exception:                
                    print("Already applied to this job! Back to search result:")                  
                    await self.page.goto(result_url, wait_until="networkidle") 
                    continue 
                
                apply_btn = self.page.locator('a[class="btn btn-primary ml-0"]')
                    
                if await apply_btn.count() > 0:
                    href = await apply_btn.get_attribute("href")
                    
                    # APEC Logic: "to=int" means internal application form
                    if href and "to=int" in href:
                        print(f"✅ Internal offer found: {raw_title}")
                        
                        # Construct the full URL for the offer
                        # We store this so the Analyze/Submit node can go directly there
                        full_offer_url = f"https://www.apec.fr{href}"

                        print("Apply button found - application form available on the job board")
                        await self.page.goto(full_offer_url)
                        await self.page.wait_for_load_state("networkidle")
                        await self.page.wait_for_timeout(2000)


                        await self.page.wait_for_selector('button[title="Postuler"]', state="visible")
                        postule_btn = self.page.locator('button[title="Postuler"]')

                        if await postule_btn.count() > 0:
                            print("Heading to the application form page:")                               
                            await postule_btn.click()
                            
                            await self.page.wait_for_load_state("networkidle")
                            await self.page.wait_for_timeout(2000)

                            print(f"Valid application form at: {self.page.url}")   

                            # Create Domain Entity
                            offer = JobOffer(
                                url=full_offer_url,
                                form_url=self.page.url,
                                search_id=state["job_search"].id,
                                company_name=raw_company,
                                job_title=raw_title,
                                location=raw_location, 
                                job_board=JobBoard.APEC,
                                status=ApplicationStatus.FOUND,
                                followup_date=None
                            )
                           
                            found_job_entities.append(offer)
                
            
                # 5. Navigate back (if URL changed) or just ensure we are ready for next
                print("🔙 Returning to search results...")
                await self.page.goto(result_url, wait_until="networkidle")

        except Exception as e:
            print(f"Scraping Error: {e}")

        # Return the entities to the state buffer
        return {"found_raw_offers": found_job_entities}

    # --- NODE 6: Analyze (Optimized Flow) ---
    async def analyze_jobs(self, state: JobApplicationState):
        print("--- [APEC] Analyzing Jobs with Gemini ---")
        
        user_id = state["user"].id
        search_id = state["job_search"].id
        raw_offers = state["found_raw_offers"]

        # 1. OPTIMIZATION: Filter & Persist Raw Jobs FIRST
        print("🔍 Checking DB for duplicates...")
        pre_process_result = await self.results_processor.execute(user_id, search_id, raw_offers)
        
        if not pre_process_result.is_success:
            print(f"DB Error during pre-check: {pre_process_result.error.message}")
            return {"found_raw_offers": []}

        jobs_to_analyze = pre_process_result.value
        
        if not jobs_to_analyze:
            print("All jobs were duplicates. Skipping LLM.")
            return {"found_raw_offers": []}

        print(f"Optimization: Analyzing {len(jobs_to_analyze)} new jobs.")

        # 2. Prepare Resume
        resume_path = state["user"].resume_path
        resume_text = await asyncio.to_thread(self._extract_resume, resume_path)

        processed_offers = []

        # 3. LLM Loop
        for offer in jobs_to_analyze:
            print(f"🤖 Analyzing: {offer.job_title}")
            try:
                # A. Navigation
                await self.page.goto(offer.url, wait_until='networkidle')
                
                # B. Scrape Description (APEC Specific)
                try:
                    # Using the selector you provided
                    desc_element = self.page.locator('div[class="col-lg-8 border-L"]')
                    if await desc_element.count() > 0:
                        # We use inner_text for cleaner tokens, but inner_html is also fine if preferred
                        job_desc = await desc_element.inner_text()
                    else:
                        job_desc = await self.page.locator("body").inner_text() # Fallback
                except Exception:
                    job_desc = ""

                # C. Validation
                if len(job_desc) < 50:
                    print("⏩ Description too short, skipping.")
                    continue

                # D. LLM Call (YOUR SPECIFIC SYSTEM MESSAGE)
                system_message = SystemMessage(
                """
                    You're an excellent AI assistant that take a job description and a resume as input and 
                    generate a custom cover letter(in french, you should write the cover letter in 
                    french) and a ranking number from 1 to 10 describing how well the job matches 
                    the resume, with 1 meaning low matching and 10 the highest rank.

                    Task for a job application assistant:
                
                    Given a job description and resume, generate:
                    1. A cover letter in French: It should be very simple and concise three to four sentences max. It's just a custom message to introduce the candidate and highlight relevant skills for the job.
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
                
                # E. Parsing (Your Specific Logic)
                # Note: We adapt this to update the Entity, not a dict
                try:
                    data = json.loads(response.content[0]["text"])
                    
                    offer.cover_letter = data.get("cover_letter", "")
                    offer.ranking = int(data.get("ranking", 5))
                    
                    processed_offers.append(offer)
                except Exception as e:
                     print(f"JSON Parsing Error for job {offer.url}: {e}")

            except Exception as e:
                print(f"LLM Error: {e}")

        # 4. Sort & Save (Architecture Integration)
        if len(processed_offers) > 0:
            # Reorder in descending order according to the ranking
            processed_offers.sort(key=lambda x: x.ranking, reverse=True )
            print(f"Analysis complete: {len(processed_offers)} offers processed.\n\n{processed_offers}")
            return {"found_raw_offers": processed_offers}
        else:
            print("No offers processed after analysis.")
            return {"found_raw_offers": []}

    # --- NODE 7: Submit & Save (APEC) ---
    async def submit_applications(self, state: JobApplicationState):
        print("--- [APEC] Submitting Applications ---")
        
        # 1. Get Inputs from State
        jobs_to_submit = state.get("found_raw_offers", [])
        user = state["user"] # The User Entity


        if not jobs_to_submit:
            print("No jobs in submission queue.")
            return {"status": "no_jobs"}

        successful_submissions = []
        i = 0
        for offer in jobs_to_submit:
            print(f"📝 Applying to: {offer.job_title}: {i+1} out of {len(jobs_to_submit)}")
            try:
                # A. Navigation
                # In the Scrape node, we already constructed the full internal URL
                await self.page.goto(offer.form_url, wait_until='networkidle')
                await self.page.wait_for_timeout(2000)

                # B. CV Upload (APEC Specific Logic)
                # Handle "Importer un CV" radio button if present
                if await self.page.locator('.form-check:has-text("Importer un CV")').count() > 0:
                    await self.page.locator('label:has-text("Importer un CV")').click() 
                
                if user.resume_path:
                    await self.page.locator('input[type="file"]').set_input_files(user.resume_path)
                    # Uncheck "Save CV" to avoid cluttering the account
                    try:
                        await self.page.locator('input[formcontrolname="isCvSave"]').uncheck()
                    except Exception:
                        pass

                # C. Cover Letter (Accordion Logic)
                # APEC puts the cover letter inside a collapsed accordion
                try:
                    await self.page.wait_for_selector('a[aria-controls="collapseThree"]', state="visible")
                    anchor = self.page.locator('a[aria-controls="collapseThree"]').first
                    anchor_label = self.page.locator('div[id="headingThree"]').first
                    
                    # Click to expand
                    await anchor_label.click()
                    await self.page.wait_for_timeout(5000)
                    
                    # Robust check: ensure it actually expanded
                    val = await anchor.get_attribute('aria-expanded')
                    if val != 'true':
                        print("⚠ Accordion didn't open, clicking again...")
                        await anchor_label.click()

                    # Wait for container and Fill
                    await self.page.locator('#collapseThree').wait_for(state="visible")
                    if offer.cover_letter:
                        await self.page.locator('#comment').fill(offer.cover_letter)
                except Exception as e:
                    print(f"⚠ Could not fill cover letter: {e}")

                # D. Additional Data (Dropdowns Logic)
                # This section contains Education fields. We assume User Entity has these fields.
                try:
                    await self.page.wait_for_selector('a[aria-controls="#collapse_additionalData"]', state="visible")
                    anchor_sec = self.page.locator('a[aria-controls="#collapse_additionalData"]').first
                    anchor_sec_label = self.page.locator('div[id="heading_additionalData"]').first
                    
                    await anchor_sec_label.click()
                    await self.page.wait_for_timeout(5000)
                    
                    # Robust check
                    if await anchor_sec.get_attribute('aria-expanded') != 'true':
                        await anchor_sec_label.click()

                    # --- Angular Selectors ---
                    # 1. Study Level
                    if hasattr(user, 'study_level') and user.study_level:
                        await self.page.locator('ng-select[formcontrolname="idNiveauFormation"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.study_level}")').first.click()

                    # 2. Major / Discipline
                    if hasattr(user, 'major') and user.major:
                        await self.page.locator('ng-select[formcontrolname="idDiscipline"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.major}")').first.click()

                    # 3. School Type
                    if hasattr(user, 'school_type') and user.school_type:
                        await self.page.locator('ng-select[formcontrolname="idNatureFormation"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.school_type}")').first.click()

                    # 4. Graduation Year
                    if hasattr(user, 'graduation_year') and user.graduation_year:
                        await self.page.locator('ng-select[formcontrolname="anneeObtention"]').click()
                        await self.page.wait_for_selector('.ng-option', state="visible")
                        await self.page.locator(f'.ng-option-label:has-text("{user.graduation_year}")').first.click()
                
                except Exception as e:
                     print(f"⚠ Could not fill additional data (optional): {e}")

                # E. Human Verification
                print("⏳ Sleeping 30s for manual verification...")
                await asyncio.sleep(30) 

                # F. Submit
                submit_btn = self.page.locator('button[title="Envoyer ma candidature"]')
                if await submit_btn.is_visible():
                    await submit_btn.click() 
                    await self.page.wait_for_timeout(5000)
                    print("Application submitted")
                    
                    # Update Status
                    offer.status = ApplicationStatus.SUBMITTED
                    successful_submissions.append(offer)
                else:
                    print("❌ Submit button not visible.")

            except Exception as e:
                print(f"❌ Submission failed for {offer.url}: {e}")

        # 2. SAVE TO DATABASE
        if successful_submissions:
            print(f"💾 Saving {len(successful_submissions)} successful applications...")
            save_result = await self.results_saver.execute(successful_submissions)
            
            if save_result.is_success:
                print(f"🎉 Success: {save_result.value}")
            else:
                print(f"⚠ DB Save Error: {save_result.error.message}")

        return {"status": "batch_complete"}
    

    # --- NODE 7: Cleanup ---
    async def cleanup(self, state: JobApplicationState):
        print("--- [APEC] Cleanup ---")
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        return {"status": "finished"}

    # --- THE SUBGRAPH BUILDER ---
    def get_graph(self):
        workflow = StateGraph(JobApplicationState)
        
        # Add Nodes (Bound to self)
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