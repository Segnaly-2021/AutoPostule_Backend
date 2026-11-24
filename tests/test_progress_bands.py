# ==============================================================================
# FILE: tests/test_progress_bands.py
#
# WHAT IT DOES:
#   Protects submit progress reporting: the right track per plan, a steady climb,
#   and 100% only on the last job. Also inspects worker source with `ast`.
#
# USED BY:
#   - pytest: test entry point; nothing imports it
#
# EXTERNAL DEPENDENCIES:
#   - pytest: test runner and fixtures
# ==============================================================================

"""The progress bar must climb steadily and only fill on the last job."""
import ast
import asyncio
from pathlib import Path

import pytest

from auto_apply_app.infrastructures.agent.runner import AgentRunner
from auto_apply_app.infrastructures.agent.stage_codes import progress_for, track_for

WORKERS = Path("auto_apply_app/infrastructures/agent/workers")
WORKER_FILES = ["apec/apec_worker.py", "hellowork/hw_worker.py", "wttj/wttj_worker.py"]


class _Broker:
    def __init__(self):
        self.percents = []

    async def publish(self, _sid, event):
        self.percents.append(event["progress_percent"])


def _run_submissions(is_premium):
    broker = _Broker()
    callback = AgentRunner(None, broker, None, None)._make_callback("sid")
    track = track_for("SUBMIT", is_premium)

    async def go():
        for done in (1, 2):
            for source in ("APEC", "WTTJ", "HELLOWORK"):
                await callback({
                    "source": source, "count_band": "submit", "count_done": done,
                    "count_total": 6, "progress_track": track, "is_premium": is_premium,
                })

    asyncio.run(go())
    return broker.percents


def test_basic_submit_stays_on_the_launch_track():
    assert track_for("SUBMIT", False) == "launch"
    assert progress_for("submit", "launch", False) == 65


def test_premium_submit_has_its_own_track():
    assert track_for("SUBMIT", True) == "submit"
    assert track_for("SCRAPE", True) == "launch"


@pytest.mark.parametrize("is_premium", [False, True])
def test_submit_progress_rises_and_fills_only_on_the_last_job(is_premium):
    percents = _run_submissions(is_premium)
    assert all(a < b for a, b in zip(percents, percents[1:]))
    assert percents[-1] == 95
    assert percents[-2] < 95


@pytest.mark.parametrize("rel", WORKER_FILES)
def test_submit_total_is_the_fixed_denominator(rel):
    tree = ast.parse((WORKERS / rel).read_text(encoding="utf-8"))
    submit = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "submit_applications"
    )
    totals = [
        kw.value for node in ast.walk(submit) if isinstance(node, ast.Call)
        for kw in node.keywords if kw.arg == "count_total"
    ]
    assert len(totals) == 1
    source = ast.unparse(totals[0])
    assert "submit_total" in source
    assert "ApplicationStatus" not in source
    assigned = {n.id for n in ast.walk(submit) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    used = {n.id for n in ast.walk(totals[0]) if isinstance(n, ast.Name)}
    assert used - {"state"} <= assigned
