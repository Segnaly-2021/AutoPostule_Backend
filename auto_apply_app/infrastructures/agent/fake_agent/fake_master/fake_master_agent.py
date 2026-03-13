# auto_apply_app/infrastructures/agent/fake_master.py

import asyncio
from typing import List
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_wttj_worker import FakeWTTJWorker
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_hw_worker import FakeHWWorker
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_apec_worker import FakeApecWorker


class FakeMasterAgent:
    """
    Orchestrates multiple fake workers in parallel to search job boards.
    No authentication, no persistence - just demo scraping.
    """
    
    def __init__(self):
        self.wttj_worker = FakeWTTJWorker()
        self.hw_worker = FakeHWWorker()
        self.apec_worker = FakeApecWorker()
    
    async def search_all_boards(self, query: str, target_count: int) -> dict:
        """
        Run all workers in parallel and aggregate results.
        
        Args:
            query: Job search term (e.g., "Product Manager")
            target_count: Desired total number of jobs (10, 20, or 50)
        
        Returns:
            {
                "jobs": [JobSnippet.to_dict(), ...],
                "total_found": int,
                "boards_searched": ["WTTJ", "HELLOWORK", "APEC"],
                "status": "success" | "error"
            }
        """
        print(f"\n🚀 [Fake Master] Starting parallel search for '{query}' (target: {target_count})")
        
        # Calculate per-board quota (distribute evenly)
        per_board_quota = target_count // 3
        remainder = target_count % 3
        
        # Assign quotas (give remainder to first board)
        quotas = {
            "wttj": per_board_quota + remainder,
            "hw": per_board_quota,
            "apec": per_board_quota
        }
        
        #print(f"📊 Quotas: WTTJ={quotas['wttj']}, HW={quotas['hw']}, APEC={quotas['apec']}")
        
        try:
            # Run workers in parallel using asyncio.gather
            results = await asyncio.gather(
                self.wttj_worker.search_jobs(query, quotas["wttj"]),
                self.hw_worker.search_jobs(query, quotas["hw"]),
                self.apec_worker.search_jobs(query, quotas["apec"]),
                return_exceptions=True  # Don't fail if one worker crashes
            )
            
            # Aggregate results
            all_jobs = []
            for worker_result in results:
                if isinstance(worker_result, List):
                    all_jobs.extend(worker_result)
                else:
                    # Worker returned an exception
                    print(f"⚠️ Worker failed: {worker_result}")
            
            # Convert to dicts for JSON serialization
            jobs_dict = [job.to_dict() for job in all_jobs]
            
            print(f"✅ [Fake Master] Total scraped: {len(jobs_dict)} jobs")
            
            return {
                "jobs": jobs_dict,
                "total_found": len(jobs_dict),
                "boards_searched": ["WTTJ", "HELLOWORK", "APEC"],
                "status": "success"
            }
        
        except Exception as e:
            print(f"❌ [Fake Master] Fatal error: {e}")
            return {
                "jobs": [],
                "total_found": 0,
                "boards_searched": [],
                "status": "error",
                "error_message": str(e)
            }