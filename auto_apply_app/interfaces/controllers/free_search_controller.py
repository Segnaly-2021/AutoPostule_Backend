# auto_apply_app/interfaces/controllers/free_search_controller.py

from dataclasses import dataclass
from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.interfaces.presenters.base_presenter import FreeSearchPresenter
from auto_apply_app.infrastructures.agent.fake_agent.fake_master.fake_master_agent import FakeMasterAgent


@dataclass
class FreeSearchController:
    """
    Interface Adapter: Orchestrates free tier job search.
    No authentication, no persistence - just demo scraping.
    """
    fake_agent: FakeMasterAgent
    presenter: FreeSearchPresenter
    
    async def handle_search(self, query: str, target_count: int) -> OperationResult:
        """
        Execute a free search across all job boards.
        
        Args:
            query: Job search term (e.g., "Product Manager")
            target_count: Desired number of jobs (10, 20, or 50)
        
        Returns:
            OperationResult containing FreeSearchResultViewModel
        """
        try:
            # 1. Validate inputs
            if not query or not query.strip():
                return OperationResult.fail(
                    message="Search query cannot be empty",
                    code="VALIDATION_ERROR"
                )
            
            if target_count not in [10, 20, 50]:
                return OperationResult.fail(
                    message="Target count must be 10, 20, or 50",
                    code="VALIDATION_ERROR"
                )
            
            # 2. Execute search via fake agent
            print(f"[FreeSearchController] Initiating search: '{query}' (target: {target_count})")
            search_output = await self.fake_agent.search_all_boards(query, target_count)
            
            # 3. Check for agent-level errors
            if search_output.get("status") == "error":
                return OperationResult.fail(
                    message=search_output.get("error_message", "Search failed"),
                    code="SEARCH_ERROR"
                )
            
            # 4. Format results via presenter
            view_model = self.presenter.present_search_results(search_output)
            
            # 5. Return success with formatted data
            return OperationResult.succeed(value=view_model)
        
        except ValueError as e:
            return self._present_validation_exception(e)
        except Exception as e:
            print(f"[FreeSearchController] Unexpected error: {e}")
            return OperationResult.fail(
                message="An unexpected error occurred during search",
                code="INTERNAL_ERROR"
            )
    
    def _present_validation_exception(self, e: ValueError) -> OperationResult:
        """Maps validation errors to ViewModels."""
        error_vm = self.presenter.present_error(str(e), "VALIDATION_ERROR")
        return OperationResult.fail(error_vm.message, error_vm.code)