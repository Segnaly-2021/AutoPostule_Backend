"""Lifecycle messaging: who gets selected, and who never gets mailed twice.

The whole subsystem is a scheduled job that nobody watches, so the failure modes
are silent by construction: a cohort query off by one boundary mails the wrong
day forever, and a 'sent' row written on a failed send suppresses that message
permanently. Both are asserted here rather than discovered from a support email.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET", "t" * 40)
os.environ.setdefault("API_PUBLIC_URL", "https://api.autopostule.test")

from auto_apply_app.application.dtos.auth_user_dtos import UnsubscribeRequest
from auto_apply_app.application.use_cases.user_use_cases import (
    UnsubscribeUseCase,
    make_unsubscribe_url,
)
from auto_apply_app.domain.entities.auth_user import AuthUser
from auto_apply_app.domain.entities.credit_transaction import CreditTransaction
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.value_objects import (
    CreditTxKind,
    MessageKind,
    SearchStatus,
)
from auto_apply_app.infrastructures.authentication.token_provider import JwtTokenProvider
from auto_apply_app.infrastructures.emailing_service.resend_email_service import (
    ResendEmailService,
)
from auto_apply_app.infrastructures.persistence.in_memory.memory import InMemoryUnitOfWork
from auto_apply_app.messaging_main import (
    anchor_window,
    _run_free_account_reminder,
    _run_new_customer_checkin,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

class RecordingEmailService(ResendEmailService):
    """Captures what would go out. Subclasses the real adapter so a signature
    change breaks the test instead of silently passing against a stale double."""

    def __init__(self, accept: bool = True) -> None:
        super().__init__()
        self.accept = accept
        self.sent: list[tuple] = []

    async def send_new_customer_checkin(self, to_email, first_name) -> bool:
        self.sent.append(("checkin", to_email, first_name, None))
        return self.accept

    async def send_free_account_reminder(self, to_email, first_name, unsubscribe_url) -> bool:
        self.sent.append(("reminder", to_email, first_name, unsubscribe_url))
        return self.accept


class FakeApp:
    """The three attributes messaging_main reads off the container."""

    uow_factory = InMemoryUnitOfWork

    def __init__(self, email: RecordingEmailService) -> None:
        self.email_service_port = email
        self.token_provider = JwtTokenProvider()


@pytest.fixture
def window():
    return anchor_window(3)


@pytest.fixture
def app():
    InMemoryUnitOfWork.reset_all()
    email = RecordingEmailService()
    return FakeApp(email)


async def _make_account(email: str, created_at: datetime, first: str = "Alice") -> uuid.UUID:
    user_id = uuid.uuid4()
    async with InMemoryUnitOfWork() as uow:
        auth = AuthUser(email=email, password_hash="h", user_id=user_id)
        auth.created_at = created_at
        await uow.auth_repo.save(auth)
        user = User(firstname=first, lastname="Durand", email=email)
        user.id = user_id
        await uow.user_repo.save(user)
    return user_id


async def _replenish(user_id: uuid.UUID, at: datetime) -> None:
    async with InMemoryUnitOfWork() as uow:
        tx = CreditTransaction(
            user_id=user_id, delta=400, balance_after=400, kind=CreditTxKind.REPLENISH
        )
        tx.created_at = at
        await uow.credit_tx_repo.record(tx)


# ----------------------------------------------------------------------
# The window
# ----------------------------------------------------------------------

def test_window_is_a_whole_utc_day():
    now = datetime(2026, 9, 5, 6, 11, tzinfo=timezone.utc)
    start, end = anchor_window(3, now)
    assert start == datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def test_consecutive_runs_tile_with_no_gap():
    """The reason the window is a calendar day and not 'now minus 72 hours': the
    job does not fire at the same second every day, and anyone falling in the
    drift would never be selected at all."""
    early = anchor_window(3, datetime(2026, 9, 5, 6, 11, tzinfo=timezone.utc))
    late = anchor_window(3, datetime(2026, 9, 6, 23, 59, tzinfo=timezone.utc))
    assert late[0] == early[1]


# ----------------------------------------------------------------------
# Cohort selection
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cohort_boundaries_are_half_open(app, window):
    start, end = window
    await _make_account("first@x.fr", start)                       # in
    await _make_account("last@x.fr", end - timedelta(microseconds=1))  # in
    await _make_account("early@x.fr", start - timedelta(microseconds=1))  # out
    await _make_account("late@x.fr", end)                          # out

    stats = await _run_free_account_reminder(app, start, end)
    assert sorted(s[1] for s in app.email_service_port.sent) == ["first@x.fr", "last@x.fr"]
    assert stats.sent == 2


@pytest.mark.asyncio
async def test_a_renewal_is_not_a_first_purchase(app, window):
    """current_period_start is rewritten every cycle, which is exactly why the
    anchor is the ledger. A month-old customer renewing inside the window must
    not be congratulated on signing up."""
    start, end = window
    inside = start + timedelta(hours=5)

    new_customer = await _make_account("new@x.fr", inside, "Paul")
    veteran = await _make_account("veteran@x.fr", start - timedelta(days=30), "Victor")
    await _replenish(new_customer, inside)
    await _replenish(veteran, start - timedelta(days=30))
    await _replenish(veteran, inside)

    await _run_new_customer_checkin(app, start, end)
    assert [s[1] for s in app.email_service_port.sent] == ["new@x.fr"]


@pytest.mark.asyncio
async def test_opted_out_user_is_never_in_a_marketing_cohort(app, window):
    start, end = window
    opted_out = await _make_account("out@x.fr", start + timedelta(hours=1))
    await _make_account("in@x.fr", start + timedelta(hours=1))

    async with InMemoryUnitOfWork() as uow:
        auth = await uow.auth_repo.get_by_id(opted_out)
        auth.set_marketing_opt_out(True)
        await uow.auth_repo.save(auth)

    await _run_free_account_reminder(app, start, end)
    assert [s[1] for s in app.email_service_port.sent] == ["in@x.fr"]


@pytest.mark.asyncio
async def test_opt_out_does_not_suppress_the_paid_checkin(app, window):
    """The check-in is transactional. Opting out of announcements is not opting
    out of hearing about the thing you are paying for."""
    start, end = window
    inside = start + timedelta(hours=2)
    payer = await _make_account("payer@x.fr", inside, "Paul")
    async with InMemoryUnitOfWork() as uow:
        auth = await uow.auth_repo.get_by_id(payer)
        auth.set_marketing_opt_out(True)
        await uow.auth_repo.save(auth)
    await _replenish(payer, inside)

    await _run_new_customer_checkin(app, start, end)
    assert [s[1] for s in app.email_service_port.sent] == ["payer@x.fr"]


@pytest.mark.asyncio
async def test_nobody_gets_both_emails_on_the_same_day(app, window):
    start, end = window
    inside = start + timedelta(hours=2)
    payer = await _make_account("payer@x.fr", inside, "Paul")
    await _replenish(payer, inside)

    await _run_new_customer_checkin(app, start, end)
    await _run_free_account_reminder(app, start, end)
    kinds = [s[0] for s in app.email_service_port.sent]
    assert kinds == ["checkin"]


@pytest.mark.asyncio
async def test_reminder_skips_anyone_who_started_a_search(app, window):
    """The copy says 'sans encore lancer de recherche'. If that stops being true
    the sentence has to go, so it is asserted rather than assumed."""
    start, end = window
    busy = await _make_account("busy@x.fr", start + timedelta(hours=1))
    await _make_account("idle@x.fr", start + timedelta(hours=1))
    async with InMemoryUnitOfWork() as uow:
        search = JobSearch(user_id=busy, job_title="Dev", job_boards=["apec"])
        search.search_status = SearchStatus.COMPLETED
        await uow.search_repo.save(search)

    await _run_free_account_reminder(app, start, end)
    assert [s[1] for s in app.email_service_port.sent] == ["idle@x.fr"]


# ----------------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_log_rejects_a_second_send(app):
    user_id = uuid.uuid4()
    async with InMemoryUnitOfWork() as uow:
        assert await uow.message_log_repo.record(user_id, MessageKind.FREE_ACCOUNT_REMINDER)
        assert not await uow.message_log_repo.record(user_id, MessageKind.FREE_ACCOUNT_REMINDER)
        # A different kind is a different message and must still be allowed.
        assert await uow.message_log_repo.record(user_id, MessageKind.NEW_CUSTOMER_CHECKIN)


@pytest.mark.asyncio
async def test_rerunning_the_job_sends_nothing(app, window):
    start, end = window
    inside = start + timedelta(hours=3)
    payer = await _make_account("payer@x.fr", inside, "Paul")
    await _replenish(payer, inside)
    await _make_account("free@x.fr", inside, "Fanny")

    for _ in range(2):
        await _run_new_customer_checkin(app, start, end)
        await _run_free_account_reminder(app, start, end)

    assert len(app.email_service_port.sent) == 2


@pytest.mark.asyncio
async def test_a_rejected_send_is_not_logged_as_sent(window):
    """The asymmetry that makes the send-then-log order correct: a row written
    for a message that never arrived would suppress it forever."""
    start, end = window
    InMemoryUnitOfWork.reset_all()
    app = FakeApp(RecordingEmailService(accept=False))

    inside = start + timedelta(hours=3)
    payer = await _make_account("payer@x.fr", inside, "Paul")
    await _replenish(payer, inside)

    stats = await _run_new_customer_checkin(app, start, end)
    assert (stats.sent, stats.failed) == (0, 1)
    async with InMemoryUnitOfWork() as uow:
        assert not await uow.message_log_repo.already_sent(
            payer, MessageKind.NEW_CUSTOMER_CHECKIN
        )


@pytest.mark.asyncio
async def test_send_returns_false_without_an_api_key(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    service = ResendEmailService()
    assert await service.send_new_customer_checkin("a@b.fr", "Alice") is False


# ----------------------------------------------------------------------
# Unsubscribe
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsubscribe_link_opts_the_user_out():
    InMemoryUnitOfWork.reset_all()
    provider = JwtTokenProvider()
    user_id = await _make_account("a@x.fr", datetime.now(timezone.utc))

    token = make_unsubscribe_url(provider, user_id).split("token=")[1]
    use_case = UnsubscribeUseCase(
        uow_factory=InMemoryUnitOfWork, token_provider=provider
    )

    assert (await use_case.execute(UnsubscribeRequest(token=token))).is_success
    async with InMemoryUnitOfWork() as uow:
        assert (await uow.auth_repo.get_by_id(user_id)).marketing_opt_out is True

    # A second click, or a mail client prefetching the link, must not be an error.
    assert (await use_case.execute(UnsubscribeRequest(token=token))).is_success


@pytest.mark.asyncio
async def test_unsubscribe_rejects_a_token_minted_for_something_else():
    """Without the purpose check, any password-reset link would double as an
    unsubscribe link for its holder."""
    InMemoryUnitOfWork.reset_all()
    provider = JwtTokenProvider()
    user_id = await _make_account("a@x.fr", datetime.now(timezone.utc))
    token = provider.encode_token(user_id=user_id, claims={"purpose": "password_reset"})

    use_case = UnsubscribeUseCase(
        uow_factory=InMemoryUnitOfWork, token_provider=provider
    )
    result = await use_case.execute(UnsubscribeRequest(token=token))
    assert not result.is_success
    async with InMemoryUnitOfWork() as uow:
        assert (await uow.auth_repo.get_by_id(user_id)).marketing_opt_out is False


@pytest.mark.asyncio
async def test_unsubscribe_survives_an_unrelated_save():
    """merge() rewrites the whole auth row from the entity, so a field missing
    from save() is reset by the next password change -- the trap the is_admin
    comment in AuthRepoDB warns about."""
    InMemoryUnitOfWork.reset_all()
    user_id = await _make_account("a@x.fr", datetime.now(timezone.utc))
    async with InMemoryUnitOfWork() as uow:
        auth = await uow.auth_repo.get_by_id(user_id)
        auth.set_marketing_opt_out(True)
        await uow.auth_repo.save(auth)

    async with InMemoryUnitOfWork() as uow:
        auth = await uow.auth_repo.get_by_id(user_id)
        auth.change_password("new-hash")
        await uow.auth_repo.save(auth)
        assert (await uow.auth_repo.get_by_id(user_id)).marketing_opt_out is True
