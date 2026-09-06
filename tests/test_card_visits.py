"""How a page of results gets worked.

The signature this removes: the scrape loop walked `for i in range(count)`, so
every page of every run was visited 1,2,3,...,n in order. Ordering is free to
randomise — every card on a filtered results page already matches the search, so
the sequence between them carries no information for the board or for us.

The invariant that makes it safe is that skipping DEFERS rather than drops: the
order returned is always a permutation of range(count), so no offer is lost and
the worker's quota is unaffected.
"""
import ast
import collections
import statistics
from pathlib import Path

import pytest

from auto_apply_app.infrastructures.agent.human_behavior import plan_card_visit_order

APEC = Path("auto_apply_app/infrastructures/agent/workers/apec/apec_worker.py")


@pytest.mark.parametrize("count", [1, 2, 5, 20, 50])
def test_every_card_is_visited_exactly_once(count):
    """The whole scheme rests on this: shuffling must never lose an offer."""
    for _ in range(200):
        order, _ = plan_card_visit_order(count)
        assert sorted(order) == list(range(count))


def test_cards_passed_over_are_visited_last_not_dropped():
    for _ in range(300):
        order, passed_over = plan_card_visit_order(20)
        if not passed_over:
            continue
        assert set(order[-len(passed_over):]) == passed_over
        assert passed_over.issubset(set(order))


def test_the_order_actually_changes_between_pages():
    """Two pages scraped in the same sequence would defeat the point."""
    seqs = {tuple(plan_card_visit_order(20)[0]) for _ in range(500)}
    assert len(seqs) > 450, f"only {len(seqs)} distinct orders in 500 draws"


def test_the_walk_starts_near_the_top_but_not_always_at_the_first_card():
    """A person opens something near the top of the results, not a card chosen
    uniformly from the whole page — but not index 0 every single time either."""
    firsts = collections.Counter(plan_card_visit_order(20)[0][0] for _ in range(3000))
    assert firsts[0] < 3000 * 0.6, "always starts on the first card"
    assert sum(v for k, v in firsts.items() if k < 5) > 3000 * 0.8, (
        f"starts too far down the page: {sorted(firsts.items())[:8]}"
    )


def test_consecutive_visits_stay_near_each_other_on_the_page():
    """The regression guard for BOTH failure modes at once.

    A uniform shuffle scores ~7.0 here on a 20-card page: every step is a
    scroll teleport across the list, which is as unnatural as the sequential
    sweep it replaced. A plain 1,2,3,... walk scores exactly 1.0. Real reading
    sits between the two.
    """
    steps = []
    for _ in range(400):
        order, _ = plan_card_visit_order(20)
        steps += [abs(b - a) for a, b in zip(order, order[1:])]

    mean_step = statistics.mean(steps)
    assert mean_step < 5.0, f"visits jump around the page: mean step {mean_step:.1f}"
    assert mean_step > 1.5, f"collapsed into a sequential sweep: mean step {mean_step:.1f}"


def test_the_walk_sometimes_goes_back_up_the_page():
    """Strictly descending would be a sorted sweep wearing a hat."""
    order, _ = plan_card_visit_order(30)
    backward = sum(1 for a, b in zip(order, order[1:]) if b < a)
    assert backward > 0


def test_skipping_is_occasional_not_the_rule():
    rates = [len(plan_card_visit_order(20)[1]) / 20 for _ in range(500)]
    assert 0.05 < sum(rates) / len(rates) < 0.20


def test_zero_cards_is_not_a_crash():
    order, passed = plan_card_visit_order(0)
    assert order == [] and passed == set()


# ---------------------------------------------------------------------------
# The worker has to actually use it, and track what it has opened
# ---------------------------------------------------------------------------

def test_apec_does_not_walk_the_page_in_index_order():
    """Guards the regression directly: a bare `for i in range(count)` over the
    cards is the sequential sweep this replaced."""
    tree = ast.parse(APEC.read_text())
    uses_planner = any(
        isinstance(n, ast.Call) and getattr(n.func, "id", "") == "plan_card_visit_order"
        for n in ast.walk(tree)
    )
    assert uses_planner, "APEC no longer plans a randomised visit order"


def test_apec_tracks_the_jobs_it_has_already_opened():
    """Out-of-order visiting plus a rebuilt DOM after nav_back means position is
    not identity — the run needs its own record of what it has opened."""
    src = APEC.read_text()
    assert "seen_jobs" in src, "APEC keeps no record of jobs seen this run"
    assert "seen_jobs.append" in src, "APEC never records a job as seen"
    assert "in seen_jobs" in src, "APEC never checks whether it has seen a job"
