"""A click that opens a window the flow never asked for.

Under CDP a spare window was harmless — events go to a Page object. Under XTEST
clicks are delivered by POINTER POSITION, so a popup covering the browser
receives every click that follows while the code goes on addressing the original
page. One orphaned window silently breaks the rest of the run, which is why this
only started mattering with the OS-level backend.
"""
import asyncio
import inspect

import pytest

from auto_apply_app.infrastructures.agent import human_behavior as hb
from auto_apply_app.infrastructures.agent.pacing import HumanPacing


class _FakeContext:
    def __init__(self):
        self.handlers = []

    def on(self, event, handler):
        assert event == "page", event
        self.handlers.append(handler)

    def emit_page(self, page):
        for h in self.handlers:
            h(page)


class _FakePage:
    def __init__(self, context, url="about:blank", opener=None):
        self.context = context
        self.url = url
        self.closed = False
        self.raised = 0
        self._opener = opener

    async def close(self):
        self.closed = True

    async def bring_to_front(self):
        self.raised += 1

    async def opener(self):
        return self._opener


@pytest.fixture(autouse=True)
def _clean():
    hb._strays.clear()
    hb._watched.clear()
    yield
    hb._strays.clear()
    hb._watched.clear()


def test_the_listener_is_registered_once_per_context():
    """Not per click. A run makes on the order of seventy clicks, and wrapping
    each in an expect_popup-style await would add its timeout to every one."""
    ctx = _FakeContext()
    page = _FakePage(ctx)

    for _ in range(5):
        hb.watch_for_stray_pages(page)

    assert len(ctx.handlers) == 1


def test_a_stray_is_closed_and_the_main_window_raised():
    """Closing is half of it. The pointer clicks whatever window is topmost, so
    a stray left in front would keep swallowing clicks after it was 'handled'."""
    ctx = _FakeContext()
    page = _FakePage(ctx)
    hb.watch_for_stray_pages(page)

    mark = hb._stray_count(page)
    stray = _FakePage(ctx, url="https://example.com/popup", opener=page)
    ctx.emit_page(stray)

    closed = asyncio.run(hb._close_strays_since(page, mark))

    assert closed == ["https://example.com/popup"]
    assert stray.closed is True
    assert page.raised == 1


def test_a_page_opened_by_somebody_else_is_left_alone():
    """Only this click's stray is this click's business."""
    ctx = _FakeContext()
    page = _FakePage(ctx)
    other = _FakePage(ctx)
    hb.watch_for_stray_pages(page)

    mark = hb._stray_count(page)
    ctx.emit_page(_FakePage(ctx, url="https://example.com/theirs", opener=other))

    assert asyncio.run(hb._close_strays_since(page, mark)) == []


def test_strays_are_consumed_so_one_window_is_handled_once():
    ctx = _FakeContext()
    page = _FakePage(ctx)
    hb.watch_for_stray_pages(page)

    mark = hb._stray_count(page)
    ctx.emit_page(_FakePage(ctx, opener=page))
    assert asyncio.run(hb._close_strays_since(page, mark)) != []
    assert asyncio.run(hb._close_strays_since(page, hb._stray_count(page))) == []


def test_the_retry_can_be_refused_by_the_caller():
    """`retry_on_popup=False` exists for actions that must not happen twice.
    A repeated submit is an application sent twice, which no cleanup undoes."""
    for fn in (hb.human_hover_and_click, hb.human_click, HumanPacing._click):
        assert "retry_on_popup" in inspect.signature(fn).parameters, fn.__name__


def test_the_submit_buttons_ask_for_no_retry():
    """Every live worker already declares 'no retry: duplicate risk' at its
    submit click. A blanket retry in the shared click helper would have
    overridden an invariant the workers had written down."""
    import ast
    from pathlib import Path

    worker_dir = Path("auto_apply_app/infrastructures/agent/workers")
    for path in (worker_dir / "apec" / "apec_worker.py",
                 worker_dir / "wttj" / "wttj_worker.py",
                 worker_dir / "hellowork" / "hw_worker.py"):
        tree = ast.parse(path.read_text())
        refused = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and any(kw.arg == "retry_on_popup" and kw.value.value is False
                    for kw in node.keywords)
        ]
        assert refused, f"{path.name}: submit click may be retried into a duplicate"


# ---------------------------------------------------------------------------
# Not creating the window in the first place
#
# Closing a stray afterwards cannot undo the interval it existed for. It takes
# focus, and on a window-managed display focus cannot be taken back — measured:
# xdotool windowactivate, windowfocus, XSetInputFocus, a pointer move and a full
# XTEST click all failed. Meanwhile XTEST clicks go to whatever window is
# topmost, so the stray receives them.
# ---------------------------------------------------------------------------

def test_the_suppressor_is_an_iife():
    """`add_init_script` evaluates a string as SOURCE, not as a function to
    call. Written as a bare `() => {...}` it builds a function value, discards
    it, and silently does nothing — which is exactly how the first version of
    this shipped and passed every unit test."""
    from auto_apply_app.infrastructures.agent.human_behavior import SUPPRESS_POPUPS_JS

    body = SUPPRESS_POPUPS_JS.strip()
    assert body.startswith("(("), body[:40]
    assert body.endswith(")();"), body[-40:]


def test_the_suppressor_hides_its_own_override():
    """A `window.open` whose toString reads as our source is a cheaper tell than
    the window would have been."""
    from auto_apply_app.infrastructures.agent.human_behavior import SUPPRESS_POPUPS_JS

    assert "toString" in SUPPRESS_POPUPS_JS
    assert "nativeOpen" in SUPPRESS_POPUPS_JS


def test_every_worker_installs_it_unconditionally():
    """Not inside `if fingerprint:`. A run without a fingerprint is already
    degraded and must not also lose control of its display."""
    import ast
    from pathlib import Path

    worker_dir = Path("auto_apply_app/infrastructures/agent/workers")
    for path in (worker_dir / "apec" / "apec_worker.py",
                 worker_dir / "wttj" / "wttj_worker.py",
                 worker_dir / "hellowork" / "hw_worker.py"):
        tree = ast.parse(path.read_text())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "_suppress_popups"
        ]
        assert len(calls) == 2, f"{path.name}: {len(calls)} call(s), expected one per context"

        # ...and none of them nested under a fingerprint check.
        for branch in [n for n in ast.walk(tree) if isinstance(n, ast.If)]:
            src = ast.dump(branch.test)
            if "fingerprint" not in src:
                continue
            nested = [
                n for n in ast.walk(branch)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", "") == "_suppress_popups"
            ]
            assert not nested, f"{path.name}: suppressor gated on a fingerprint"
