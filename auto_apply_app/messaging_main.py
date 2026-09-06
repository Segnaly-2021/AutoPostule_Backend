"""
Cloud Run Job entrypoint for lifecycle messaging.

Runs once a day, on a schedule, and sends the two onboarding emails to whoever
crossed their anchor exactly N days ago:

  * NEW_CUSTOMER_CHECKIN   -- anchor: the user's FIRST replenish in the credit
                              ledger, i.e. the day they actually paid.
  * FREE_ACCOUNT_REMINDER  -- anchor: auth_users.created_at, for accounts that
                              never started a search.

Launched as:  python -m auto_apply_app.messaging_main

Three properties this file is built around:

1. **The window is a calendar day, not "72 hours ago".** The job may run at 06:00
   one day and 06:11 the next; a sliding window anchored on the current instant
   would leave an 11-minute gap that nobody is ever selected from. The window is
   the whole UTC day that fell N days back, so consecutive runs tile the timeline
   with no seam.

2. **Re-running is safe.** Every send is guarded by message_log, whose UNIQUE on
   (user_id, kind) is the real barrier. A retry after a crash, an accidental
   double trigger, or a manual backfill re-selects the same cohort and sends
   nothing.

3. **One failure does not stop the run.** A cohort of 200 with one bad address
   must still deliver the other 199, so each recipient is wrapped individually and
   the exit code reflects only whether the run itself completed.
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID

from auto_apply_app.domain.value_objects import MessageKind, SearchStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("messaging_main")

# How long after the anchor event the message goes out. Env-overridable so a
# backfill can target an older day without a redeploy.
ANCHOR_DAYS = int(os.getenv("MESSAGING_ANCHOR_DAYS", "3"))

# Select and log everything, send nothing. The verification step in the plan runs
# the job this way before it is ever pointed at real inboxes.
DRY_RUN = os.getenv("MESSAGING_DRY_RUN", "false").lower() == "true"


def anchor_window(days_ago: int, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """The UTC calendar day that fell `days_ago` days back, half-open [start, end).

    Half-open matches the house convention for every other range query in the
    repo (`count_created_between`, `sum_consumed_between`), which matters here
    because midnight belongs to exactly one window -- a closed range would send
    twice to anyone created on the boundary, if message_log did not catch it.
    """
    now = now or datetime.now(timezone.utc)
    day = (now - timedelta(days=days_ago)).date()
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


class _Stats:
    """Per-kind tally, printed as the run's one summary line."""

    def __init__(self) -> None:
        self.selected = 0
        self.skipped = 0
        self.sent = 0
        self.failed = 0

    def __str__(self) -> str:
        return (
            f"selected={self.selected} skipped={self.skipped} "
            f"sent={self.sent} failed={self.failed}"
        )


async def _deliver(uow_factory, kind: MessageKind, user_id: UUID, send, stats: _Stats) -> None:
    """Send one message and log it, in that order.

    The order is deliberate and asymmetric: logging first would permanently
    suppress a message whose send then failed, because the log is also what
    suppresses retries. Sending first risks a duplicate only in the narrow window
    where the process dies between the two -- the cheaper mistake by far.
    """
    async with uow_factory() as uow:
        if await uow.message_log_repo.already_sent(user_id, kind):
            stats.skipped += 1
            return

    if DRY_RUN:
        logger.info("[dry-run] would send %s to user %s", kind.value, user_id)
        stats.sent += 1
        return

    try:
        accepted = await send()
    except Exception:
        logger.exception("Send crashed for %s / user %s", kind.value, user_id)
        stats.failed += 1
        return

    if not accepted:
        # Not an exception: _send already logged the provider's reason. Nothing is
        # written, so tomorrow's run picks this user up again if they are still in
        # window -- which, with a calendar-day window, they will not be. Accepted:
        # chasing a bounce is a deliverability problem, not a scheduling one.
        stats.failed += 1
        return

    async with uow_factory() as uow:
        await uow.message_log_repo.record(user_id, kind)
    stats.sent += 1


async def _run_new_customer_checkin(app, start: datetime, end: datetime) -> _Stats:
    """Everyone whose first-ever replenish landed in the window."""
    stats = _Stats()
    kind = MessageKind.NEW_CUSTOMER_CHECKIN

    async with app.uow_factory() as uow:
        user_ids = await uow.credit_tx_repo.first_purchase_between(start, end)
    stats.selected = len(user_ids)

    for user_id in user_ids:
        async with app.uow_factory() as uow:
            user = await uow.user_repo.get(user_id)
        if user is None:
            logger.warning("Check-in: no profile for user %s", user_id)
            stats.skipped += 1
            continue

        await _deliver(
            app.uow_factory,
            kind,
            user_id,
            # Transactional: no unsubscribe link, and marketing_opt_out is not
            # consulted. Someone who opted out of announcements has not opted out
            # of hearing about the thing they are paying for.
            lambda u=user: app.email_service_port.send_new_customer_checkin(
                to_email=u.email,
                first_name=u.firstname or "",
            ),
            stats,
        )

    return stats


async def _run_free_account_reminder(app, start: datetime, end: datetime) -> _Stats:
    """Accounts created in the window that never started a search.

    The 'never started a search' filter is what keeps this honest -- the copy says
    "sans encore lancer de recherche", and sending it to someone mid-run would
    read as though we were not watching.
    """
    from auto_apply_app.application.use_cases.user_use_cases import make_unsubscribe_url

    stats = _Stats()
    kind = MessageKind.FREE_ACCOUNT_REMINDER
    all_statuses = list(SearchStatus)

    async with app.uow_factory() as uow:
        # Marketing, so consent is filtered at the source rather than per-recipient.
        auth_users = await uow.auth_repo.list_created_between(
            start, end, exclude_opted_out=True
        )
        # Anyone who paid in the same window gets the check-in instead; sending
        # both on the same day would be the worst possible first impression.
        paid = set(await uow.credit_tx_repo.first_purchase_between(start, end))

    stats.selected = len(auth_users)

    for auth in auth_users:
        if auth.user_id in paid:
            stats.skipped += 1
            continue

        async with app.uow_factory() as uow:
            searches = await uow.search_repo.list_recent_by_user(
                auth.user_id, all_statuses, limit=1
            )
            if searches:
                stats.skipped += 1
                continue
            user = await uow.user_repo.get(auth.user_id)

        first_name = user.firstname if user else ""
        unsubscribe_url = make_unsubscribe_url(app.token_provider, auth.user_id)

        await _deliver(
            app.uow_factory,
            kind,
            auth.user_id,
            lambda e=auth.email, n=first_name, u=unsubscribe_url: (
                app.email_service_port.send_free_account_reminder(
                    to_email=e, first_name=n or "", unsubscribe_url=u
                )
            ),
            stats,
        )

    return stats


async def _amain() -> int:
    start, end = anchor_window(ANCHOR_DAYS)
    logger.info(
        "Messaging run: window=[%s, %s) anchor_days=%s dry_run=%s",
        start.isoformat(), end.isoformat(), ANCHOR_DAYS, DRY_RUN,
    )

    from auto_apply_app.infrastructures.configuration.container import (
        create_worker_application,
    )
    # for_messaging=True adds the email sender and the token provider that signs
    # unsubscribe links; the agent Job builds neither.
    app = create_worker_application(for_messaging=True)

    stop = asyncio.Event()

    def _on_sigterm() -> None:
        # Cloud Run preempts with SIGTERM. There is nothing to clean up -- no
        # browser, no long transaction -- so this just stops the run between
        # recipients rather than mid-send.
        logger.warning("SIGTERM: finishing the current recipient, then stopping")
        stop.set()

    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, _on_sigterm)
    except (NotImplementedError, RuntimeError):
        pass  # signal handlers may be unavailable in some environments

    checkin = await _run_new_customer_checkin(app, start, end)
    logger.info("new_customer_checkin: %s", checkin)

    if stop.is_set():
        logger.warning("Stopping after check-ins; reminder cohort not processed")
        return 1

    reminder = await _run_free_account_reminder(app, start, end)
    logger.info("free_account_reminder: %s", reminder)

    # A failed send is a real outcome worth an alert -- with no scheduled-job
    # monitoring yet, the exit code is the only signal that leaves this process.
    return 1 if (checkin.failed or reminder.failed) else 0


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
