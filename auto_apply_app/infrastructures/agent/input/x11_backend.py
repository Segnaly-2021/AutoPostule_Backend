"""OS-level input: XTEST fake events against the X display the browser runs on.

The difference from the CDP backend is provenance. `Input.dispatchMouseEvent`
hands Chrome an event that no input device produced; XTEST events enter through
the X server's own input pipeline, so the browser receives them exactly as it
receives a real mouse and keyboard. A pointer genuinely exists, it genuinely
travels the path we draw, and `screenX`/`screenY` are real coordinates on a real
screen rather than values derived for a window that has no position.

Two mechanisms, chosen per job:

- **Mouse: python-xlib.** A move emits 15-40 motion events, and each one has to
  land within milliseconds of the last. In-process XTEST calls cost microseconds;
  a subprocess per event would cost ~10 ms and destroy the velocity profile that
  is the whole point.
- **Keyboard: xdotool.** Cover letters are French, and `é è à ç ù` are not in the
  default keymap. xdotool already does the temporary-keycode remap that typing
  them requires; hand-rolling keysym allocation in python-xlib to save a
  subprocess per keystroke would be false economy at 35-300 ms between keys.

`xdotool type` is deliberately used WITHOUT `--window`. That flag switches it to
XSendEvent, which produces events flagged `send_event=True` — Chrome ignores
those, and a page that received them would be able to tell. Without it, xdotool
uses XTEST and the keystrokes go to whatever holds input focus, which is why one
browser per display is a requirement rather than a convenience.
"""
import asyncio
import logging
import os
import shutil
import uuid
from types import SimpleNamespace

from auto_apply_app.infrastructures.agent.input.backend import BACKEND_X11

logger = logging.getLogger(__name__)

# What one wheel notch is worth in Chrome on X11, measured with
# scripts/input_probe.py: the page reports deltaY 120 per button-4/5 click.
# XTEST can only deliver discrete clicks while the rest of the codebase reasons
# in pixels, so this is the exchange rate — and getting it wrong is not cosmetic.
# At the 53 this started as, every scroll travelled 2.3x further than asked for.
# Tunable because it follows the desktop's scroll settings.
PIXELS_PER_NOTCH = float(os.getenv("AGENT_X11_WHEEL_NOTCH_PX", "120"))

# How a browser names itself in WM_CLASS. Chrome reports its profile directory
# in the first field ("google-chrome (/tmp/...)"), so these are matched as
# substrings rather than compared for equality.
BROWSER_WM_CLASSES = ("chromium", "google-chrome", "chrome", "msedge", "microsoft-edge")

# A reparenting WM puts the browser one level inside a frame. Two is enough for
# that and cheap; the tree below a browser window is large and irrelevant.
WINDOW_SEARCH_DEPTH = 2

# XTEST button numbers.
_BUTTONS = {"left": 1, "middle": 2, "right": 3}
_WHEEL_UP, _WHEEL_DOWN = 4, 5

# Playwright key names -> the names xdotool knows.
_KEY_ALIASES = {
    "Enter": "Return",
    "Escape": "Escape",
    "Tab": "Tab",
    "Backspace": "BackSpace",
    "Delete": "Delete",
    "ArrowUp": "Up",
    "ArrowDown": "Down",
    "ArrowLeft": "Left",
    "ArrowRight": "Right",
    "PageUp": "Prior",
    "PageDown": "Next",
    "Home": "Home",
    "End": "End",
    "Space": "space",
    "Control": "ctrl",
    "Shift": "shift",
    "Alt": "alt",
    "Meta": "super",
}


class X11Unavailable(RuntimeError):
    """Raised when the display, the tooling or the browser window is missing.

    Always recoverable: the caller mounts the CDP backend instead. A run that
    cannot reach an X server should still apply for jobs.
    """


def _xdotool_key(key: str) -> str:
    """Translate a Playwright key name, including chords like 'Control+A'.

    Single letters are lowercased: in Playwright 'Control+A' names the A key, but
    xdotool reads a capital as shift+a, so passing it through verbatim would send
    ctrl+shift+a — a different shortcut entirely.
    """
    parts = []
    for part in key.split("+"):
        part = _KEY_ALIASES.get(part, part)
        if len(part) == 1 and part.isalpha():
            part = part.lower()
        parts.append(part)
    return "+".join(parts)


class X11InputBackend:
    """One display, one browser window, one pointer.

    Build it with `create()` — the constructor takes an already-calibrated
    mapping because construction must not silently produce a backend that clicks
    in the wrong place.
    """

    name = BACKEND_X11

    def __init__(self, page, display, window, display_name, scale, origin, window_geometry=None):
        self._page = page
        self._display = display
        self._window = window
        self._display_name = display_name
        self._scale_x, self._scale_y = scale
        self._origin_x, self._origin_y = origin
        self._window_x = getattr(window_geometry, "x", 0)
        self._window_y = getattr(window_geometry, "y", 0)
        self._window_width = getattr(window_geometry, "width", 0)
        self._env = {**os.environ, "DISPLAY": display_name}
        # Sub-notch scroll remainder, carried across calls. See wheel().
        self._wheel_residual = 0.0

    # -- geometry ---------------------------------------------------------

    def to_root(self, x: float, y: float) -> tuple[int, int]:
        """Viewport CSS pixels -> absolute pixels on the X root window.

        The scale is not assumed to be the persona's device_scale_factor even
        though it usually equals it: it is measured (see `create`), because the
        browser's own zoom, the X screen's DPI and the window decorations all
        land in the same number and only one of them is known up front.
        """
        return (
            int(round(self._origin_x + x * self._scale_x)),
            int(round(self._origin_y + y * self._scale_y)),
        )

    def chrome_target(self, name: str) -> tuple[float, float]:
        """Viewport-space coordinates of a browser-UI element.

        The toolbar is not in the page, so it has no DOM node and no
        `bounding_box()`. It does have a position though, and it is a fixed
        distance ABOVE the viewport — which is why these come back with a
        negative y. `to_root` maps them the same as any other point.

        Derived from the measured chrome height rather than hardcoded pixels:
        the gap between the window's top edge and the viewport's is the tab strip
        plus the toolbar, and the toolbar is the lower part of it. Positions are
        proportional so they survive a different window size or scale factor.

        Only the x11 backend has any of this. A headless browser has no chrome at
        all, which is why callers check for the method before using it.

        The address bar is the only target here, and it is the only one worth
        aiming at: it is a wide field, so a row that is roughly right still lands
        inside it. A toolbar BUTTON is not — the back arrow used to be a target
        and was removed, because a small button reached by inference misses onto
        whatever is beside it. Navigation back goes through Alt+Left instead.
        """
        chrome_h = self._origin_y - self._window_y
        if chrome_h <= 0:
            raise X11Unavailable("no browser chrome above the viewport to aim at")

        # The toolbar row sits under the tab strip; ~72% down the chrome lands in
        # the middle of it for a stock Chrome.
        row_y = -(chrome_h * 0.28) / max(self._scale_y, 0.01)
        window_left_offset = (self._window_x - self._origin_x) / max(self._scale_x, 0.01)

        if name == "omnibox":
            # Well into the address field, clear of the reload and the icons.
            width_css = (self._window_width or 1200) / max(self._scale_x, 0.01)
            return window_left_offset + width_css * 0.45, row_y
        raise ValueError(f"unknown chrome target {name!r}")

    # -- mouse ------------------------------------------------------------
    #
    # Called synchronously rather than through asyncio.to_thread. An
    # XTestFake*Event plus a flush is a few microseconds on a unix socket, so
    # handing it to a thread pool would cost more than it saves — and it keeps
    # every touch of this Display connection on one thread, which python-xlib
    # requires anyway.

    async def move(self, x: float, y: float) -> None:
        from Xlib.ext import xtest

        root_x, root_y = self.to_root(x, y)
        xtest.fake_input(self._display, 6, x=root_x, y=root_y)  # MotionNotify
        self._display.sync()

    async def button_down(self, button: str = "left") -> None:
        from Xlib.ext import xtest

        xtest.fake_input(self._display, 4, _BUTTONS.get(button, 1))  # ButtonPress
        self._display.sync()

    async def button_up(self, button: str = "left") -> None:
        from Xlib.ext import xtest

        xtest.fake_input(self._display, 5, _BUTTONS.get(button, 1))  # ButtonRelease
        self._display.sync()

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        """Wheel notches, not pixels.

        A real wheel cannot scroll 37 pixels. It emits whole clicks and the
        browser decides what each one is worth, which is the one place this
        backend cannot reproduce a caller's request exactly.

        The remainder is CARRIED rather than dropped. `human_scroll` deliberately
        emits many small deltas to shape a velocity profile — most of them below
        one notch — so discarding sub-notch requests made scrolling silently do
        nothing at all. Accumulating means a run of small steps still travels the
        distance asked for, just quantised the way a wheel quantises it.
        """
        from Xlib.ext import xtest

        self._wheel_residual += delta_y
        notches = int(abs(self._wheel_residual) / PIXELS_PER_NOTCH)
        if notches <= 0:
            return

        direction = 1 if self._wheel_residual > 0 else -1
        self._wheel_residual -= direction * notches * PIXELS_PER_NOTCH
        button = _WHEEL_DOWN if direction > 0 else _WHEEL_UP

        for _ in range(notches):
            xtest.fake_input(self._display, 4, button)
            xtest.fake_input(self._display, 5, button)
            self._display.sync()
            # Even a fast flick has a gap between notches; back-to-back clicks
            # at socket speed are not a wheel any hand turns.
            await asyncio.sleep(0.012)

    # -- keyboard ---------------------------------------------------------

    async def _xdotool(self, *args: str, stdin_text: str = None) -> None:
        proc = await asyncio.create_subprocess_exec(
            "xdotool", *args,
            env=self._env,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate(
            stdin_text.encode() if stdin_text is not None else None
        )
        message = (stderr or b"").decode(errors="replace").strip()

        # A non-zero exit is not the only failure. `xdotool keydown ':'` exits 0
        # while printing "No such key name ':'. Ignoring it." and pressing
        # nothing — so a caller trusting the return code types a URL and gets it
        # back with every colon, slash and dot silently missing.
        if proc.returncode != 0 or "No such key name" in message or "Invalid key sequence" in message:
            raise X11Unavailable(f"xdotool {' '.join(args)} failed: {message}")

    async def focus_window(self) -> None:
        """Give the browser window X input focus before a bare keystroke.

        Works on an Xvfb we started, which is the only display we are allowed to
        borrow keyboard focus on anyway. It CANNOT work on a managed desktop, and
        that is measured, not assumed: on WSLg with focus held by another window,
        `xdotool windowactivate`, `xdotool windowfocus`, `XSetInputFocus`, a
        pointer move and a full XTEST click all failed to bring it back. Weston
        is rootless, so focus belongs to the host OS and nothing inside X can
        take it. `display_has_window_manager` is what keeps us off such displays.

        Two earlier versions of this docstring were wrong in opposite directions
        — one called this decoration on the strength of an Xvfb-only probe, the
        next called it the fix. It is neither: it is the cheap, correct thing to
        do on a display where focus is ours to set.
        """
        activated = False
        try:
            await asyncio.wait_for(
                self._xdotool("windowactivate", "--sync", str(self._window.id)),
                timeout=3,
            )
            activated = True
        except Exception:
            # No window manager to ask (a bare Xvfb), or it declined.
            logger.debug("windowactivate did not take; setting focus directly",
                         exc_info=True)

        if not activated:
            try:
                from Xlib import X

                self._display.set_input_focus(self._window, X.RevertToParent, X.CurrentTime)
                self._display.sync()
            except Exception:
                logger.debug("could not focus the browser window", exc_info=True)

        # Say so when it did not land. A keystroke sent to the wrong window
        # surfaces three layers away as "the cards did not reappear", which is
        # not a trail anybody follows back to here.
        try:
            focused = await asyncio.wait_for(
                self._xdotool_output("getwindowfocus"), timeout=3
            )
            if focused and int(focused) != self._window.id:
                logger.warning(
                    "[X11] focus is on window %#x, not the browser (%#x) — the "
                    "keystrokes about to be sent will go there instead",
                    int(focused), self._window.id,
                )
        except Exception:
            logger.debug("could not read the focused window", exc_info=True)

    async def _xdotool_output(self, *args: str) -> str:
        """Run xdotool and return its stdout, for the commands we ask questions of."""
        proc = await asyncio.create_subprocess_exec(
            "xdotool", *args,
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return (out or b"").decode(errors="replace").strip()

    async def key_press(self, key: str, dwell_s: float, locator=None) -> None:
        """Hold the key for `dwell_s`, then release.

        `locator` is ignored: XTEST has no concept of a target element, the
        keystroke goes wherever focus is. Callers click the field first, which
        they already did for the CDP backend too.
        """
        name = _xdotool_key(key)

        if "+" in name:
            # Chords go through `key`, which presses and releases as one atomic
            # action. Building one out of keydown/sleep/keyup leaves the modifier
            # logically HELD in the X server afterwards: every following
            # punctuation character then arrives as Ctrl+<char>, a dead shortcut,
            # and silently produces nothing. Letters still worked, which is what
            # made it look like a punctuation bug rather than a stuck modifier.
            #
            # The cost is the dwell, which matters far less on a shortcut than on
            # a character — nobody measures how long Ctrl+A was held.
            await self._xdotool("key", "--clearmodifiers", name)
            await asyncio.sleep(dwell_s)
            return

        await self._xdotool("keydown", "--clearmodifiers", name)
        await asyncio.sleep(dwell_s)
        await self._xdotool("keyup", "--clearmodifiers", name)

    async def type_char(self, char: str, locator=None, dwell_s: float = 0.0) -> None:
        """One character, held for `dwell_s`.

        `xdotool type` presses and releases immediately, so the hold is built
        from keydown/keyup around a sleep. It only works for characters xdotool
        can name; anything else (an accent, a symbol outside the keymap) falls
        back to `type`, which does the temporary keycode remap and gives up the
        dwell for that character rather than dropping it.
        """
        # Only ASCII alphanumerics get the keydown/keyup hold: they are the
        # characters xdotool names after themselves. Punctuation has to be called
        # by its keysym ('colon', 'slash', 'period'), and accents are not in the
        # keymap at all — both go through `type`, which does the lookup and the
        # temporary keycode remap, at the cost of the dwell for that character.
        if dwell_s > 0 and char.isascii() and char.isalnum():
            try:
                await self._xdotool("keydown", "--clearmodifiers", char)
                await asyncio.sleep(dwell_s)
                await self._xdotool("keyup", "--clearmodifiers", char)
                return
            except X11Unavailable:
                pass  # not a name xdotool knows — fall through
        # Through stdin, not as an argument. `xdotool type` has no `--` option
        # terminator (checked: `xdotool type --help`), so a character that looks
        # like a flag cannot be passed positionally — and passing `--` anyway got
        # it swallowed as an unknown option, which dropped every colon, slash and
        # dot from a typed URL while reporting success.
        await self._xdotool(
            "type", "--clearmodifiers", "--delay", "0", "--file", "-", stdin_text=char
        )

    async def close(self) -> None:
        try:
            self._display.close()
        except Exception:
            logger.debug("X display close failed", exc_info=True)

    # -- construction -----------------------------------------------------

    @classmethod
    async def create(cls, page, display_name: str = None) -> "X11InputBackend":
        """Connect, find the browser window, and MEASURE the mapping.

        Raises X11Unavailable for every failure mode so the caller has exactly
        one thing to catch before falling back to CDP.
        """
        display_name = display_name or os.getenv("DISPLAY") or ":0"

        if shutil.which("xdotool") is None:
            raise X11Unavailable("xdotool is not installed")
        try:
            from Xlib import display as xdisplay, X
            from Xlib.ext import xtest
        except ImportError as exc:
            raise X11Unavailable(f"python-xlib is not importable: {exc}") from exc

        try:
            conn = xdisplay.Display(display_name)
        except Exception as exc:
            raise X11Unavailable(f"cannot open display {display_name}: {exc}") from exc

        if not conn.has_extension("XTEST"):
            conn.close()
            raise X11Unavailable(f"display {display_name} has no XTEST extension")

        # Exact first (this page's own window), then "a browser", then a guess.
        window, geometry = await cls._find_this_page_window(page, conn, X)
        if window is None:
            logger.info("no window carried this page's marker title; matching on WM_CLASS")
            window, geometry = cls._find_browser_window(conn, X)
        if window is None:
            conn.close()
            raise X11Unavailable(f"no viewable browser window on {display_name}")

        # Under a reparenting WM the geometry is relative to the frame, so the
        # position has to be re-read against root before anything is aimed at it.
        root_x, root_y = cls._root_position(conn, window, geometry)
        geometry = SimpleNamespace(
            x=root_x, y=root_y, width=geometry.width, height=geometry.height
        )
        try:
            wm_class = window.get_wm_class() or ()
        except Exception:
            wm_class = ()
        describe = f"window {window.id:#x} class={wm_class or None};"

        # The keystrokes go to whatever holds focus, so make sure that is us.
        try:
            conn.set_input_focus(window, X.RevertToParent, X.CurrentTime)
            conn.sync()
        except Exception:
            logger.debug("could not set input focus", exc_info=True)

        scale, origin = await cls._calibrate(page, conn, geometry, xtest, describe)

        backend = cls(page, conn, window, display_name, scale, origin, window_geometry=geometry)
        logger.info(
            "x11 backend on %s: window %sx%s at (%s,%s), scale=(%.3f, %.3f), origin=(%.1f, %.1f)",
            display_name, geometry.width, geometry.height, geometry.x, geometry.y,
            scale[0], scale[1], origin[0], origin[1],
        )
        return backend

    @staticmethod
    async def _find_this_page_window(page, conn, X, attempts=12):
        """The window belonging to THIS page, identified by asking the page to
        name it.

        WM_CLASS narrows the field to "a Chrome", which is enough on an Xvfb we
        started and own. It is NOT enough on a borrowed display: a browser left
        over from a previous run, or one the developer opened, matches just as
        well — and picking it means calibrating against a page that will never
        see our pointer, then silently dropping the whole run to CDP. That was
        intermittent in testing, which is the worst way for it to behave.

        Chrome puts the document title in the window title, so the page can label
        its own window and we can match on something unique. The title is put back
        afterwards; nothing observes it in between, and the page has not navigated
        anywhere yet.
        """
        marker = f"__agent_input_{uuid.uuid4().hex[:12]}__"
        try:
            original = await page.title()
            await page.evaluate("(m) => { document.title = m; }", marker)
        except Exception:
            return None, None

        found, geometry = None, None
        try:
            for _ in range(attempts):
                for window, geom in X11InputBackend._viewable_windows(conn, X):
                    try:
                        name = window.get_wm_name()
                    except Exception:
                        continue
                    if name and marker in name:
                        found, geometry = window, geom
                        break
                if found is not None:
                    break
                await asyncio.sleep(0.1)
        finally:
            try:
                await page.evaluate("(t) => { document.title = t; }", original)
            except Exception:
                logger.debug("could not restore the page title", exc_info=True)

        return found, geometry

    @staticmethod
    def _viewable_windows(conn, X):
        """Every mapped, non-popup window on the display, with its geometry."""
        root = conn.screen().root
        screen_w = conn.screen().width_in_pixels
        screen_h = conn.screen().height_in_pixels
        out = []

        def visit(window, depth):
            try:
                children = window.query_tree().children
            except Exception:
                return
            for child in children:
                try:
                    attributes = child.get_attributes()
                    if attributes.map_state != X.IsViewable:
                        continue
                    if getattr(attributes, "override_redirect", False):
                        continue
                    geometry = child.get_geometry()
                except Exception:
                    continue
                if geometry.width * geometry.height > 1 and (
                    geometry.width <= screen_w and geometry.height <= screen_h
                ):
                    out.append((child, geometry))
                if depth < WINDOW_SEARCH_DEPTH:
                    visit(child, depth + 1)

        visit(root, 0)
        return out

    @staticmethod
    def _find_browser_window(conn, X):
        """The browser's own window, identified rather than guessed at.

        This used to take the largest viewable top-level window, which is
        unambiguous on Xvfb because the browser is the only client there. On a
        borrowed display it is wrong. Measured on WSLg, whose Weston WM reparents
        every window into a frame:

            id=0x20003e  2644x1511+-32+-32  class=None            <- the frame
              id=0x600004  2568x1414+38+59  class=Google-chrome   <- the browser

        The frame is larger than the browser it contains, so size alone always
        picks the wrapper. Weston also keeps an 8192x8192 window on root, 33x any
        browser on a 1920x1080 screen, which wins the moment it is mapped.

        So: match WM_CLASS, descend into frames, and never accept a window bigger
        than the screen it is on — nothing real is.
        """
        root = conn.screen().root
        screen_w = conn.screen().width_in_pixels
        screen_h = conn.screen().height_in_pixels

        named, unnamed = [], []

        def visit(window, depth):
            try:
                children = window.query_tree().children
            except Exception:
                return
            for child in children:
                try:
                    attributes = child.get_attributes()
                    if attributes.map_state != X.IsViewable:
                        continue
                    # Override-redirect windows are menus, tooltips and drag
                    # images — never the thing a pointer should be mapped onto.
                    if getattr(attributes, "override_redirect", False):
                        continue
                    geometry = child.get_geometry()
                except Exception:
                    continue

                area = geometry.width * geometry.height
                if area > 1:
                    try:
                        wm_class = child.get_wm_class() or ()
                    except Exception:
                        wm_class = ()
                    label = " ".join(wm_class).lower()
                    if any(name in label for name in BROWSER_WM_CLASSES):
                        named.append((area, child, geometry, wm_class))
                    elif geometry.width <= screen_w and geometry.height <= screen_h:
                        unnamed.append((area, child, geometry, wm_class))

                if depth < WINDOW_SEARCH_DEPTH:
                    visit(child, depth + 1)

        visit(root, 0)

        # A WM_CLASS match always beats a size guess. The fallback is kept for a
        # browser that sets no class, but it can no longer outrank a real match.
        pool = named or unnamed
        if not pool:
            return None, None
        _, window, geometry, wm_class = max(pool, key=lambda entry: entry[0])
        if not named:
            logger.info(
                "no window matched a browser WM_CLASS; falling back to the largest "
                "viewable window %#x (%sx%s)", window.id, geometry.width, geometry.height
            )
        return window, geometry

    @staticmethod
    def _root_position(conn, window, geometry):
        """Where the window really is on the root, not where its parent thinks.

        `get_geometry()` reports x/y relative to the PARENT. With no window
        manager the parent IS root and the two agree — which is why this never
        mattered on Xvfb. Under a reparenting WM the browser sits inside a frame,
        and its reported (38, 59) is an offset inside that frame, not a place on
        the screen. Measured on WSLg: reported (38, 59), actual (6, 27).
        """
        try:
            translated = window.translate_coords(conn.screen().root, 0, 0)
            return -translated.x, -translated.y
        except Exception:
            logger.debug("translate_coords failed; using parent-relative geometry",
                         exc_info=True)
            return geometry.x, geometry.y

    @staticmethod
    async def _content_box(page):
        """Where the page believes its own content area sits on the screen.

        The init script spoofs `screen.*` and `devicePixelRatio` but NOT
        `screenX/screenY/outerWidth/outerHeight/innerWidth/innerHeight`, so these
        six come back honest. They describe the content area directly, which the
        X window rectangle only approximates: the window includes the tab strip,
        the omnibox, any infobar Chrome decides to show, and the frame a window
        manager wraps around it. Measured across two runs of the same persona,
        that difference was 29px of height and a 20px vertical offset — enough to
        put a probe outside the content on one run and inside it on the next.
        """
        try:
            box = await page.evaluate(
                """() => ({
                    sx: window.screenX, sy: window.screenY,
                    ow: window.outerWidth, oh: window.outerHeight,
                    iw: window.innerWidth, ih: window.innerHeight,
                })"""
            )
        except Exception:
            return None
        if not box or not box["iw"] or not box["ih"]:
            return None

        # A window's side borders are symmetric; everything else above the
        # content is chrome.
        border = max(0, (box["ow"] - box["iw"]) // 2)
        return (
            box["sx"] + border,
            box["sy"] + (box["oh"] - box["ih"]) - border,
            box["iw"],
            box["ih"],
        )

    @staticmethod
    def _probe_points(box, screen_w, screen_h):
        """Two well-separated points inside the part of `box` that is on screen."""
        x, y, width, height = box
        left, top = max(x, 0), max(y, 0)
        right, bottom = min(x + width, screen_w), min(y + height, screen_h)
        if right - left < 100 or bottom - top < 100:
            return None
        visible_w, visible_h = right - left, bottom - top
        return [
            (left + int(visible_w * 0.33), top + int(visible_h * 0.45)),
            (left + int(visible_w * 0.66), top + int(visible_h * 0.80)),
        ]

    @staticmethod
    async def _calibrate(page, conn, geometry, xtest, describe=""):
        """Solve the viewport->root mapping by moving the pointer and asking the
        page where it thinks the pointer is.

        Measured rather than computed. The offset between the two spaces is the
        sum of the window position, the border, the tab strip, the omnibox and
        the device pixel ratio — and the init script spoofs `devicePixelRatio`,
        so the page cannot even be asked for that one honestly. Two probe points
        per axis give the scale and the origin without needing to know any of the
        parts separately.

        The probes are aimed from the PAGE's own content box first and from the X
        window rectangle only as a fallback. The window rectangle is a guess about
        where the content is — it contains chrome of unknown height, and a run
        that grows an infobar moves the content down without moving the window.
        `_content_box` asks instead of guessing. Both are clipped to the screen,
        because a persona larger than the display puts part of the window
        somewhere no pointer can go.
        """
        screen_w = conn.screen().width_in_pixels
        screen_h = conn.screen().height_in_pixels

        candidates = []
        content = await X11InputBackend._content_box(page)
        if content is not None:
            points = X11InputBackend._probe_points(content, screen_w, screen_h)
            if points:
                candidates.append(("page content box", points))
        points = X11InputBackend._probe_points(
            (geometry.x, geometry.y, geometry.width, geometry.height), screen_w, screen_h
        )
        if points:
            candidates.append(("x11 window rect", points))

        if not candidates:
            conn.close()
            raise X11Unavailable(
                f"nothing usefully on screen to aim at: {describe} window "
                f"{geometry.width}x{geometry.height} at ({geometry.x},{geometry.y}), "
                f"content box {content}, screen {screen_w}x{screen_h}"
            )

        await page.evaluate(
            """() => {
                window.__inputProbe = null;
                window.addEventListener('mousemove', (e) => {
                    window.__inputProbe = { x: e.clientX, y: e.clientY };
                }, true);
            }"""
        )

        attempts = []
        for source, probes in candidates:
            readings = []
            for root_x, root_y in probes:
                xtest.fake_input(conn, 6, x=root_x, y=root_y)
                conn.sync()
                reading = None
                for _ in range(20):
                    await asyncio.sleep(0.05)
                    reading = await page.evaluate("window.__inputProbe")
                    if reading:
                        break
                await page.evaluate("window.__inputProbe = null")
                if not reading:
                    attempts.append(f"{source} {probes} -> no mousemove at ({root_x},{root_y})")
                    break
                readings.append(((root_x, root_y), reading))

            if len(readings) != 2:
                continue

            (r1, c1), (r2, c2) = readings
            span_client_x = c2["x"] - c1["x"]
            span_client_y = c2["y"] - c1["y"]
            if abs(span_client_x) < 2 or abs(span_client_y) < 2:
                attempts.append(f"{source} {probes} -> probes too close: {c1} vs {c2}")
                continue

            scale_x = (r2[0] - r1[0]) / span_client_x
            scale_y = (r2[1] - r1[1]) / span_client_y
            logger.info("calibrated from the %s: %s", source, probes)
            return (
                (scale_x, scale_y),
                (r1[0] - c1["x"] * scale_x, r1[1] - c1["y"] * scale_y),
            )

        conn.close()
        raise X11Unavailable(
            f"calibration failed. {describe} window {geometry.width}x{geometry.height} "
            f"at ({geometry.x},{geometry.y}), content box {content}, screen "
            f"{screen_w}x{screen_h}. Tried: " + "; ".join(attempts)
        )

    async def verify(self, tolerance_px: float = 2.0) -> bool:
        """Move to a known viewport point and check the page agrees.

        Called after `create` so a mapping that is subtly wrong fails loudly here
        rather than silently clicking the wrong element for a whole run.
        """
        viewport = self._page.viewport_size or {"width": 1280, "height": 720}
        target_x = viewport["width"] * 0.5
        target_y = viewport["height"] * 0.5

        await self._page.evaluate("window.__inputProbe = null")
        await self.move(target_x, target_y)

        reading = None
        for _ in range(20):
            await asyncio.sleep(0.05)
            reading = await self._page.evaluate("window.__inputProbe")
            if reading:
                break
        if not reading:
            logger.warning("x11 mapping verification saw no mousemove")
            return False

        drift = max(abs(reading["x"] - target_x), abs(reading["y"] - target_y))
        if drift > tolerance_px:
            logger.warning(
                "x11 mapping is off by %.1fpx (asked for %.0f,%.0f; page saw %s,%s)",
                drift, target_x, target_y, reading["x"], reading["y"],
            )
            return False
        return True
