"""How APEC returns to the results list.

Two independent steps, because APEC can put up to two pages between an offer and
the list it came from: a redirect interstitial, and a page carrying the link that
actually goes back. Both are flags the caller sets, and the card check at the end
is what decides whether the results are really there.

Driven by binding the real method to a stub, so the branching is tested without a
browser.
"""
import ast
from pathlib import Path

import pytest

from auto_apply_app.infrastructures.agent.workers.apec.apec_worker import ApecWorker

SOURCE = Path("auto_apply_app/infrastructures/agent/workers/apec/apec_worker.py")


class _FakeLocator:
    def __init__(self, page, visible=True):
        self._page = page
        self._visible = visible

    async def wait_for(self, state=None, timeout=None):
        if not self._visible:
            raise TimeoutError("not visible")

    @property
    def page(self):
        return self._page


class _FakePage:
    def __init__(self, back_link_visible=True, cards_visible=True):
        self.url = "https://www.apec.fr/offre/123"
        self.calls = []
        self._back_link_visible = back_link_visible
        self._cards_visible = cards_visible

    def locator(self, selector):
        self.calls.append(("locator", selector))
        return _FakeLocator(self, visible=self._back_link_visible)

    async def go_back(self, **kw):
        self.calls.append(("go_back", None))

    async def goto(self, url, **kw):
        self.calls.append(("goto", url))

    async def reload(self, **kw):
        self.calls.append(("reload", None))

    async def wait_for_load_state(self, *a, **kw):
        pass

    async def wait_for_selector(self, selector, **kw):
        if not self._cards_visible:
            raise TimeoutError("no cards")


class _StubWorker:
    """Only what nav_back touches."""

    CARD_SELECTOR = ApecWorker.CARD_SELECTOR
    RESULTS_BACK_LINK = ApecWorker.RESULTS_BACK_LINK
    nav_back = ApecWorker.nav_back

    def __init__(self, page):
        self.page = page
        self.actions = []
        self.logs = []

    def _plog(self, message, user_id=None):
        self.logs.append(message)

    async def _click(self, locator, hesitation=True):
        self.actions.append("click_back_link")

    async def _handle_cookies(self):
        pass


def _steps(page):
    return [name for name, _ in page.calls]


@pytest.mark.asyncio
async def test_the_interstitial_costs_one_step_back():
    """`at_redir_page` means the current page is a redirect, not the offer. One
    step back is spent leaving it before anything else can be found."""
    page = _FakePage()
    worker = _StubWorker(page)

    await worker.nav_back(at_redir_page=True)

    assert _steps(page).count("go_back") == 1


@pytest.mark.asyncio
async def test_a_missing_back_link_costs_a_second_step():
    """The depth of that stack varies with how the offer was opened, so when the
    link is still not there after one step, another is taken."""
    page = _FakePage(back_link_visible=False)
    worker = _StubWorker(page)

    await worker.nav_back(at_redir_page=True)

    assert _steps(page).count("go_back") == 2
    assert any("getting one page back" in message for message in worker.logs)


@pytest.mark.asyncio
async def test_the_back_link_is_what_returns_to_the_list():
    """Going back lands somewhere adjacent to the results, not on them. The
    anchor is the step that actually arrives, and it is clicked through the
    OS-level pointer like any other element."""
    page = _FakePage()
    worker = _StubWorker(page)

    await worker.nav_back(has_back_button=True)

    assert worker.actions == ["click_back_link"]
    assert ("locator", ApecWorker.RESULTS_BACK_LINK) in page.calls


@pytest.mark.asyncio
async def test_a_missing_back_link_is_not_an_error():
    """Back sometimes lands straight on the results. The card check below is the
    real arbiter, so a missing link is not a failure on its own."""
    page = _FakePage(back_link_visible=False)
    worker = _StubWorker(page)

    await worker.nav_back(has_back_button=True)

    assert worker.actions == []
    assert any("no back link" in message for message in worker.logs)


@pytest.mark.asyncio
async def test_neither_flag_still_checks_the_cards():
    """The flags say what stands between here and the list; the card check runs
    either way, because it is what decides the list is actually back."""
    page = _FakePage()
    worker = _StubWorker(page)

    await worker.nav_back()

    assert _steps(page).count("go_back") == 0
    assert worker.actions == []


@pytest.mark.asyncio
async def test_missing_cards_are_waited_out_not_reloaded():
    """The recovery re-waits. It used to reload, and the log still said so long
    after the reload was gone — a message describing a step that no longer runs
    is worse than no message."""
    for flags in ({}, {"at_redir_page": True}, {"has_back_button": True}):
        page = _FakePage(cards_visible=False)
        worker = _StubWorker(page)

        await worker.nav_back(**flags)

        assert ("reload", None) not in page.calls, f"reloaded for {flags}"
        assert any("page state may be broken" in m for m in worker.logs), flags


def test_nav_back_takes_no_url():
    """It navigates by history and by clicking a link, so there is no URL for it
    to use. The parameter outlived the code that read it."""
    import inspect

    parameters = inspect.signature(ApecWorker.nav_back).parameters
    assert "url" not in parameters
    assert parameters["at_redir_page"].default is False
    assert parameters["has_back_button"].default is False


def test_every_caller_says_what_stands_in_the_way():
    """A bare `nav_back()` inside the card loop would check for cards that are
    two pages away. Each site knows its own shape and has to declare it."""
    tree = ast.parse(SOURCE.read_text())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "nav_back"
    ]

    assert calls, "no nav_back call sites found"
    for call in calls:
        assert not call.args, f"nav_back at line {call.lineno} passes a positional arg"
        flags = {kw.arg for kw in call.keywords}
        assert flags <= {"at_redir_page", "has_back_button"}, flags
        assert flags, f"nav_back at line {call.lineno} declares nothing"
