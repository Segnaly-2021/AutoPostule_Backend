#import asyncio
from langgraph.graph import StateGraph, END

# --- PORTS & DOMAIN ---
from auto_apply_app.application.service_ports.agent_port import AgentServicePort
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.infrastructures.agent.state import JobApplicationState

# --- WORKERS (Type Hinting) ---
from auto_apply_app.infrastructures.agent.workers.wttj.wttj_worker import WelcomeToTheJungleWorker
from auto_apply_app.infrastructures.agent.workers.hellowork.hw_worker_v1 import HelloWorkWorker
from auto_apply_app.infrastructures.agent.workers.apec.apec_worker import ApecWorker

class MasterAgent(AgentServicePort):
    def __init__(
        self, 
        wttj_worker: WelcomeToTheJungleWorker,
        hellowork_worker: HelloWorkWorker,
        apec_worker: ApecWorker
    ):
        # 1. INJECTION: We receive the pre-configured workers (with their tools)
        self._wttj = wttj_worker
        self._hw = hellowork_worker
        self._apec = apec_worker

    def _route_request(self, state: JobApplicationState):
        """
        Decides which worker to dispatch based on the JobSearch entity.
        """
        # We access the Entity directly from the State
        search_mission = state["job_search"]


        job_board = search_mission.job_board.name.lower()

        print(f"--- [Master] Routing mission: '{job_board}' ---")

        if "apec" in job_board:
            return "apec_worker"
        elif "hellowork" in job_board:
            return "hellowork_worker"
        
        # Default fallback to WTTJ
        return "wttj_worker"

    def _handle_unknown(self, state: JobApplicationState):
        print("❌ Error: Could not determine a suitable worker.")
        return {"status": "failed_routing"}

    def get_graph(self):
        """
        Builds the Master Graph that orchestrates the workers.
        """
        workflow = StateGraph(JobApplicationState)

        # 2. Add Workers as Subgraphs (Nodes)
        # We call .get_graph() on the workers to embed their logic
        workflow.add_node("wttj_worker", self._wttj.get_graph())
        workflow.add_node("hellowork_worker", self._hw.get_graph())
        workflow.add_node("apec_worker", self._apec.get_graph())
        workflow.add_node("unknown_board", self._handle_unknown)

        # 3. Conditional Entry Point (The Router)
        workflow.set_conditional_entry_point(
            self._route_request,
            {
                "wttj_worker": "wttj_worker",
                "hellowork_worker": "hellowork_worker",
                "apec_worker": "apec_worker",
                "unknown_board": "unknown_board",
            }
        )

        # 4. Define Exits
        workflow.add_edge("wttj_worker", END)
        workflow.add_edge("hellowork_worker", END)
        workflow.add_edge("apec_worker", END)
        workflow.add_edge("unknown_board", END)

        return workflow.compile()

    async def run_job_search(self, user: User, search: JobSearch) -> None:
        """
        The Entry Point called by the Use Case.
        """
        print(f"🤖 Master Agent waking up for user: {user.email}")

        # 1. Initialize State with Domain Entities
        initial_state = JobApplicationState(
            user=user,
            job_search=search,
            found_raw_offers=[], # Empty buffer
            processed_offers=[], # Empty buffer
            current_url="",
            is_logged_in=False,
            status="starting"
        )

        # 2. Build the Graph
        app = self.get_graph()

        # 3. Execute the Workflow
        # This will route to the correct worker -> scrape -> use tool -> save
        await app.ainvoke(initial_state)
        
        print("✅ Master Agent finished mission.")