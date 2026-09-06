"""Which X display a browser lands on.

Not a detail. The same code has to produce a window a person can WATCH on a dev
box and a private framebuffer in a container, and it has to decide that without
being told — an Xvfb on a dev box makes the run invisible, which is exactly what
running locally before a deploy is for.
"""
import pytest

from auto_apply_app.infrastructures.agent.input import display as mod
# Bound before the autouse fixture patches the module attribute, so this one
# test can still reach the real implementation.
from auto_apply_app.infrastructures.agent.input.display import (
    display_has_window_manager as _real_has_wm,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """No real X server, and no claim leaking between tests."""
    mod._borrowed_by.clear()
    monkeypatch.setattr(mod, "display_reachable", lambda name: True)
    # Unmanaged by default: these tests are about WHICH display is chosen, not
    # about the window-manager rule, which has its own tests below.
    monkeypatch.setattr(mod, "display_has_window_manager", lambda name: False)
    monkeypatch.setattr(mod.BorrowedDisplay, "_geometry", staticmethod(lambda name: (1920, 1080)))
    yield
    mod._borrowed_by.clear()


class _NoXvfb:
    """Stands in for a started Xvfb, so a test never spawns a server."""

    def __init__(self, width, height, **kw):
        self.display, self.width, self.height = ":99", width, height
        self.stopped = False

    def start(self):
        return self

    def stop(self):
        self.stopped = True


def test_a_dev_box_with_a_screen_gets_that_screen(monkeypatch):
    """WSLg sets DISPLAY=:0. Inheriting it is what makes the run visible."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")

    display = mod.open_display(1600, 900)

    assert display.display == ":0"
    assert getattr(display, "borrowed", False) is True


def test_a_persona_bigger_than_the_screen_still_gets_the_screen(monkeypatch):
    """The window is made to fit instead — see tests/test_viewport_fit.py.

    Refusing to borrow was the earlier answer to an oversized persona, and it was
    the wrong lever: it cost the visible window, which is the only reason to run
    on a dev box at all. Shrinking the viewport keeps both the visibility and the
    guarantee that every part of the window is somewhere a pointer can reach."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    display = mod.open_display(2560, 1440)

    assert display.display == ":0"
    assert getattr(display, "borrowed", False) is True


def test_an_exact_fit_is_still_borrowed(monkeypatch):
    """The boundary belongs on the usable side: a persona the same size as the
    screen fits on it."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    assert mod.open_display(1920, 1080).display == ":0"


def test_a_container_with_no_screen_starts_its_own(monkeypatch):
    """Cloud Run has no DISPLAY, so the same code starts an Xvfb without anyone
    configuring it."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    display = mod.open_display(1440, 900)

    assert display.display == ":99"
    assert (display.width, display.height) == (1440, 900)


def test_the_framebuffer_is_sized_to_the_persona(monkeypatch):
    """Only when we start it. The spoofed screen.width has to describe something
    that exists, and a display we started is one we can size."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    assert mod.open_display(3840, 2160).height == 2160


def test_one_borrowed_display_serves_exactly_one_browser(monkeypatch):
    """A display owns one pointer and one input focus, and boards fan out
    concurrently. Two browsers sharing a cursor is a correctness failure, not a
    tuning problem — so the second one gets its own Xvfb."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    first = mod.open_display(1920, 1080)
    second = mod.open_display(1920, 1080)

    assert first.display == ":0"
    assert second.display == ":99", "two browsers were pointed at one cursor"


def test_releasing_a_borrowed_display_lets_the_next_run_have_it(monkeypatch):
    """The claim is for the life of a browser, not the life of the process."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")

    first = mod.open_display(1920, 1080)
    first.stop()

    assert mod.open_display(1920, 1080).display == ":0"


def test_stopping_a_borrowed_display_does_not_kill_it(monkeypatch):
    """We did not start the dev box's X server and we do not get to end it."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")

    display = mod.open_display(1920, 1080)
    display.stop()  # must not raise, must not terminate anything

    assert display.display == ":0"


@pytest.mark.parametrize("value", ["none", "off", "xvfb", "NONE"])
def test_borrowing_can_be_refused(monkeypatch, value):
    """The way to get a private framebuffer on a machine that has a screen —
    for a headless-shaped test, or to run boards in parallel locally."""
    monkeypatch.setenv("AGENT_X11_DISPLAY", value)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    assert mod.open_display(1920, 1080).display == ":99"


def test_an_explicit_display_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("AGENT_X11_DISPLAY", ":7")
    monkeypatch.setenv("DISPLAY", ":0")

    assert mod.open_display(1920, 1080).display == ":7"


def test_an_unreachable_display_is_not_borrowed(monkeypatch):
    """DISPLAY set but dead is common — a stale value in a shell profile. Trying
    to launch onto it fails the whole browser, not just the input."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "display_reachable", lambda name: False)
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    assert mod.open_display(1920, 1080).display == ":99"


# ---------------------------------------------------------------------------
# Never two kinds of X server in one process
#
# python-xlib registers extension methods on shared class objects at connect
# time. Xvfb + Xvfb is fine (measured); Xwayland + Xvfb corrupts that state, and
# the second Display() raises "'type' object does not support item assignment".
# Boards fan out in ONE process, so this is reachable in a normal dev run.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_own_display():
    mod._started_own_display = False
    yield
    mod._started_own_display = False


def test_once_a_process_starts_an_xvfb_it_stops_borrowing(monkeypatch):
    """An oversized persona forces an Xvfb; a later board that WOULD fit must not
    then borrow :0, because the two cannot coexist. Losing the visible window is
    a small price against a board silently dropping to CDP."""
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    monkeypatch.setenv("AGENT_X11_DISPLAY", "none")
    first = mod.open_display(1600, 900)          # forced onto its own Xvfb
    monkeypatch.delenv("AGENT_X11_DISPLAY")
    second = mod.open_display(1600, 900)         # :0 is free, but must not be taken

    assert first.display == ":99"
    assert second.display == ":99", "borrowed :0 alongside an Xvfb"
    assert ":0" not in mod._borrowed_by


def test_the_other_order_is_warned_about(monkeypatch, caplog):
    """Borrow first, then need an Xvfb: unavoidable, so it has to be loud and
    name the fix rather than let the board fail mysteriously."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    assert mod.open_display(1600, 900).display == ":0"
    monkeypatch.setenv("AGENT_X11_DISPLAY", "none")
    with caplog.at_level("WARNING"):
        assert mod.open_display(1600, 900).display == ":99"

    assert any("AGENT_X11_DISPLAY=none" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# A display someone else manages
#
# XTEST delivers pointer events by POSITION but key events by FOCUS. On a
# managed desktop focus belongs to the user's window manager, and on a rootless
# one (WSLg/Weston) to the host OS outright. Measured there with focus held by
# another window: `xdotool windowactivate`, `xdotool windowfocus`,
# `XSetInputFocus`, a pointer move and a full XTEST click ALL failed to bring it
# back, and typing into a clicked field produced '' instead of its text.
#
# So the mouse keeps working while every keystroke goes to whatever the user
# last clicked — a run that clicks its way through an application accurately and
# types none of it.
# ---------------------------------------------------------------------------

def test_a_managed_display_is_still_borrowed(monkeypatch):
    """Seeing the run wins. The window-manager check warns rather than vetoes.

    The trade is real and was made deliberately: keystrokes follow focus, so
    clicking another window mid-run sends them there. Being able to watch the
    browser is worth more than being protected from a failure with a known shape
    and a loud warning."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "display_has_window_manager", lambda name: True)
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    assert mod.open_display(1600, 900).display == ":0"


def test_borrowing_a_managed_display_warns_about_focus(monkeypatch, caplog):
    """The trade has to be stated where someone will read it. A run that loses
    every keystroke and says nothing is the outcome this exists to prevent."""
    monkeypatch.delenv("AGENT_X11_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "display_has_window_manager", lambda name: True)

    with caplog.at_level("WARNING"):
        mod.open_display(1600, 900)

    message = " ".join(r.message for r in caplog.records)
    assert "focus" in message and "AGENT_X11_DISPLAY=none" in message


def test_off_screen_is_still_one_setting_away(monkeypatch):
    """For a run nobody will be sitting in front of."""
    monkeypatch.setenv("AGENT_X11_DISPLAY", "none")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(mod, "display_has_window_manager", lambda name: True)
    monkeypatch.setattr(mod, "XvfbDisplay", _NoXvfb)

    assert mod.open_display(1600, 900).display == ":99"


def test_an_unanswerable_display_is_treated_as_managed(monkeypatch):
    """Guessing 'unmanaged' costs every keystroke; guessing 'managed' costs the
    visible window. Guess the cheap way."""
    monkeypatch.setattr(mod.subprocess, "run", _raise)

    assert _real_has_wm(":0") is True


def _raise(*a, **kw):
    raise OSError("xprop missing")


# ---------------------------------------------------------------------------
# Watching a run
#
# The browser cannot live on the developer's own display — a window-managed one
# cannot hold keyboard focus for us. So the run is SERVED from its Xvfb instead,
# and the viewer is an ordinary client of the desktop. Clicking, moving or
# closing that viewer cannot disturb the run, because it is not on the run's
# display at all.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "window", "WINDOW"])
def test_the_friendly_values_open_a_window(monkeypatch, value):
    monkeypatch.setenv("AGENT_X11_VIEW", value)
    assert mod.viewer_mode() == mod.VIEW_WINDOW


@pytest.mark.parametrize("value", ["serve", "vnc"])
def test_serving_without_opening_a_window(monkeypatch, value):
    """For attaching a client from Windows, or from another machine."""
    monkeypatch.setenv("AGENT_X11_VIEW", value)
    assert mod.viewer_mode() == mod.VIEW_SERVE


@pytest.mark.parametrize("value", [None, "", "off", "nonsense"])
def test_watching_is_off_unless_asked_for(monkeypatch, value):
    """It costs a process and a listening port. Neither belongs in a production
    worker that nobody is watching."""
    if value is None:
        monkeypatch.delenv("AGENT_X11_VIEW", raising=False)
    else:
        monkeypatch.setenv("AGENT_X11_VIEW", value)
    assert mod.viewer_mode() == mod.VIEW_OFF


def test_each_display_gets_its_own_port():
    """Boards fan out concurrently, so two runs must not fight over one port."""
    ports = {mod._DisplayViewer(f":{n}").port for n in (101, 102, 103)}
    assert len(ports) == 3
    assert all(p > 5900 for p in ports)


def test_the_viewer_is_torn_down_with_the_display():
    """x11vnc holds a port open for as long as it lives. A leaked one blocks the
    next run's viewer and keeps serving a browser that no longer exists."""
    viewer = mod._DisplayViewer(":101")
    viewer.stop()  # nothing started: must not raise

    class _Proc:
        def __init__(self): self.killed = False
        def poll(self): return None
        def terminate(self): self.killed = True
        def wait(self, timeout=None): return 0

    server, window = _Proc(), _Proc()
    viewer._server, viewer._viewer = server, window
    viewer.stop()

    assert server.killed and window.killed
    assert viewer._server is None and viewer._viewer is None
