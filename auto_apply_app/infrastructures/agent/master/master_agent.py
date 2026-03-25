# auto_apply_app/infrastructures/agent/master.py

import io # 🚨 Needed for RAM reading
import json
import asyncio
import traceback
from uuid import UUID
import pdfplumber
from typing import Callable, Optional, Dict, Any
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic


from auto_apply_app.application.service_ports.agent_port import AgentServicePort
from auto_apply_app.application.service_ports.file_storage_port import FileStoragePort
from auto_apply_app.domain.value_objects import ApplicationStatus, ClientType, JobBoard
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.board_credentials import BoardCredential
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.infrastructures.agent.state import JobApplicationState
from auto_apply_app.application.use_cases.job_offer_use_cases import CleanupUnsubmittedJobsUseCase
from auto_apply_app.application.use_cases.agent_use_cases import ConsumeAiCreditsUseCase, SaveJobApplicationsUseCase
from auto_apply_app.infrastructures.agent.workers.wttj.wttj_worker import WelcomeToTheJungleWorker
from auto_apply_app.infrastructures.agent.workers.hellowork.hw_worker_v1 import HelloWorkWorker
from auto_apply_app.infrastructures.agent.workers.apec.apec_worker import ApecWorker
from auto_apply_app.infrastructures.config import Config
from auto_apply_app.application.use_cases.agent_state_use_cases import GetAgentStateUseCase, ResetAgentUseCase



# --- Update __init__ ---
class MasterAgent(AgentServicePort):
    def __init__(
        self, 
        wttj_worker: WelcomeToTheJungleWorker,
        hellowork_worker: HelloWorkWorker,
        apec_worker: ApecWorker,
        api_keys: dict,
        file_storage: FileStoragePort,
        consume_credits_use_case: ConsumeAiCreditsUseCase,
        save_applications_use_case: SaveJobApplicationsUseCase,
        cleanup_unsubmitted_use_case: CleanupUnsubmittedJobsUseCase,
        get_agent_state: GetAgentStateUseCase,    # 🚨 NEW
        reset_agent_state: ResetAgentUseCase      # 🚨 NEW
    ):
        # Workers
        self._wttj = wttj_worker
        self._hw = hellowork_worker
        self._apec = apec_worker
        
        # Tools
        self.api_keys = api_keys
        self.consume_credits = consume_credits_use_case
        self.save_applications = save_applications_use_case
        self.cleanup_unsubmitted = cleanup_unsubmitted_use_case
        self.get_agent_state = get_agent_state       # 🚨 NEW
        self.reset_agent_state = reset_agent_state   # 🚨 NEW
        self._checkpointer = None
        
        self._active_workers: Dict[str, Any] = {}
        self.file_storage = file_storage
        self._progress_callback = None



    # --- MISSING HELPER 1: The Master's Brain ---
    def _get_llm(self, preferences: UserPreferences) -> BaseChatModel:
        provider = getattr(preferences, "ai_model", "gemini").lower()
        temp = preferences.llm_temperature
        print(f"🧠 [Master] Booting up LLM Brain: {provider.upper()}")

        if provider in ["gpt", "openai"]:

            return ChatOpenAI(
                api_key=self.api_keys.get("openai"), 
                model="gpt-5.4", 
                temperature=temp
            )
        
        elif provider in ["claude", "anthropic"]:

            return ChatAnthropic(
                api_key=self.api_keys.get("anthropic"), 
                model="claude-sonnet-4-6", 
                temperature=temp
            )
        
        else:
            return ChatGoogleGenerativeAI(
                api_key=self.api_keys.get("gemini"), 
                model="gemini-3.1-pro-preview", 
                temperature=temp
            )


    # --- HELPER: Resume Extraction ---
    def _extract_resume(self, resume_bytes: bytes) -> str:
        text = ""
        try:
            if not resume_bytes:
                return ""
            # 🚨 pdfplumber reads from RAM using io.BytesIO!
            with pdfplumber.open(io.BytesIO(resume_bytes)) as pdf:
                for p in pdf.pages:
                    text += p.extract_text() + "\n"
        except Exception as e:
            print(f"Error reading resume: {e}")
        return text
    
    # --- HELPER: Unified Explicit Emit (Master Agent) ---
    async def _emit(self, state: JobApplicationState, stage: str, status: str = "in_progress", error: str = None):
        """Master Agent explicit progress emitter."""
        if not self._progress_callback:
            return
        try:
            search_id = str(state["job_search"].id) if "job_search" in state else ""
            
            await self._progress_callback({
                "source": "MASTER",
                "stage": stage,
                "node": "master", 
                "status": "error" if error else status,
                "error": error,
                "search_id": search_id
            })
        except Exception:
            pass

    # --- NODE 1: The Scrape Dispatcher ---
    async def dispatch_scrape(self, state: JobApplicationState):
        """
        Reads user preferences, calculates job limits per board, 
        and dynamically spins up ONLY the active workers.
        """
        await self._emit(state, "Launching Search Workers")

        print("--- [Master] Dispatching Scrape Missions ---")
        
        # 1. Get Active Boards
        # Assuming preferences has a list like [JobBoard.APEC, JobBoard.WTTJ]
        active_boards = [board for board, is_active in state["preferences"].active_boards.items() if is_active]
        
        if not active_boards:
            print("⚠️ No active job boards selected in preferences.")
            return [] # Returns nothing, ending the graph safely

        # 2. Calculate the Workload Split (Your math!)
        max_jobs = state.get("max_jobs", 20) # Default to 20 for Basic if not set
        worker_limit = max(1, max_jobs // len(active_boards))
        remainder = max_jobs % len(active_boards)
        
        print(f"📊 Workload: {max_jobs} max jobs split across {len(active_boards)} boards "
              f"({worker_limit} jobs per worker).")

        # 3. Build the Dynamic Worker Commands
        # We loop through the active boards and prepare a custom state packet for each.
        sends = []
        
        for board in active_boards:
            # Create a localized state dictionary for the worker
            worker_state = {
                **state, # Copy the global state
                "action_intent": "SCRAPE",     # 🚨 Tell it which track to take!
                "worker_job_limit": worker_limit # 🚨 Pass the calculated limit
            }
            
            board_name = board.lower()
            
            if "apec" in board_name:
                print("🚀 Launching APEC Worker...")
                sends.append(Send("apec_worker", worker_state))
                
            elif "hellowork" in board_name:
                print("🚀 Launching HelloWork Worker...")
                worker_state = {
                **state, # Copy the global state
                "action_intent": "SCRAPE",     
                "worker_job_limit": worker_limit + remainder 
                }
                sends.append(Send("hellowork_worker", worker_state))
                
            elif "wttj" in board_name:
                print("🚀 Launching WTTJ Worker...")
                sends.append(Send("wttj_worker", worker_state))
                
        # 4. Fire them off in parallel!
        return sends

    # --- NODE 2: The Brain ---
    async def analyze_and_generate(self, state: JobApplicationState):

        await self._emit(state, "AI Generating Cover Letters")

        print("--- [Master Brain] Analyzing Jobs with LLM ---")       
        
        user_id = state["user"].id
        raw_offers = state.get("found_raw_offers", [])
        
        if not raw_offers:
            print("⚠️ No raw jobs were found by any worker.")
            return {"error": "No jobs were found across your active boards today."}

        # 1. Pre-Check AI Credits
        jobs_count = len(raw_offers)
        print(f"🧠 Orchestrator received {jobs_count} new jobs for analysis.")
        
        subscription = state.get("subscription")
        if not subscription:
             return {"error": "Could not verify your subscription status."}
             
        if not subscription.has_sufficient_credits(jobs_count):
             return {"error": "You are out of AI Credits for this billing cycle. Please upgrade or wait for your credits to replenish."}
             
        # If they have some credits, but not enough for all jobs, slice the list!
        jobs_to_analyze = raw_offers
        if subscription.ai_credits_balance < jobs_count:
             print(f"⚠ Low balance! Only analyzing {subscription.ai_credits_balance} out of {jobs_count} jobs.")
             jobs_to_analyze = raw_offers[:subscription.ai_credits_balance]

        daily_limit = subscription.daily_limit

        # 2. Prepare Resume & LLM
        # (Assuming you moved the _extract_resume helper to the MasterAgent)
        resume_path = state["user"].resume_path

        # 🚨 Fetch bytes from Cloud Storage into RAM
        resume_bytes = await self.file_storage.download_file(resume_path)

        resume_text = await asyncio.to_thread(self._extract_resume, resume_bytes)
        system_messages = {
            "wttj": SystemMessage(
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
            ),

            "apec": SystemMessage(
                """
                    You're an excellent AI assistant that takes a job description and a resume as input.
                    Generate a custom cover letter (in French) and a ranking number from 1 to 10.
                    
                    Task:
                    1. A cover letter in French: Simple, concise, 3-4 sentences max.
                    2. A ranking (1-10) indicating job fit.
                    
                    CRITICAL: Return ONLY valid JSON. Your response must start with { and end with }.
                    {
                    "cover_letter": "text here",
                    "ranking": 8
                    }
                    Do NOT wrap the JSON in ```json or ``` markers.
                """
            ),

            "hellowork": SystemMessage(
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
            ),
        }

        llm = self._get_llm(state["preferences"])
        processed_offers = []

        # 3. The Global LLM Loop
        for offer in jobs_to_analyze:
            print(f"🤖 Generating Cover Letter for [{offer.job_board.name}]: {offer.job_title}")
            
            # Validation: Did the worker successfully grab the description?
            if not offer.job_desc or len(offer.job_desc) < 50:
                print(f"⏩ Description too short for {offer.url}, skipping LLM.")
                continue

            try:                
                prompt = HumanMessage(content=f"""
                Job Description: {offer.job_desc}
                Resume: {resume_text}        
                """)

                # Call LLM
                response = await llm.ainvoke([system_messages[str(offer.job_board.name).lower()], prompt])
                
                # Parse JSON
                try:
                    data = json.loads(response.content[0]["text"])
                    
                    offer.cover_letter = data.get("cover_letter", "")
                    offer.ranking = int(data.get("ranking", 5))

                    offer.status = ApplicationStatus.GENERATED if subscription.account_type == ClientType.PREMIUM else ApplicationStatus.APPROVED
                                        
                    processed_offers.append(offer)
                except Exception as e:
                     print(f"JSON Parsing Error for job {offer.url}: {e}")

            except Exception as e:
                print(f"LLM Error for {offer.job_title}: {e}")

        # 🚨 CIRCUIT BREAKER: All LLM calls failed
        if not processed_offers:
            return {"error": "Our AI engine couldn't generate valid cover letters. Please try again later."}

        # 4. Global Sorting (Highest ranking jobs first!)
        processed_offers.sort(key=lambda x: x.ranking, reverse=True)
        print(f"📈 Sorted {len(processed_offers)} generated jobs by AI ranking.")

        # 5. Deduct Credits Centrally
        credits_to_deduct = len(processed_offers)
        print(f"💳 Deducting {credits_to_deduct} credits...")
        
        billing_result = await self.consume_credits.execute(user_id=user_id, amount=credits_to_deduct)
        if not billing_result.is_success:
            return {"error": "A billing error occurred while processing your AI credits. Please contact support."}
        

        if daily_limit < len(processed_offers):
            processed_offers = processed_offers[:daily_limit]
        
        # 6. Save Drafts Centrally
        print(f"💾 Saving {len(processed_offers)} drafts to database...")
        save_result = await self.save_applications.execute(processed_offers)
        if not save_result.is_success:
            print(f"⚠ Error saving drafts: {save_result.error.message}")

        # Return to State
        return {
            "processed_offers": processed_offers
        }
    


    

    # --- NODE 4: The Submit Dispatcher ---
    def dispatch_submit(self, state: JobApplicationState):
       
        """Groups approved jobs by board and launches workers in parallel for submission."""
        print("--- [Master] Dispatching Submit Missions ---")

        processed_offers = state.get("processed_offers", [])

        # We only want to dispatch workers for jobs that are actually approved
        approved_jobs = [job for job in processed_offers if job.status == ApplicationStatus.APPROVED]

        if not approved_jobs:
            print("⚠️ No approved jobs found in the queue. Ending graph.")
            return [] # Returns empty list, safely ending the graph

        # Figure out which boards we actually need to spin up workers for
        boards_needed = set(job.job_board for job in approved_jobs)

        sends = []

        for board in boards_needed:
            # Create a localized state dictionary for the worker
            # Note: We pass the entire state, the worker's node handles filtering 
            # for its specific board internally.
            worker_state = {
            **state, 
            "action_intent": "SUBMIT", # 🚨 The magic switch!
            }

            if board == JobBoard.APEC:
                print("🚀 Launching APEC Worker for Submissions...")
                sends.append(Send("apec_worker", worker_state))

            elif board == JobBoard.HELLOWORK:
                print("🚀 Launching HelloWork Worker for Submissions...")
                sends.append(Send("hellowork_worker", worker_state))

            elif board == JobBoard.WTTJ:
                print("🚀 Launching WTTJ Worker for Submissions...")
                sends.append(Send("wttj_worker", worker_state))

        return sends
    


    # --- NODE 5: The Final Save ---
    async def finalize_batch(self, state: JobApplicationState):
        """Final cleanup and state synchronization."""
        await self._emit(state, "Saving Final Results")
        print("--- [Master] Finalizing Batch ---")
        
        user_id = state["user"].id
        
        # 🚨 1. CHECK KILL SWITCH BEFORE RESETTING
        is_killed = False
        try:
            state_result = await self.get_agent_state.execute(user_id)
            if state_result.is_success and state_result.value.is_shutdown:
                is_killed = True
        except Exception:
            pass

        # 2. GRACEFUL EXIT RESET
        await self.reset_agent_state.execute(user_id)
        
        # 3. Persist Successful Submissions
        submitted_offers = state.get("submitted_offers", [])
        if submitted_offers:
            print(f"💾 Persisting {len(submitted_offers)} submitted applications...")
            save_result = await self.save_applications.execute(submitted_offers)
            if not save_result.is_success:
                print(f"⚠ Error updating DB with final statuses: {save_result.error.message}")
        else:
             print("⚠️ No applications were successfully submitted.")

        # 4. THE NEW CLEANUP PHASE
        search_id = state["job_search"].id
        print(f"🧹 Sweeping database for leftover APPROVED (failed) jobs for search {search_id}...")
        
        cleanup_result = await self.cleanup_unsubmitted.execute(search_id)
        
        if cleanup_result.is_success:
            deleted = cleanup_result.value.get("deleted_count", 0)
            print(f"✅ Cleanup complete. Deleted {deleted} zombie job offers.")
        else:
            print(f"⚠ Database cleanup failed: {cleanup_result.error.message}")

        # 🚨 5. Return the exact status for the final router
        return {"status": "killed" if is_killed else "finished_successfully"}
    

    
    async def worker_return_router(self, state: JobApplicationState):
        """Catches returning workers and routes them to the correct Master phase."""
        # 1. Check for Kill Switch first!
        try:
            state_result = await self.get_agent_state.execute(state["user"].id)
            if state_result.is_success and state_result.value.is_shutdown:
                print("🛑 [Master] Kill switch detected! Aborting orchestrator and routing to Finalize.")
                return "finalize"
        except Exception:
            pass # Failsafe

        # 2. Standard routing
        intent = state.get("action_intent", "SCRAPE")
        if intent == "SUBMIT":
            print("🛬 [Master] Workers returned from SUBMIT. Routing to Finalize.")
            return "finalize"
            
        print("🛬 [Master] Workers returned from SCRAPE. Routing to Analyze.")
        return "analyze"
    

    # --- 1. THE ROUTER (Conditional Edge) ---
    async def route_review(self, state: JobApplicationState):       
        """Routes to human review if Premium, else bypasses straight to submit."""
        # 1. Check for Kill Switch (in case they stopped it during the LLM phase)
        try:
            state_result = await self.get_agent_state.execute(state["user"].id)
            if state_result.is_success and state_result.value.is_shutdown:
                print("🛑 [Master] Kill switch detected! Skipping review/submission. Routing to Finalize.")
                return "finalize"
        except Exception:
            pass # Failsafe

        # 2. Standard routing
        subscription = state.get("subscription")
        if subscription and subscription.account_type == ClientType.PREMIUM:
            print("⏸️ Premium User: Routing to manual review node.")
            return "human_review"
            
        print("▶️ Basic User: Auto-approved. Bypassing review.")
        return "prepare_submit"

    # --- 2. THE PAUSE POINT (Dummy Node) ---
    async def human_review(self, state: JobApplicationState):
        """
        Dummy node serving strictly as the LangGraph interruption point.
        Execution pauses BEFORE this node runs.
        """
        await self._emit(state, "Waiting for User Review")
        print("👤 [Master] Manual review approved. Resuming workflow...")
        return  {"status": "human_review_complete"}

    # --- 3. THE HUB (Dummy Node) ---
    async def prepare_submit(self, state: JobApplicationState):
        """
        Unified launchpad for the Send() fan-out to workers.
        """
        await self._emit(state, "Launching Submission Workers")
        print("🚀 [Master] Preparing to dispatch submission workers...")
        return {"status": "ready_for_submission"}



    # --- TERMINAL NODE 1: Success Notification ---
    async def completion_notification(self, state: JobApplicationState):
        """Dummy node to notify the frontend to hit 100% and close the overlay."""
        print("🎉 [Master] Emitting final completion signal.")
        await self._emit(state, stage="Job Search Complete", status="finished")
        return {"status": "finished"}

    # --- TERMINAL NODE 2: Killed Notification ---
    async def stop_agent_notification(self, state: JobApplicationState):
        """Dummy node to notify the frontend that the 90s spinner can be safely cleared."""
        print("🛑 [Master] Emitting agent killed signal.")
        await self._emit(state, stage="Agent has been stopped", status="killed")
        return {"status": "killed"}


    def route_end(self, state: JobApplicationState):
        """Routes from finalize to the correct terminal notification node."""
        if state.get("status") == "killed":
            return "stop_agent_notification"
        return "completion_notification"


    def get_graph(self):
        """Builds the Hub-and-Spoke Master Orchestrator."""
        workflow = StateGraph(JobApplicationState)

        # 1. Register the Spokes (Sub-Graphs)
        workflow.add_node("apec_worker", self._apec.get_graph())
        workflow.add_node("hellowork_worker", self._hw.get_graph())
        workflow.add_node("wttj_worker", self._wttj.get_graph())

        # 2. Register the Hub (Master Nodes)
        workflow.add_node("analyze", self.analyze_and_generate)
        workflow.add_node("human_review", self.human_review) 
        workflow.add_node("prepare_submit", self.prepare_submit) 
        workflow.add_node("finalize", self.finalize_batch)
        
        # 🚨 Register New Terminal Nodes
        workflow.add_node("completion_notification", self.completion_notification)
        workflow.add_node("stop_agent_notification", self.stop_agent_notification)
        
        # --- THE WORKFLOW ROUTING ---

        # 🛫 PHASE 1: The Scrape Fan-Out
        workflow.add_conditional_edges(
            START, 
            self.dispatch_scrape, 
            ["apec_worker", "hellowork_worker", "wttj_worker"] 
        )

        # 🛬 PHASE 2 & 4: The Synchronized Fan-In (Catches both returns)
        workflow.add_conditional_edges("apec_worker", self.worker_return_router, ["analyze", "finalize"])
        workflow.add_conditional_edges("hellowork_worker", self.worker_return_router, ["analyze", "finalize"])
        workflow.add_conditional_edges("wttj_worker", self.worker_return_router, ["analyze", "finalize"])

        # 🧠 PHASE 3A: Brain -> Review Router
        workflow.add_conditional_edges(
            "analyze", 
            self.route_review, 
            ["human_review", "prepare_submit", "finalize"]
        )
        
        # 👤 PHASE 3B: Connect Human Review to the Launchpad
        workflow.add_edge("human_review", "prepare_submit")

        # 📤 PHASE 3C: Launchpad -> Submit Fan-Out
        workflow.add_conditional_edges(
            "prepare_submit", 
            self.dispatch_submit, 
            ["apec_worker", "hellowork_worker", "wttj_worker"]
        )

        # 🏁 PHASE 5: The End Routing
        # Instead of going straight to END, we route to our notifications
        workflow.add_conditional_edges(
            "finalize",
            self.route_end,
            ["completion_notification", "stop_agent_notification"]
        )
        
        # Both notifications safely terminate the graph
        workflow.add_edge("completion_notification", END)
        workflow.add_edge("stop_agent_notification", END)

        return workflow.compile(
            checkpointer=self._checkpointer,
            interrupt_before=["human_review"] 
        )
    
    async def run_job_search(
        self, 
        user: User, 
        search: JobSearch,
        subscription: UserSubscription,
        preferences: UserPreferences,
        credentials: Optional[Dict[str, BoardCredential]] = None,
        progress_callback: Optional[Callable] = None
    ) -> None:
        print(f"🤖 Master Agent waking up for user: {user.email}")

        # 🚨 FORCE CLEAN SLATE ON BOOT
        await self.reset_agent_state.execute(user.id)

        # 🚨 UPDATE: Added new state properties
        initial_state = JobApplicationState(
            user=user,
            job_search=search,
            subscription=subscription,
            preferences=preferences,
            credentials=credentials,
            max_jobs=10 if "BASIC" in subscription.account_type.name else 60, # Inject limits!
            worker_job_limit=0,
            found_raw_offers=[],
            processed_offers=[],
            submitted_offers=[], # New Outbox
            current_url="",
            is_logged_in=False,
            status="starting"
        )
        print(f"📊 Initial State prepared with user preferences and subscription details {initial_state}.")

        # 🚨 UPDATE: Register ALL active workers for this search
        active_instances = []
        for board, is_active in preferences.active_boards.items():
            if is_active:
                active_instances.append(self._get_worker_for_board(board.lower()))

        self._active_workers[str(search.id)] = active_instances
        print(f"🚀 Registered {len(active_instances)} active workers for search {search.id}: {[type(w).__name__ for w in active_instances]}")
    
        # Set callback on all workers for this run
        self._progress_callback = progress_callback
        self._wttj._progress_callback = progress_callback
        self._hw._progress_callback = progress_callback
        self._apec._progress_callback = progress_callback

        try:
            await self._execute_with_progress(initial_state, search.id)
        finally:
            # Clear callbacks after run — prevents stale references
            self._progress_callback = None
            self._wttj._progress_callback = None
            self._hw._progress_callback = None
            self._apec._progress_callback = None
            self._active_workers.pop(str(search.id), None)




    async def kill_job_search(self, search_id: UUID) -> None:
        print(f"🛑 KILLING job search {search_id}")
        
        workers = self._active_workers.get(str(search_id), [])
        
        if workers:
            for worker in workers:
                try:
                    await worker.force_cleanup()
                except Exception as e:
                    print(f"⚠️ Cleanup error on worker: {e}")
            print(f"✅ All parallel workers cleaned up for {search_id}")
            self._active_workers.pop(str(search_id), None)
        else:
            print(f"⚠️ No active workers found for {search_id}")




    def _get_worker_for_board(self, job_board: str):
        """Helper to get the correct worker instance based on job board."""
        if "apec" in job_board:
            return self._apec
        elif "hellowork" in job_board:
            return self._hw
        return self._wttj
    


    async def _execute_with_progress(
        self, 
        initial_state: JobApplicationState, 
        search_id: UUID
        # 🚨 Removed progress_callback param
    ):
        if not self._checkpointer:
            self._checkpointer = await Config.get_checkpointer()

        app = self.get_graph()
        config = {"configurable": {"thread_id": f"search_{search_id}"}}

        try: 
            # 🚨 Nodes emit on their own now, just let the graph run!
            async for _ in app.astream(initial_state, config, subgraphs=True):
                pass 

        except Exception:
            print("🚨 FATAL GRAPH ERROR:")
            traceback.print_exc()



    async def resume_job_search(
        self,
        user: User,
        search: JobSearch,
        preferences: UserPreferences, # Need this to know which boards to register
        progress_callback: Optional[Callable] = None
    ) -> None:
        print(f"🔄 Resuming job search {search.id} for user: {user.email}")

        # 🚨 FORCE CLEAN SLATE ON BOOT
        await self.reset_agent_state.execute(user.id)
        
        if not self._checkpointer:
            self._checkpointer = await Config.get_checkpointer()

        app = self.get_graph()
        config = {"configurable": {"thread_id": f"search_{search.id}"}}
        

        # Register workers for the Submit phase
        active_instances = []
        for board, is_active in preferences.active_boards.items():
            if is_active:
                worker = self._get_worker_for_board(board.lower())
                worker._progress_callback = progress_callback # 🚨 Assign callback to worker
                active_instances.append(worker)

        self._active_workers[str(search.id)] = active_instances
        self._progress_callback = progress_callback # 🚨 Assign callback to Master
        
        try:
            # 🚨 Let LangGraph run freely
            async for _ in app.astream(None, config, subgraphs=True):
                pass
        finally:
            # 🚨 Cleanup
            self._progress_callback = None
            for worker in active_instances:
                worker._progress_callback = None
            self._active_workers.pop(str(search.id), None)