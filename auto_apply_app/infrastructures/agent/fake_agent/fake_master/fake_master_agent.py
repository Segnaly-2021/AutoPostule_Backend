# auto_apply_app/infrastructures/agent/fake_master.py

import asyncio
import logging
from typing import List
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_wttj_worker import FakeWTTJWorker
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_hw_worker import FakeHWWorker
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_apec_worker import FakeApecWorker

logger = logging.getLogger(__name__)


class FakeMasterAgent:
    """
    Orchestrates fake workers in parallel to search job boards.
    No authentication, no persistence - just demo scraping.

    Workload routing: the entire target goes to HelloWork, the only board that
    currently works. APEC (serves a bot-detection page to this worker) and WTTJ
    are intentionally kept wired (the workers still exist and are import-safe)
    but receive a quota of 0 and are NOT launched. To re-enable one later, give
    it a non-zero quota and add its coroutine back into the gather() call.
    """

    def __init__(self):
        self.wttj_worker = FakeWTTJWorker()   # kept, but not fed any workload
        self.hw_worker = FakeHWWorker()
        self.apec_worker = FakeApecWorker()   # kept, but not fed any workload

    def _split_quota(self, target_count: int) -> dict:
        """
        Route the whole target to HelloWork. APEC and WTTJ are fixed at 0.
        """
        return {
            "apec": 0,
            "hw": target_count,
            "wttj": 0,
        }

    async def search_all_boards(
        self,
        query: str,
        target_count: int,
        user_id: str = "unknown",
        user_name: str = "unknown",
    ) -> dict:
        """
        Run the active workers (currently HelloWork only) and aggregate results.

        user_id / user_name are debug attribution only — they identify who
        triggered the run in logs and never affect the search itself.

        Returns:
            {
                "jobs": [JobSnippet.to_dict(), ...],
                "total_found": int,
                "boards_searched": ["HELLOWORK"],
                "status": "success" | "error"
            }
        """
        print(f"\n🚀 [Fake Master] Starting parallel search for '{query}' (target: {target_count}) — run by {user_name} (user_id={user_id})")
        logger.info(
            "Fake agent run started by %s (user_id=%s) — query=%r, target=%d",
            user_name, user_id, query, target_count,
        )

        quotas = self._split_quota(target_count)
        print(f"📊 Quotas: HW={quotas['hw']}, APEC={quotas['apec']}, WTTJ={quotas['wttj']} (APEC + WTTJ disabled)")

        try:
            # Only HW is launched. APEC and WTTJ are deliberately excluded from gather().
            results = await asyncio.gather(
                self.hw_worker.search_jobs(query, quotas["hw"]),
                return_exceptions=True  # one worker crashing must not sink the others
            )

            all_jobs = []
            for worker_result in results:
                if isinstance(worker_result, List):
                    all_jobs.extend(worker_result)
                else:
                    # Worker returned an exception (return_exceptions=True)
                    print(f"⚠️ Worker failed: {worker_result}")

            jobs_dict = [job.to_dict() for job in all_jobs]

            print(f"✅ [Fake Master] Total scraped: {len(jobs_dict)} jobs)")
            logger.info(
                "Fake agent run finished for %s (user_id=%s) — %d jobs scraped",
                user_name, user_id, len(jobs_dict),
            )

            return {
                "jobs": jobs_dict,
                "total_found": len(jobs_dict),
                "boards_searched": ["HELLOWORK"],
                "status": "success"
            }

        except Exception as e:
            print(f"❌ [Fake Master] Fatal error: {e} — run by {user_name} (user_id={user_id})")
            logger.error(
                "Fake agent run failed for %s (user_id=%s): %s",
                user_name, user_id, e,
            )
            return {
                "jobs": [],
                "total_found": 0,
                "boards_searched": [],
                "status": "error",
                "error_message": str(e)
            }