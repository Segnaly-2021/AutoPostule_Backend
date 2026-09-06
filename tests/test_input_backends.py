"""The input backend seam, and the arithmetic that decides where a click lands.

The mapping and the wheel quantisation are the two places where the X11 backend
can be silently, invisibly wrong — a click that lands 40px off still "works", it
just hits the wrong element, and a scroll that drops sub-notch requests scrolls
nothing at all while reporting success. Both were real bugs caught by
scripts/input_probe.py; these tests pin the fixes.
"""
import pytest

from auto_apply_app.infrastructures.agent.input.backend import (
    BACKEND_CDP,
    BACKEND_X11,
    attach_backend,
    detach_backend,
    get_backend,
    resolve_backend_name,
)
from auto_apply_app.infrastructures.agent.input.cdp_backend import CdpInputBackend


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _capability(monkeypatch, capable):
    """Pin what the machine can do, so selection is tested rather than the box
    the suite happens to run on."""
    import auto_apply_app.infrastructures.agent.input.backend as mod
    monkeypatch.setattr(mod, "x11_capable", lambda: (capable, "pinned"))


@pytest.mark.parametrize("value", ["x11", "X11", "xtest", "os"])
def test_x11_can_be_forced_on_a_machine_that_cannot_do_it(monkeypatch, value):
    """The env var is an override, not a hint. Forcing it on a box without the
    tooling has to reach the launch path, because that is where the failure is
    reported — resolving to CDP here would hide a misconfigured deployment."""
    _capability(monkeypatch, False)
    monkeypatch.setenv("AGENT_INPUT_BACKEND", value)
    assert resolve_backend_name() == BACKEND_X11


def test_cdp_can_be_forced_on_a_machine_that_could_do_x11(monkeypatch):
    """This is the rollback lever from the plan. Detection must never take it
    away."""
    _capability(monkeypatch, True)
    monkeypatch.setenv("AGENT_INPUT_BACKEND", "cdp")
    assert resolve_backend_name() == BACKEND_CDP


@pytest.mark.parametrize("value", [None, "", "auto", "detect", "nonsense"])
def test_without_an_explicit_choice_the_machine_decides(monkeypatch, value):
    """Unset, blank, 'auto' and a typo all detect.

    This is the bug that made the whole OS-level path dead code: the old default
    was CDP, nothing ever set the var, and so nothing ever ran on x11 in either
    environment. A typo lands here too — it is a reason to look at the machine,
    not a reason to pick the weaker backend and say nothing."""
    if value is None:
        monkeypatch.delenv("AGENT_INPUT_BACKEND", raising=False)
    else:
        monkeypatch.setenv("AGENT_INPUT_BACKEND", value)

    _capability(monkeypatch, True)
    assert resolve_backend_name() == BACKEND_X11

    _capability(monkeypatch, False)
    assert resolve_backend_name() == BACKEND_CDP


def test_capability_names_what_is_missing(monkeypatch):
    """The reason is logged and shown by preflight, so it has to be specific
    enough to act on."""
    import auto_apply_app.infrastructures.agent.input.backend as mod

    monkeypatch.setattr(mod.sys, "platform", "darwin")
    capable, why = mod.x11_capable()
    assert capable is False and "darwin" in why

    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.shutil, "which", lambda tool: None if tool == "xdotool" else "/usr/bin/x")
    capable, why = mod.x11_capable()
    assert capable is False and "xdotool" in why


def test_a_page_with_no_backend_still_clicks():
    """Defaulting rather than raising: a helper called on a page whose worker
    never attached anything must behave exactly as it did before the seam."""
    page = object()
    try:
        assert get_backend(page).name == BACKEND_CDP
    finally:
        detach_backend(page)


def test_attach_replaces_the_default():
    page = object()
    sentinel = CdpInputBackend(page)
    try:
        attach_backend(page, sentinel)
        assert get_backend(page) is sentinel
    finally:
        detach_backend(page)


# ---------------------------------------------------------------------------
# CDP
# ---------------------------------------------------------------------------

class _RecordingLocator:
    def __init__(self):
        self.calls = []

    async def press_sequentially(self, char, delay=0):
        self.calls.append(("type", char, delay))

    async def press(self, key, delay=0):
        self.calls.append(("press", key, delay))


@pytest.mark.asyncio
async def test_cdp_passes_the_dwell_as_playwright_delay():
    """Playwright's per-character `delay` is the gap between THAT key's keydown
    and keyup — the hold. Passing 0 released every key ~2ms after pressing it,
    which is what the probe measured before this was threaded through."""
    loc = _RecordingLocator()
    backend = CdpInputBackend(page=None)

    await backend.type_char("a", locator=loc, dwell_s=0.085)
    await backend.key_press("Enter", 0.07, locator=loc)

    assert loc.calls[0] == ("type", "a", 85.0)
    assert loc.calls[1] == ("press", "Enter", 70.0)


# ---------------------------------------------------------------------------
# X11 geometry and wheel
# ---------------------------------------------------------------------------

def _x11(scale=(1.0, 1.0), origin=(0.0, 0.0)):
    from auto_apply_app.infrastructures.agent.input.x11_backend import X11InputBackend

    class _FakeDisplay:
        def sync(self):
            pass

    return X11InputBackend(
        page=None, display=_FakeDisplay(), window=None,
        display_name=":99", scale=scale, origin=origin,
    )


def test_viewport_maps_onto_the_root_window():
    backend = _x11(scale=(1.0, 1.0), origin=(12.0, 87.0))
    # The vertical origin is the browser chrome: the probe measures 87px of tab
    # strip and omnibox above the viewport, which is exactly the offset CDP
    # reports as zero because a headless window has no position at all.
    assert backend.to_root(0, 0) == (12, 87)
    assert backend.to_root(100, 200) == (112, 287)


def test_a_retina_persona_maps_at_its_own_scale():
    """device_scale_factor is a real Playwright setting, so CSS pixels are not X
    pixels for any persona that is not at 1.0."""
    backend = _x11(scale=(2.0, 2.0), origin=(0.0, 74.0))
    assert backend.to_root(50, 50) == (100, 174)


@pytest.mark.asyncio
async def test_the_wheel_carries_its_remainder(monkeypatch):
    """human_scroll shapes a velocity profile out of many small deltas, most of
    them below one notch. Dropping sub-notch requests made scrolling silently do
    nothing at all; carrying them means a run of small steps still travels the
    distance asked for, quantised the way a wheel quantises it.
    """
    pytest.importorskip("Xlib")
    clicks = []

    def fake_input(display, event_type, detail=0, **kwargs):
        if event_type == 4:  # ButtonPress
            clicks.append(detail)

    monkeypatch.setattr("Xlib.ext.xtest.fake_input", fake_input)
    backend = _x11()

    # Six 40px nudges = 240px = exactly two notches at 120px each.
    for _ in range(6):
        await backend.wheel(0, 40)

    assert clicks == [5, 5], f"expected two wheel-down clicks, got {clicks}"
    assert abs(backend._wheel_residual) < 1e-6


@pytest.mark.asyncio
async def test_a_single_sub_notch_scroll_emits_nothing_yet(monkeypatch):
    """It must not round up either — a 40px request is not a 120px scroll."""
    pytest.importorskip("Xlib")
    clicks = []

    monkeypatch.setattr(
        "Xlib.ext.xtest.fake_input",
        lambda display, event_type, detail=0, **kw: clicks.append(detail)
        if event_type == 4 else None,
    )
    backend = _x11()

    await backend.wheel(0, 40)
    assert clicks == []
    assert backend._wheel_residual == 40


@pytest.mark.asyncio
async def test_scrolling_back_up_nets_off_against_the_remainder(monkeypatch):
    """Direction changes must not leave a stale residual pushing the next scroll
    the wrong way."""
    pytest.importorskip("Xlib")
    clicks = []

    monkeypatch.setattr(
        "Xlib.ext.xtest.fake_input",
        lambda display, event_type, detail=0, **kw: clicks.append(detail)
        if event_type == 4 else None,
    )
    backend = _x11()

    await backend.wheel(0, 80)    # down, under a notch
    await backend.wheel(0, -80)   # back up, cancels
    assert clicks == []
    assert backend._wheel_residual == 0


def test_playwright_key_names_translate_to_x_names():
    from auto_apply_app.infrastructures.agent.input.x11_backend import _xdotool_key

    assert _xdotool_key("Enter") == "Return"
    assert _xdotool_key("Backspace") == "BackSpace"
    # Lowercased: xdotool reads a capital as shift+a, so "ctrl+A" would send
    # ctrl+shift+a — a different shortcut than the one Playwright names.
    assert _xdotool_key("Control+A") == "ctrl+a"
    assert _xdotool_key("Alt+ArrowLeft") == "alt+Left"
    assert _xdotool_key("Tab") == "Tab"


@pytest.mark.asyncio
async def test_chords_are_pressed_atomically():
    """A chord built from keydown/sleep/keyup leaves the modifier logically HELD
    in the X server. Every punctuation character after it then arrives as
    Ctrl+<char> — a dead shortcut that silently types nothing — while letters
    keep working, which is what made it look like a punctuation bug rather than a
    stuck modifier. Measured: a typed URL came back as 'http1270019typedhere'.
    """
    backend = _x11()
    invocations = []

    async def fake_xdotool(*args, stdin_text=None):
        invocations.append(args)

    backend._xdotool = fake_xdotool

    await backend.key_press("Control+A", 0.05)
    assert [a[0] for a in invocations] == ["key"], (
        f"a chord must be one atomic `key`, got {invocations}"
    )
    assert invocations[0][-1] == "ctrl+a"


@pytest.mark.asyncio
async def test_single_keys_keep_their_dwell():
    """Only chords give up the hold; a lone key is still pressed and released
    around a real dwell."""
    backend = _x11()
    invocations = []

    async def fake_xdotool(*args, stdin_text=None):
        invocations.append(args)

    backend._xdotool = fake_xdotool

    await backend.key_press("Enter", 0.01)
    assert [a[0] for a in invocations] == ["keydown", "keyup"]
    assert invocations[0][-1] == "Return"


@pytest.mark.asyncio
async def test_punctuation_is_typed_through_stdin():
    """`xdotool type` has no `--` option terminator, so a character that looks
    like a flag cannot be passed positionally — and passing `--` anyway got it
    swallowed, dropping every colon, slash and dot from a typed URL."""
    backend = _x11()
    invocations = []

    async def fake_xdotool(*args, stdin_text=None):
        invocations.append((args, stdin_text))

    backend._xdotool = fake_xdotool

    await backend.type_char(":", dwell_s=0.08)
    args, stdin_text = invocations[0]
    assert args[0] == "type" and "--file" in args and "-" in args
    assert stdin_text == ":"
    assert "--" not in args, "the option terminator xdotool does not support is back"


@pytest.mark.asyncio
async def test_letters_still_take_the_dwell_path():
    backend = _x11()
    invocations = []

    async def fake_xdotool(*args, stdin_text=None):
        invocations.append(args)

    backend._xdotool = fake_xdotool

    await backend.type_char("a", dwell_s=0.01)
    assert [a[0] for a in invocations] == ["keydown", "keyup"]


def test_silent_xdotool_failures_are_not_treated_as_success():
    """`xdotool keydown ':'` exits 0 while printing "No such key name" and
    pressing nothing. Trusting the return code is how the colons went missing."""
    import inspect
    from auto_apply_app.infrastructures.agent.input import x11_backend

    source = inspect.getsource(x11_backend.X11InputBackend._xdotool)
    assert "No such key name" in source and "Invalid key sequence" in source


# ---------------------------------------------------------------------------
# Finding the browser window
#
# Measured on WSLg, whose Weston WM reparents every window into a frame:
#
#   id=0x20003e  2644x1511+-32+-32  class=None            <- the frame
#     id=0x600004  2568x1414+38+59  class=Google-chrome   <- the browser
#
# The frame is LARGER than the browser inside it, so "largest viewable window"
# always picks the wrapper. Weston also parks an 8192x8192 window on root.
# ---------------------------------------------------------------------------

class _FakeAttrs:
    def __init__(self, viewable=True, override=False):
        self.map_state = 2 if viewable else 0  # X.IsViewable == 2
        self.override_redirect = override


class _FakeGeom:
    def __init__(self, w, h, x=0, y=0):
        self.width, self.height, self.x, self.y = w, h, x, y


class _FakeWindow:
    def __init__(self, id, geom, wm_class=None, viewable=True, override=False, children=()):
        self.id = id
        self._geom = geom
        self._class = wm_class
        self._attrs = _FakeAttrs(viewable, override)
        self._children = list(children)

    def query_tree(self):
        return type("_", (), {"children": self._children})()

    def get_attributes(self):
        return self._attrs

    def get_geometry(self):
        return self._geom

    def get_wm_class(self):
        return self._class


class _FakeX:
    IsViewable = 2


def _weston_tree():
    """Root as it really looks on WSLg with Chrome up."""
    browser = _FakeWindow(0x600004, _FakeGeom(2568, 1414, 38, 59),
                          ("google-chrome (/tmp/profile-OB3vCQ)", "Google-chrome"))
    frame = _FakeWindow(0x20003E, _FakeGeom(2644, 1511, -32, -32), None,
                        children=[browser])
    return _FakeWindow(0x390, _FakeGeom(1920, 1080), None, children=[
        _FakeWindow(0x200002, _FakeGeom(8192, 8192), None),          # Weston overlay
        _FakeWindow(0x200027, _FakeGeom(10, 10), None),              # "Weston WM"
        frame,
    ]), browser


def _conn_for(root, w=1920, h=1080):
    screen = type("_", (), {"root": root, "width_in_pixels": w, "height_in_pixels": h})()
    return type("_", (), {"screen": lambda self=None: screen})()


def test_the_browser_is_found_inside_its_window_manager_frame():
    """A WM_CLASS match beats any size comparison, and the search has to descend
    into the frame to find it at all."""
    from auto_apply_app.infrastructures.agent.input.x11_backend import X11InputBackend

    root, browser = _weston_tree()
    window, geometry = X11InputBackend._find_browser_window(_conn_for(root), _FakeX)

    assert window is browser, f"picked {window.id:#x}, not the browser"
    assert (geometry.width, geometry.height) == (2568, 1414)


def test_a_window_larger_than_the_screen_is_never_the_answer():
    """Weston's 8192x8192 window is 33x any browser on a 1920x1080 screen. With
    no WM_CLASS to match on, size alone would hand it the run."""
    from auto_apply_app.infrastructures.agent.input.x11_backend import X11InputBackend

    real = _FakeWindow(0x1, _FakeGeom(1200, 800, 10, 10), None)
    root = _FakeWindow(0x390, _FakeGeom(1920, 1080), None, children=[
        _FakeWindow(0x200002, _FakeGeom(8192, 8192), None),
        real,
    ])

    window, _ = X11InputBackend._find_browser_window(_conn_for(root), _FakeX)
    assert window is real


def test_menus_and_tooltips_are_skipped():
    """Override-redirect windows are popups. A pointer mapped onto one would be
    aimed at something that disappears."""
    from auto_apply_app.infrastructures.agent.input.x11_backend import X11InputBackend

    browser = _FakeWindow(0x2, _FakeGeom(1000, 700), ("chromium", "Chromium"))
    root = _FakeWindow(0x390, _FakeGeom(1920, 1080), None, children=[
        _FakeWindow(0x3, _FakeGeom(1800, 1000), ("chromium", "Chromium"), override=True),
        browser,
    ])

    window, _ = X11InputBackend._find_browser_window(_conn_for(root), _FakeX)
    assert window is browser


def test_an_unmapped_window_is_not_a_candidate():
    from auto_apply_app.infrastructures.agent.input.x11_backend import X11InputBackend

    root = _FakeWindow(0x390, _FakeGeom(1920, 1080), None, children=[
        _FakeWindow(0x4, _FakeGeom(1600, 900), ("chromium", "Chromium"), viewable=False),
    ])

    window, _ = X11InputBackend._find_browser_window(_conn_for(root), _FakeX)
    assert window is None
