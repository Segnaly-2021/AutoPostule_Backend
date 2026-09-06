"""Announcements: who sees one, and who stops seeing it.

The read path runs on every authenticated page load, so its filters are the whole
feature: a draft that leaks is embarrassing, and a dismissal that does not stick
shows the same release note forever. Both are asserted here, along with the
validation on the app's first admin write surface.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET", "t" * 40)

from auto_apply_app.application.dtos.announcement_dtos import SaveAnnouncementRequest
from auto_apply_app.application.use_cases.announcement_use_cases import (
    DeleteAnnouncementUseCase,
    DismissAnnouncementUseCase,
    GetActiveAnnouncementsUseCase,
    ListAnnouncementsUseCase,
    SaveAnnouncementUseCase,
)
from auto_apply_app.domain.entities.announcement import Announcement
from auto_apply_app.domain.exceptions import ValidationError
from auto_apply_app.infrastructures.persistence.in_memory.memory import InMemoryUnitOfWork

UOW = InMemoryUnitOfWork


@pytest.fixture
def use_cases():
    InMemoryUnitOfWork.reset_all()
    return {
        "active": GetActiveAnnouncementsUseCase(UOW),
        "dismiss": DismissAnnouncementUseCase(UOW),
        "list": ListAnnouncementsUseCase(UOW),
        "save": SaveAnnouncementUseCase(UOW),
        "delete": DeleteAnnouncementUseCase(UOW),
    }


def _payload(**kw) -> SaveAnnouncementRequest:
    base = dict(title_fr="Titre", title_en="Title", body_fr="Corps", body_en="Body")
    base.update(kw)
    return SaveAnnouncementRequest(**base)


async def _store(*announcements: Announcement) -> None:
    async with InMemoryUnitOfWork() as uow:
        for a in announcements:
            await uow.announcement_repo.save(a)


def _announcement(title: str, **kw) -> Announcement:
    return Announcement(
        title_fr=title, title_en=title, body_fr="b", body_en="b", **kw
    )


# ----------------------------------------------------------------------
# Entity rules
# ----------------------------------------------------------------------

def test_both_languages_are_required():
    """A missing translation must fail here rather than render an empty modal to
    half the users."""
    for missing in ("title_fr", "title_en", "body_fr", "body_en"):
        fields = dict(title_fr="T", title_en="T", body_fr="B", body_en="B")
        fields[missing] = "   "
        with pytest.raises(ValidationError, match=missing):
            Announcement(**fields)


def test_a_cta_is_all_three_fields_or_none():
    """A URL with no label renders an unlabelled button; a label with no URL
    renders a button that does nothing."""
    ok = Announcement(
        title_fr="T", title_en="T", body_fr="B", body_en="B",
        cta_url="/x", cta_label_fr="Voir", cta_label_en="See",
    )
    assert ok.cta_url == "/x"

    for partial in (
        dict(cta_url="/x"),
        dict(cta_label_fr="Voir"),
        dict(cta_url="/x", cta_label_fr="Voir"),
    ):
        with pytest.raises(ValidationError, match="together"):
            Announcement(title_fr="T", title_en="T", body_fr="B", body_en="B", **partial)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<script>", "ftp://x"])
def test_cta_url_scheme_is_restricted(url):
    """This href is rendered for every logged-in user at once."""
    with pytest.raises(ValidationError, match="relative path or an http"):
        Announcement(
            title_fr="T", title_en="T", body_fr="B", body_en="B",
            cta_url=url, cta_label_fr="a", cta_label_en="b",
        )


def test_a_window_must_be_a_window():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="after starts_at"):
        Announcement(
            title_fr="T", title_en="T", body_fr="B", body_en="B",
            starts_at=now, ends_at=now - timedelta(seconds=1),
        )


def test_publication_and_schedule_are_independent_gates():
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=1)

    draft_in_window = _announcement("d", starts_at=past)
    assert draft_in_window.is_live_at(now) is False, "a draft must stay invisible"

    published_before_start = _announcement(
        "f", is_published=True, starts_at=now + timedelta(days=1)
    )
    assert published_before_start.is_live_at(now) is False

    open_ended = _announcement("o", is_published=True, starts_at=past)
    assert open_ended.is_live_at(now) is True, "ends_at=None means open-ended"


# ----------------------------------------------------------------------
# Read path
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_live_announcements_reach_a_user(use_cases):
    now = datetime.now(timezone.utc)
    await _store(
        _announcement("live", is_published=True, starts_at=now - timedelta(days=1)),
        _announcement("draft", starts_at=now - timedelta(days=1)),
        _announcement("future", is_published=True, starts_at=now + timedelta(days=1)),
        _announcement(
            "expired", is_published=True,
            starts_at=now - timedelta(days=5), ends_at=now - timedelta(days=1),
        ),
    )
    result = await use_cases["active"].execute(uuid.uuid4())
    assert [a.title_fr for a in result.value] == ["live"]


@pytest.mark.asyncio
async def test_dismissal_is_per_user(use_cases):
    now = datetime.now(timezone.utc)
    live = _announcement("live", is_published=True, starts_at=now - timedelta(days=1))
    await _store(live)
    alice, bob = uuid.uuid4(), uuid.uuid4()

    assert (await use_cases["dismiss"].execute(alice, live.id)).is_success
    assert (await use_cases["active"].execute(alice)).value == []
    assert len((await use_cases["active"].execute(bob)).value) == 1


@pytest.mark.asyncio
async def test_dismissing_twice_is_not_an_error(use_cases):
    """A double-click, or a dismiss racing a reload, must be a no-op."""
    now = datetime.now(timezone.utc)
    live = _announcement("live", is_published=True, starts_at=now - timedelta(days=1))
    await _store(live)
    user_id = uuid.uuid4()

    assert (await use_cases["dismiss"].execute(user_id, live.id)).is_success
    assert (await use_cases["dismiss"].execute(user_id, live.id)).is_success


@pytest.mark.asyncio
async def test_dismissing_an_unknown_announcement_is_404(use_cases):
    """A stale id means the client is out of date; writing the view row anyway
    would leave a dangling reference."""
    result = await use_cases["dismiss"].execute(uuid.uuid4(), uuid.uuid4())
    assert not result.is_success
    assert result.error.code.name == "NOT_FOUND"


@pytest.mark.asyncio
async def test_republishing_does_not_re_show_it_to_a_dismisser(use_cases):
    """unpublish keeps the dismissals precisely so this cannot happen -- that is
    what separates it from delete."""
    now = datetime.now(timezone.utc)
    created = await use_cases["save"].execute(
        _payload(is_published=True, starts_at=now - timedelta(minutes=1))
    )
    announcement_id = uuid.UUID(created.value.id)
    user_id = uuid.uuid4()
    await use_cases["dismiss"].execute(user_id, announcement_id)

    await use_cases["save"].execute(
        _payload(is_published=False, announcement_id=announcement_id)
    )
    await use_cases["save"].execute(
        _payload(
            is_published=True, announcement_id=announcement_id,
            starts_at=now - timedelta(minutes=1),
        )
    )
    assert (await use_cases["active"].execute(user_id)).value == []


# ----------------------------------------------------------------------
# Admin path
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_listing_includes_drafts(use_cases):
    await use_cases["save"].execute(_payload(is_published=False))
    rows = (await use_cases["list"].execute()).value
    assert len(rows) == 1 and rows[0].is_published is False


@pytest.mark.asyncio
async def test_an_edit_is_revalidated(use_cases):
    """An edit can break a rule the stored row satisfied -- here, clearing one
    CTA label but not the URL."""
    created = await use_cases["save"].execute(
        _payload(cta_url="/x", cta_label_fr="Voir", cta_label_en="See")
    )
    broken = await use_cases["save"].execute(
        _payload(
            announcement_id=uuid.UUID(created.value.id),
            cta_url="/x", cta_label_fr="Voir", cta_label_en="",
        )
    )
    assert not broken.is_success
    assert broken.error.code.name == "VALIDATION_ERROR"
    assert "together" in broken.error.message


@pytest.mark.asyncio
async def test_empty_optional_fields_are_stored_as_absent(use_cases):
    """A form posts "" for a cleared field, and "" would read as 'a CTA is
    present' while rendering a button with no label."""
    created = await use_cases["save"].execute(_payload(cta_label_fr="  ", icon=""))
    assert created.is_success
    assert created.value.cta_label_fr is None
    assert created.value.icon is None


@pytest.mark.asyncio
async def test_saving_an_unknown_id_is_404_not_a_silent_create(use_cases):
    result = await use_cases["save"].execute(_payload(announcement_id=uuid.uuid4()))
    assert not result.is_success
    assert result.error.code.name == "NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_removes_it_and_its_dismissals(use_cases):
    created = await use_cases["save"].execute(_payload(is_published=True))
    announcement_id = uuid.UUID(created.value.id)
    await use_cases["dismiss"].execute(uuid.uuid4(), announcement_id)

    assert (await use_cases["delete"].execute(announcement_id)).is_success
    assert (await use_cases["list"].execute()).value == []
    # Second delete is a 404, not a success -- deleting nothing is not deleting.
    assert not (await use_cases["delete"].execute(announcement_id)).is_success
