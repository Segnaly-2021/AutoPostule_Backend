"""Persona pool lifecycle, driven through the real use case and in-memory repo."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from auto_apply_app.application.use_cases.fingerprint_use_cases import (
    ResolveRunFingerprintUseCase,
)
from auto_apply_app.infrastructures.agent.fingerprint_generator import FingerprintGenerator
from auto_apply_app.infrastructures.persistence.in_memory.memory import InMemoryUnitOfWork


@pytest.fixture(autouse=True)
def _clean_pool():
    """The in-memory UoW keeps storage on the CLASS, so it leaks between tests."""
    InMemoryUnitOfWork._shared_fingerprints_db.clear()
    yield
    InMemoryUnitOfWork._shared_fingerprints_db.clear()


@pytest.fixture
def use_case():
    return ResolveRunFingerprintUseCase(
        uow_factory=InMemoryUnitOfWork, generator=FingerprintGenerator()
    )


async def _resolve(use_case, user_id, board, token):
    result = await use_case.execute(user_id, board, token)
    assert result.is_success, getattr(result.error, "message", result.error)
    return result.value


async def test_memory_mode_actually_has_a_fingerprint_repo():
    """InMemoryUnitOfWork never set user_fingerprint_repo, and the use case's
    blanket except swallowed the AttributeError — so every local run went out
    unfingerprinted on one shared hardcoded UA."""
    async with InMemoryUnitOfWork() as uow:
        assert uow.user_fingerprint_repo is not None


async def test_each_board_gets_its_own_device(use_case):
    user_id = uuid4()
    apec = await _resolve(use_case, user_id, "apec", "run-1")
    wttj = await _resolve(use_case, user_id, "wttj", "run-1")
    assert apec.fingerprint.id != wttj.fingerprint.id
    assert apec.fingerprint.platform != wttj.fingerprint.platform


async def test_a_board_keeps_its_device_across_runs(use_case):
    """The device must be stable: the login cookies for this board belong to it,
    and a board seeing a new machine every visit is the mismatch this design
    exists to avoid."""
    user_id = uuid4()
    first = await _resolve(use_case, user_id, "apec", "run-1")
    second = await _resolve(use_case, user_id, "apec", "run-2")

    assert first.fingerprint.id == second.fingerprint.id
    assert first.fingerprint.webgl_renderer == second.fingerprint.webgl_renderer
    # ...while the session surface still rotates.
    assert first.fingerprint.canvas_seed != second.fingerprint.canvas_seed


async def test_pool_size_is_bounded(use_case, monkeypatch):
    """More active boards than personas must SHARE devices, not mint unbounded
    machines for one user."""
    monkeypatch.setenv("FINGERPRINT_POOL_SIZE", "2")
    user_id = uuid4()
    for board in ("apec", "wttj", "hellowork"):
        await _resolve(use_case, user_id, board, "run-1")

    async with InMemoryUnitOfWork() as uow:
        active = await uow.user_fingerprint_repo.list_by_user(user_id)
    assert len(active) == 2


async def test_session_count_is_tracked(use_case):
    user_id = uuid4()
    for i in range(3):
        await _resolve(use_case, user_id, "apec", f"run-{i}")

    async with InMemoryUnitOfWork() as uow:
        apec = await uow.user_fingerprint_repo.get_by_user_and_board(user_id, "apec")
    assert apec.session_count == 3


async def test_worn_out_device_is_retired_and_replaced(use_case, monkeypatch):
    monkeypatch.setenv("FINGERPRINT_DEVICE_MAX_AGE_DAYS", "45")
    user_id = uuid4()
    original = (await _resolve(use_case, user_id, "apec", "run-1")).fingerprint

    async with InMemoryUnitOfWork() as uow:
        row = await uow.user_fingerprint_repo.get_by_user_and_board(user_id, "apec")
        row.created_at = datetime.now(timezone.utc) - timedelta(days=90)

    after = await _resolve(use_case, user_id, "apec", "run-2")

    assert original.id in after.retired_ids, "aged-out device was not retired"
    assert after.fingerprint.id != original.id, "retired device is still in use"

    async with InMemoryUnitOfWork() as uow:
        active = await uow.user_fingerprint_repo.list_by_user(user_id)
        every = await uow.user_fingerprint_repo.list_by_user(user_id, include_retired=True)
    assert all(fp.id != original.id for fp in active)
    assert any(fp.id == original.id for fp in every), "retired row must stay addressable"


async def test_device_retires_on_session_count(use_case, monkeypatch):
    monkeypatch.setenv("FINGERPRINT_DEVICE_MAX_SESSIONS", "3")
    user_id = uuid4()
    seen = []
    for i in range(6):
        seen.append((await _resolve(use_case, user_id, "apec", f"run-{i}")).fingerprint.id)
    assert len(set(seen)) > 1, "device never rotated despite the session cap"


async def test_slots_stay_unique_across_retirements(use_case, monkeypatch):
    """(user_id, slot) is the upsert key. A reused slot would silently overwrite
    a live persona."""
    monkeypatch.setenv("FINGERPRINT_DEVICE_MAX_SESSIONS", "2")
    user_id = uuid4()
    for i in range(8):
        await _resolve(use_case, user_id, "apec", f"run-{i}")

    async with InMemoryUnitOfWork() as uow:
        every = await uow.user_fingerprint_repo.list_by_user(user_id, include_retired=True)
    slots = [fp.slot for fp in every]
    assert len(slots) == len(set(slots)), f"duplicate slots: {slots}"


async def test_repo_save_upserts_instead_of_conflicting(use_case):
    """The old save() used session.merge() on an entity carrying a fresh uuid4,
    which against a unique user_id was an insert conflict rather than an update.
    That is exactly why rotation could not work."""
    user_id = uuid4()
    await _resolve(use_case, user_id, "apec", "run-1")

    async with InMemoryUnitOfWork() as uow:
        repo = uow.user_fingerprint_repo
        row = await repo.get_by_user_and_board(user_id, "apec")
        original_id = row.id

        row.chrome_major = 999
        saved = await repo.save(row)
        assert saved.id == original_id, "upsert renumbered the persona"

        again = await repo.list_by_user(user_id)
        assert len([fp for fp in again if fp.slot == row.slot]) == 1
        assert next(fp for fp in again if fp.slot == row.slot).chrome_major == 999


async def test_resume_reproduces_the_same_browser(use_case):
    """A human-review resume passes the original run token back."""
    user_id = uuid4()
    a = await _resolve(use_case, user_id, "apec", "run-token-A")
    b = await _resolve(use_case, user_id, "apec", "run-token-A")
    assert a.fingerprint.canvas_seed == b.fingerprint.canvas_seed
    assert a.fingerprint.viewport_width == b.fingerprint.viewport_width


async def test_failure_returns_a_failed_result_not_an_exception():
    class Exploding:
        def generate_device(self, *a, **k):
            raise RuntimeError("boom")
        def derive_session_variant(self, *a, **k):
            raise RuntimeError("boom")

    uc = ResolveRunFingerprintUseCase(
        uow_factory=InMemoryUnitOfWork, generator=Exploding()
    )
    result = await uc.execute(uuid4(), "apec", "run-1")
    assert not result.is_success


# ---------------------------------------------------------------------------
# FINGERPRINT_MODE=per_run — a whole new device every run.
#
# Opt-in, and measured against the pool rather than assumed better: it trades the
# cookie jar and the sticky exit IP (both keyed on the persona id) for a machine
# the board has never seen. These tests pin the trade-off's mechanics, not its
# wisdom.
# ---------------------------------------------------------------------------


@pytest.fixture
def per_run(monkeypatch, use_case):
    monkeypatch.setenv("FINGERPRINT_MODE", "per_run")
    return use_case


async def test_pool_is_the_default_when_the_flag_is_absent(use_case, monkeypatch):
    monkeypatch.delenv("FINGERPRINT_MODE", raising=False)
    first = await _resolve(use_case, (user_id := uuid4()), "apec", "run-1")
    second = await _resolve(use_case, user_id, "apec", "run-2")
    assert first.fingerprint.id == second.fingerprint.id


async def test_per_run_mints_a_new_device_every_run(per_run):
    user_id = uuid4()
    runs = [
        (await _resolve(per_run, user_id, "apec", f"run-{i}")).fingerprint
        for i in range(8)
    ]

    ids = [fp.id for fp in runs]
    assert len(set(ids)) == len(ids), f"device was reused: {ids}"

    # The device identity itself must move, not just the session surface. Drawn
    # at random, so two consecutive machines can legitimately match — over eight
    # runs, all-identical means nothing is re-rolling.
    devices = {
        (fp.platform, fp.webgl_renderer, fp.screen_width, fp.hardware_concurrency)
        for fp in runs
    }
    assert len(devices) > 1, f"device identity never changed: {devices}"


async def test_per_run_retires_the_outgoing_device(per_run):
    """The old persona must be retired, not left active — that is what deletes
    its cookie jar and stops it being handed to another board."""
    user_id = uuid4()
    first = await _resolve(per_run, user_id, "apec", "run-1")
    second = await _resolve(per_run, user_id, "apec", "run-2")

    assert first.fingerprint.id in second.retired_ids

    async with InMemoryUnitOfWork() as uow:
        active = await uow.user_fingerprint_repo.list_by_user(user_id)
    assert [fp.id for fp in active] == [second.fingerprint.id]


async def test_per_run_never_reuses_a_slot(per_run):
    """A reused slot would hand the new device the OLD persona id (save upserts
    on (user_id, slot) and keeps the existing id) — and with it a cookie jar and
    an exit IP belonging to a machine that no longer exists."""
    user_id = uuid4()
    ids, slots = [], []
    for i in range(5):
        fp = (await _resolve(per_run, user_id, "apec", f"run-{i}")).fingerprint
        ids.append(fp.id)
        slots.append(fp.slot)

    assert len(set(ids)) == 5, f"persona ids repeated: {ids}"
    assert len(set(slots)) == 5, f"slots repeated: {slots}"


async def test_per_run_keeps_boards_on_separate_devices(per_run):
    user_id = uuid4()
    apec = await _resolve(per_run, user_id, "apec", "run-1")
    wttj = await _resolve(per_run, user_id, "wttj", "run-1")

    assert apec.fingerprint.id != wttj.fingerprint.id
    # Minting for wttj must not retire apec's live device.
    assert apec.fingerprint.id not in wttj.retired_ids

    async with InMemoryUnitOfWork() as uow:
        active = await uow.user_fingerprint_repo.list_by_user(user_id)
    assert {fp.board for fp in active} == {"apec", "wttj"}


async def test_per_run_resume_is_not_a_resume(per_run):
    """Honest documentation of the cost: passing the original run token back no
    longer reproduces the browser, because the device underneath it is gone. A
    human-review resume in this mode restarts on a new machine."""
    user_id = uuid4()
    a = await _resolve(per_run, user_id, "apec", "run-token-A")
    b = await _resolve(per_run, user_id, "apec", "run-token-A")
    assert a.fingerprint.id != b.fingerprint.id


async def test_retired_rows_do_not_grow_without_bound(per_run, monkeypatch):
    monkeypatch.setenv("FINGERPRINT_RETIRED_KEEP", "3")
    user_id = uuid4()
    for i in range(10):
        await _resolve(per_run, user_id, "apec", f"run-{i}")

    async with InMemoryUnitOfWork() as uow:
        every = await uow.user_fingerprint_repo.list_by_user(user_id, include_retired=True)

    retired = [fp for fp in every if fp.retired_at is not None]
    assert len(retired) == 3, f"purge kept {len(retired)} retired rows"
    # Purging must never touch the live device, and slots must still be unique.
    active = [fp for fp in every if fp.retired_at is None]
    assert len(active) == 1
    slots = [fp.slot for fp in every]
    assert len(slots) == len(set(slots))


async def test_switching_back_to_pool_reuses_the_live_device(per_run, monkeypatch):
    """Flipping the flag off mid-life must not strand the account: the device
    minted by the last per_run run is a normal pinned persona."""
    user_id = uuid4()
    last = await _resolve(per_run, user_id, "apec", "run-1")

    monkeypatch.setenv("FINGERPRINT_MODE", "pool")
    after = await _resolve(per_run, user_id, "apec", "run-2")
    assert after.fingerprint.id == last.fingerprint.id
