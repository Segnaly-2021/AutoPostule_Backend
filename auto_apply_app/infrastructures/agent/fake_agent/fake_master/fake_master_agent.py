# auto_apply_app/infrastructures/agent/fake_master.py

import asyncio
import logging
from typing import List
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_wttj_worker import FakeWTTJWorker
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_hw_worker import FakeHWWorker
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_apec_worker import FakeApecWorker
from auto_apply_app.infrastructures.agent.fake_agent.fake_workers.fake_indeed_worker import FakeIndeedWorker

logger = logging.getLogger(__name__)


class FakeMasterAgent:
    """
    Orchestrates fake workers in parallel to search job boards.
    No authentication, no persistence - just demo scraping.

    Workload routing: the target is split between Indeed and HelloWork.
    Indeed's share is capped at INDEED_MAX because anonymous users only get
    page 1 (page 2 redirects to sign-in); HelloWork takes the rest.

    APEC and WTTJ are kept wired (workers exist and are import-safe) but get a
    quota of 0 and are NOT launched. To re-enable one, give it a non-zero quota
    and add its coroutine back into the gather() call.
    """

    INDEED_MAX = 15  # anonymous Indeed stops at page 1 (~15 cards)

    def __init__(self):
        self.wttj_worker = FakeWTTJWorker()   # kept, but not fed any workload
        self.apec_worker = FakeApecWorker()   # kept, but not fed any workload
        self.hw_worker = FakeHWWorker()
        self.indeed_worker = FakeIndeedWorker()

    def _split_quota(self, target_count: int) -> dict:
        """
        Indeed takes half (plus the remainder if odd), capped at INDEED_MAX.
        HelloWork takes everything else. APEC and WTTJ are fixed at 0.

        10 -> Indeed 5 / HW 5;  20 -> 10 / 10;  50 -> 15 / 35.
        """
        indeed = min(target_count // 2 + target_count % 2, self.INDEED_MAX)
        return {
            "indeed": indeed,
            "hw": target_count - indeed,
            "apec": 0,
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
        Run the active workers (Indeed + HelloWork) in parallel and aggregate results.

        user_id / user_name are debug attribution only — they identify who
        triggered the run in logs and never affect the search itself.

        Returns:
            {
                "jobs": [JobSnippet.to_dict(), ...],
                "total_found": int,
                "boards_searched": ["INDEED", "HELLOWORK"],
                "status": "success" | "error"
            }
        """
        print(f"\n🚀 [Fake Master] Starting parallel search for '{query}' (target: {target_count}) — run by {user_name} (user_id={user_id})")
        logger.info(
            "Fake agent run started by %s (user_id=%s) — query=%r, target=%d",
            user_name, user_id, query, target_count,
        )

        quotas = self._split_quota(target_count)
        print(f"📊 Quotas: Indeed={quotas['indeed']}, HW={quotas['hw']} (APEC, WTTJ disabled)")

        try:
            # Only Indeed + HW are launched. APEC and WTTJ are deliberately excluded.
            results = await asyncio.gather(
                self.indeed_worker.search_jobs(query, quotas["indeed"]),
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

            print(f"✅ [Fake Master] Total scraped: {len(jobs_dict)} jobs)")
            logger.info(
                "Fake agent run finished for %s (user_id=%s) — %d jobs scraped",
                user_name, user_id, len(jobs_dict),
            )

            return {
                "jobs": jobs_dict,
                "total_found": len(jobs_dict),
                "boards_searched": ["INDEED", "HELLOWORK"],
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