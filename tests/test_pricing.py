"""Plan allowances must be solvent against each other.

The three limits are not independent: the scrape budget decides how many cover
letters get written, the daily cap decides how many are kept and therefore billed,
and the credit allocation has to cover a full cycle of that. Getting the
relationship wrong is invisible until a paying user runs dry mid-month, so it is
asserted here rather than left to arithmetic in a plan document.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.value_objects import ClientType

PAID = [ClientType.BASIC, ClientType.PREMIUM]


def _sub(tier: ClientType, **kw) -> UserSubscription:
    return UserSubscription(user_id=uuid4(), email="a@b.c", account_type=tier, **kw)


@pytest.mark.parametrize("tier,volume,daily,scrape,credits", [
    (ClientType.BASIC,   180, 10, 15, 250),
    (ClientType.PREMIUM, 300, 25, 32, 400),
    (ClientType.FREE,      0,  0,  0,   0),
])
def test_plan_numbers(tier, volume, daily, scrape, credits):
    s = _sub(tier)
    assert (s.volume_limit, s.daily_limit, s.daily_scrape_budget,
            s.allocated_ai_credits) == (volume, daily, scrape, credits)


@pytest.mark.parametrize("tier", PAID)
def test_credits_cover_a_full_cycle(tier):
    """The check that was failing before: one credit per KEPT letter, and a full
    cycle of them must fit inside the allocation."""
    s = _sub(tier)
    assert s.allocated_ai_credits >= s.volume_limit, (
        f"{tier.name}: {s.volume_limit} applications sold but only "
        f"{s.allocated_ai_credits} credits"
    )


@pytest.mark.parametrize("tier", PAID)
def test_scrape_budget_exceeds_the_daily_cap(tier):
    """A surplus is the whole point -- without it 'keep the best N' selects from
    exactly N and ranking is decorative."""
    s = _sub(tier)
    assert s.daily_scrape_budget > s.daily_limit


@pytest.mark.parametrize("tier", PAID)
def test_billing_the_surplus_would_not_fit(tier):
    """Guards the ORDER of truncate-then-bill in analyze_and_generate. If someone
    moves the debit back above the truncation, BASIC goes over budget -- so this
    records why the order matters rather than trusting a comment."""
    s = _sub(tier)
    days = s.volume_limit // s.daily_limit
    billed_if_surplus_charged = s.daily_scrape_budget * days
    billed_as_implemented = s.daily_limit * days
    assert billed_as_implemented <= s.allocated_ai_credits
    if tier is ClientType.BASIC:
        assert billed_if_surplus_charged > s.allocated_ai_credits


def test_free_tier_gets_nothing():
    s = _sub(ClientType.FREE)
    assert s.volume_limit == 0 and s.daily_limit == 0 and s.daily_scrape_budget == 0


@pytest.mark.parametrize("tier", PAID)
def test_zero_credits_blocks_the_run(tier):
    now = datetime.now(timezone.utc)
    s = _sub(tier, is_active=True, current_period_end=now + timedelta(days=10))
    s.ai_credits_balance = s.allocated_ai_credits
    assert s.can_run_agent() is True
    s.ai_credits_balance = 0
    assert s.can_run_agent() is False


# ---------------------------------------------------------------------------
# Cycle rollover
#
# Volume has no counter of its own: it is COUNT(offers submitted inside
# [current_period_start, current_period_end)). So "reset the volume" IS "move the
# window", and credits must move with it -- a refilled wallet against last
# cycle's window would let a user pay for applications they cannot send.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tier", PAID)
def test_start_new_cycle_resets_credits_and_window(tier):
    now = datetime.now(timezone.utc)
    s = _sub(tier, is_active=True)
    s.current_period_start = now - timedelta(days=60)
    s.current_period_end = now - timedelta(days=30)
    s.ai_credits_balance = 3

    s.start_new_cycle(now, now + timedelta(days=30))

    assert s.ai_credits_balance == s.allocated_ai_credits
    assert s.current_period_start == now
    assert s.current_period_end == now + timedelta(days=30)
    assert s.next_billing_date == s.current_period_end


@pytest.mark.parametrize("tier", PAID)
def test_a_new_cycle_makes_the_agent_runnable_again(tier):
    """The expired-period branch of can_run_agent is what a lapsed user hits;
    rolling the cycle has to clear it, not just the empty wallet."""
    now = datetime.now(timezone.utc)
    s = _sub(tier, is_active=True)
    s.current_period_end = now - timedelta(days=1)
    s.ai_credits_balance = 0
    assert s.can_run_agent() is False

    s.start_new_cycle(now, now + timedelta(days=30))
    assert s.can_run_agent() is True


def test_replenishing_without_moving_the_window_is_not_enough():
    """Records why start_new_cycle exists. replenish_credits alone leaves the old
    window in place, so last cycle's sends still count against the new volume."""
    now = datetime.now(timezone.utc)
    s = _sub(ClientType.BASIC, is_active=True)
    s.current_period_start = now - timedelta(days=60)
    s.current_period_end = now - timedelta(days=30)

    s.replenish_credits()
    assert s.ai_credits_balance == 250
    assert s.current_period_end < now          # window untouched -> still expired
    assert s.can_run_agent() is False
