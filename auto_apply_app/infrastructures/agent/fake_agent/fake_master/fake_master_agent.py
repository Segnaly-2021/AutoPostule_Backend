# auto_apply_app/infrastructures/agent/fake_master.py

import asyncio
from typing import List
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_wttj_worker import FakeWTTJWorker
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_hw_worker import FakeHWWorker
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_apec_worker import FakeApecWorker


class FakeMasterAgent:
    """
    Orchestrates fake workers in parallel to search job boards.
    No authentication, no persistence - just demo scraping.

    Workload routing: the entire target is split between APEC and HelloWork.
    WTTJ is intentionally kept wired (the worker still exists and is import-safe)
    but receives a quota of 0 and is NOT launched. To re-enable it later, restore
    a non-zero quota and add its coroutine back into the gather() call.
    """

    def __init__(self):
        self.wttj_worker = FakeWTTJWorker()   # kept, but not fed any workload
        self.hw_worker = FakeHWWorker()
        self.apec_worker = FakeApecWorker()

    def _split_quota(self, target_count: int) -> dict:
        """
        Split the whole target between APEC and HelloWork only.

        APEC takes the remainder when target_count is odd (10/20/50 are all even,
        so in practice the split is exactly even, but this stays correct for any input).
        WTTJ is fixed at 0.
        """
        half = target_count // 2
        remainder = target_count % 2
        return {
            "apec": half + remainder,
            "hw": half,
            "wttj": 0,
        }

    async def search_all_boards(self, query: str, target_count: int) -> dict:
        """
        Run the active workers (APEC + HelloWork) in parallel and aggregate results.

        Returns:
            {
                "jobs": [JobSnippet.to_dict(), ...],
                "total_found": int,
                "boards_searched": ["APEC", "HELLOWORK"],
                "status": "success" | "error"
            }
        """
        print(f"\n🚀 [Fake Master] Starting parallel search for '{query}' (target: {target_count})")

        quotas = self._split_quota(target_count)
        print(f"📊 Quotas: APEC={quotas['apec']}, HW={quotas['hw']}, WTTJ={quotas['wttj']} (WTTJ disabled)")

        try:
            # Only APEC + HW are launched. WTTJ is deliberately excluded from gather().
            results = await asyncio.gather(
                self.apec_worker.search_jobs(query, quotas["apec"]),
                self.hw_worker.search_jobs(query, quotas["hw"]),
                return_exceptions=True  # one worker crashing must not sink the other
            )

            all_jobs = []
            for worker_result in results:
                if isinstance(worker_result, List):
                    all_jobs.extend(worker_result)
                else:
                    # Worker returned an exception (return_exceptions=True)
                    print(f"⚠️ Worker failed: {worker_result}")

            jobs_dict = [job.to_dict() for job in all_jobs]

            print(f"✅ [Fake Master] Total scraped: {len(jobs_dict)} jobs")

            return {
                "jobs": jobs_dict,
                "total_found": len(jobs_dict),
                "boards_searched": ["APEC", "HELLOWORK"],
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