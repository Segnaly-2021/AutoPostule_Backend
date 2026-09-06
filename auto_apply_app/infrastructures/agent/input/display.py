"""A private X display per browser, so OS-level input has somewhere to land.

`AGENT_INPUT_BACKEND=x11` cannot work against a headless browser. Playwright
launches `chromium-headless-shell` when `headless=True` (chromium.js:318), which
has no window at all — there is nothing for a pointer to be over and nothing for
a keystroke to be focused on. So the x11 backend implies a headful browser, and a
headful browser in a container implies Xvfb.

One display per browser, never shared. A display owns exactly one pointer and one
input focus, and boards fan out concurrently (master_agent.py builds up to three
`Send` branches), so a shared display would have three browsers fighting over one
cursor. That is not a tuning problem, it is a correctness one.

The framebuffer is sized to the PERSONA's screen. Personas draw screens up to
3840x2160, and `to_init_script()` tells the page `screen.width` is whatever the
persona says; if the X screen were smaller, the window could not be as large as
the page claims it is, and the two would contradict each other. Sizing the
framebuffer to the persona makes the spoof describe something true.
"""
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DISPLAY_BASE = int(os.getenv("AGENT_X11_DISPLAY_BASE", "101"))
_DISPLAY_SEARCH_RANGE = 24


# ---------------------------------------------------------------------------
# Watching a run
#
# The browser has to live on an Xvfb — a window-managed display cannot hold
# keyboard focus for us, so every keystroke would go wherever the developer last
# clicked (see display_has_window_manager). That makes the run correct and
# invisible, which is not much use before a deploy.
#
# So the run is SERVED rather than hosted: x11vnc exports the Xvfb, and a viewer
# opens on the developer's own display. The viewer is an ordinary client of the
# desktop, so clicking it, moving it or closing it cannot take focus away from
# the browser — it is not on the browser's display at all. That separation is the
# whole point, and it is what borrowing :0 could never give.
# ---------------------------------------------------------------------------

VIEW_OFF, VIEW_SERVE, VIEW_WINDOW = "off", "serve", "window"
_VNC_PORT_BASE = 5900


def viewer_mode() -> str:
    """`AGENT_X11_VIEW`: off (default), serve, or window.

    `serve` exports the display and prints where to connect — for attaching a
    VNC client from Windows, or from another machine. `window` also opens a
    viewer here, which is the one-step answer to "let me see the run".
    """
    raw = (os.getenv("AGENT_X11_VIEW") or "").strip().lower()
    if raw in ("1", "true", "yes", "on", VIEW_WINDOW):
        return VIEW_WINDOW
    if raw in (VIEW_SERVE, "vnc"):
        return VIEW_SERVE
    return VIEW_OFF


def _last_line(path: str) -> str:
    """The last non-empty line of a log, for putting a reason in a warning."""
    try:
        with open(path) as handle:
            lines = [line.strip() for line in handle if line.strip()]
        return lines[-1] if lines else "(empty)"
    except OSError:
        return "(unreadable)"


class _DisplayViewer:
    """x11vnc over one Xvfb, plus an optional viewer window."""

    def __init__(self, display_name: str):
        self.display = display_name
        self.port = _VNC_PORT_BASE + max(1, int(display_name.lstrip(":") or 1) % 100)
        self._server = None
        self._viewer = None

    def start(self, mode: str) -> "_DisplayViewer":
        if mode == VIEW_OFF or shutil.which("x11vnc") is None:
            if mode != VIEW_OFF:
                logger.warning("AGENT_X11_VIEW is set but x11vnc is not installed "
                               "(apt install x11vnc)")
            return self

        # WAYLAND_DISPLAY has to go. x11vnc reads it from the ENVIRONMENT, not
        # from `-display`, and on WSLg (where it is set to "wayland-0") it prints
        # "Wayland sessions are as of now only supported via -rawfb" and exits
        # immediately — even though the display we are exporting is a plain Xvfb
        # with no Wayland anywhere near it. Measured: with the variable set the
        # port never binds; unset, x11vnc serves the Xvfb normally.
        env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}

        # Not DEVNULL. x11vnc reports why it refused to start on stderr, and
        # discarding that turned "the viewer never connected" into a dead end
        # with nothing to read. Kept out of the run's own log by going to a file.
        self._log_path = os.path.join(
            tempfile.gettempdir(), f"x11vnc{self.display.replace(':', '-')}.log"
        )
        try:
            log = open(self._log_path, "w")
        except OSError:
            log = subprocess.DEVNULL

        self._server = subprocess.Popen(
            [
                "x11vnc", "-display", self.display, "-rfbport", str(self.port),
                # Bound to loopback and unauthenticated: this is a dev-box view of
                # a browser session, and WSL2 forwards localhost from Windows, so
                # a viewer on the host still reaches it without opening a port to
                # the network.
                "-localhost", "-nopw",
                "-forever", "-shared", "-quiet", "-noxdamage",
            ],
            stdout=log, stderr=subprocess.STDOUT, env=env,
        )

        # Say whether it actually came up. It exits in well under a second when
        # it refuses, so a short wait separates "serving" from "already dead" —
        # and a viewer told to connect to a port nobody is listening on just
        # shows a retry dialog with no explanation.
        time.sleep(0.8)
        if self._server.poll() is not None:
            logger.warning(
                "x11vnc exited immediately — no viewer will connect. Reason is in "
                "%s (last line: %s)", self._log_path, _last_line(self._log_path),
            )
            self._server = None
            return self

        logger.info("watching %s on vnc://localhost:%s", self.display, self.port)

        if mode == VIEW_WINDOW:
            self._open_window()
        return self

    def _open_window(self) -> None:
        viewer = shutil.which("xtigervncviewer") or shutil.which("vncviewer")
        host_display = (os.environ.get("DISPLAY") or "").strip()
        if viewer is None or not host_display:
            logger.info("no viewer to open (install tigervnc-viewer, and run where "
                        "DISPLAY is set) — connect to vnc://localhost:%s", self.port)
            return

        time.sleep(0.6)  # let x11vnc bind before the viewer knocks
        self._viewer = subprocess.Popen(
            [
                viewer, f"localhost::{self.port}",
                # View-only on purpose. A click inside the viewer would be
                # delivered to the run's display as a real event, landing on
                # whatever the worker happened to be about to click.
                "-ViewOnly", "-Shared",
            ],
            env={**os.environ, "DISPLAY": host_display},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("opened a viewer window on %s", host_display)

    def stop(self) -> None:
        for proc in (self._viewer, self._server):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._viewer = self._server = None


def display_geometry(name: str) -> Optional[tuple[int, int]]:
    """The screen size of the X server on `name`, or None if it is not answering.

    Readiness is a real round trip, not a socket file appearing: Xvfb on some
    kernels (WSL2 among them) binds only the abstract unix namespace, so
    /tmp/.X11-unix/X<n> is never created and a file check waits forever on a
    server that is already up.

    Done in a SUBPROCESS, deliberately, even though python-xlib is right there.
    python-xlib registers extension methods on shared class objects at connect
    time, and connecting to two X servers with different extension sets in one
    process corrupts that state: after touching WSLg's Xwayland `:0`, every later
    `Display(':101')` in the same process dies with

        TypeError: 'type' object does not support item assignment

    Measured — an Xvfb that `xdotool` reached instantly was declared unreachable
    for 24 display numbers in a row, purely because `:0` had been probed first.
    Two Xvfb servers in one process are fine; it is the mixed pair that breaks.
    So a display we are only ASKING about never gets an Xlib connection, and the
    one connection python-xlib makes is to the display actually being used.
    """
    try:
        result = subprocess.run(
            ["xdotool", "getdisplaygeometry"],
            env={**os.environ, "DISPLAY": name},
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        width, height = result.stdout.split()[:2]
        return int(width), int(height)
    except (ValueError, IndexError):
        return None


def display_reachable(name: str) -> bool:
    """Whether an X server is answering on `name`. See `display_geometry`."""
    return display_geometry(name) is not None


def display_has_window_manager(name: str) -> bool:
    """Whether something else is managing focus on this display.

    This decides whether we may borrow it, and the reason is measured rather than
    assumed. On a managed desktop, X input focus belongs to the user's window
    manager, and on a ROOTLESS one (WSLg/Weston, where each X window is a real
    host window) it belongs to the host OS outright. Nothing inside X can take it
    back — tested on WSLg with focus held by another window, all five of
    `xdotool windowactivate`, `xdotool windowfocus`, `XSetInputFocus`, a pointer
    move and a full XTEST click failed to restore it.

    That matters because XTEST delivers pointer events by POSITION but key events
    by FOCUS. So on a managed display the mouse keeps working while EVERY
    keystroke silently goes to whatever the user last clicked — measured: typing
    into a clicked field produced '' with focus elsewhere, and 'before-steal'
    with focus on the browser. A run would click accurately through a whole
    application and type none of it.

    An Xvfb we start has no window manager, nothing competes for focus, and
    Chrome holds it for the life of the browser. That is why production is
    unaffected.
    """
    try:
        result = subprocess.run(
            ["xprop", "-root", "-display", name, "_NET_SUPPORTING_WM_CHECK"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        # No xprop, no answer — assume managed, because the failure mode of
        # guessing "unmanaged" is a run that clicks correctly and types nothing.
        return True
    return result.returncode == 0 and "window id" in result.stdout


class XvfbDisplay:
    """A virtual framebuffer, held for the life of one browser."""

    def __init__(self, width: int, height: int, base: int = DEFAULT_DISPLAY_BASE):
        self.width = width
        self.height = height
        self.display = None
        self._base = base
        self._proc = None
        self._viewer = None

    @staticmethod
    def available() -> bool:
        return shutil.which("Xvfb") is not None

    _reachable = staticmethod(lambda name: display_reachable(name))

    def start(self) -> "XvfbDisplay":
        if not self.available():
            raise RuntimeError("Xvfb is not installed")

        last_error = None
        for number in range(self._base, self._base + _DISPLAY_SEARCH_RANGE):
            display = f":{number}"
            if Path(f"/tmp/.X{number}-lock").exists():
                continue
            proc = subprocess.Popen(
                [
                    "Xvfb", display,
                    "-screen", "0", f"{self.width}x{self.height}x24",
                    "-nolisten", "tcp",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            for _ in range(100):
                if proc.poll() is not None:
                    last_error = (proc.stderr.read() or b"").decode(errors="replace").strip()
                    break
                if self._reachable(display):
                    self._proc, self.display = proc, display
                    logger.info(
                        "Xvfb up on %s at %sx%s", display, self.width, self.height
                    )
                    mode = viewer_mode()
                    if mode != VIEW_OFF:
                        self._viewer = _DisplayViewer(display).start(mode)
                    return self
                time.sleep(0.05)
            else:
                last_error = f"{display} never became reachable"
                proc.terminate()

        raise RuntimeError(f"could not start Xvfb: {last_error}")

    def stop(self) -> None:
        if self._viewer is not None:
            self._viewer.stop()
            self._viewer = None
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    # Context-manager sugar for scripts; workers use start/stop explicitly
    # because the display outlives the function that created it.
    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


class BorrowedDisplay:
    """A display that already exists and that we did not start.

    On a dev box there is a real X server with a real screen — WSLg's `:0`, or a
    desktop session — and a browser launched onto it is a window you can WATCH.
    That is the whole point of running locally before a deploy, and starting an
    Xvfb instead throws it away: the run still happens, headful, correct, and
    completely invisible.

    Same interface as XvfbDisplay so callers do not branch, except `stop()` does
    nothing. We did not start this server and we do not get to kill it.
    """

    def __init__(self, name: str):
        self.display = name
        self.width, self.height = self._geometry(name)
        self.borrowed = True

    @staticmethod
    def _geometry(name: str) -> tuple[int, int]:
        # Subprocess rather than python-xlib — see `display_geometry`. Probing a
        # display we might not end up using must leave no Xlib state behind.
        return display_geometry(name) or (1920, 1080)

    def start(self) -> "BorrowedDisplay":
        return self

    def stop(self) -> None:
        _release_borrowed(self.display)


# One browser per display is a correctness requirement, not a preference: a
# display owns exactly one pointer and one input focus, and boards fan out
# concurrently. A borrowed display can therefore serve exactly ONE browser — the
# first to ask. Everyone else gets their own Xvfb and is told why.
_borrowed_by: set[str] = set()

# python-xlib cannot hold connections to two X servers with different extension
# sets in one process — see `display_geometry`. Xvfb and Xvfb is fine (measured);
# Xwayland and Xvfb is not, in either order. So a process that has started an
# Xvfb must stop borrowing, or the next `Display()` dies with a TypeError and
# that board silently drops to CDP.
_started_own_display = False


def _release_borrowed(name: str) -> None:
    _borrowed_by.discard(name)


def borrowable_display() -> Optional[str]:
    """The existing display we may use, if there is one and it is free.

    `AGENT_X11_DISPLAY` decides: a display name uses that one, `none` refuses to
    borrow at all (the way to get an Xvfb on a machine that has a screen), and
    unset means inherit `$DISPLAY`. Cloud Run has no `$DISPLAY`, so the container
    starts an Xvfb without being told to; this box has one, so a run here is
    visible without being told to. Same rule, right answer in both places.
    """
    configured = (os.getenv("AGENT_X11_DISPLAY") or "").strip()
    if configured.lower() in ("none", "off", "xvfb"):
        return None

    name = configured or (os.environ.get("DISPLAY") or "").strip()
    if not name:
        return None
    if display_has_window_manager(name):
        # A warning, not a veto. Seeing the run is worth more here than being
        # protected from a failure mode with a known shape: keystrokes go to
        # whatever window holds focus, so clicking away mid-run loses them. The
        # focus check in X11InputBackend.focus_window is what makes that loud at
        # the moment it happens. AGENT_X11_DISPLAY=none opts out entirely.
        logger.warning(
            "%s is window-managed: keyboard input follows focus, so clicking "
            "another window during a run sends keystrokes there. Do not click "
            "away, or set AGENT_X11_DISPLAY=none to run off-screen instead.",
            name,
        )
    if name in _borrowed_by:
        logger.info("%s is already driving a browser -> starting an Xvfb instead", name)
        return None
    if not display_reachable(name):
        logger.info("%s is set but not reachable -> starting an Xvfb instead", name)
        return None
    return name


def open_display(width: int, height: int):
    """The display this browser will use: the one already running, or a new one.

    Returns something with `.display`, `.width`, `.height` and `.stop()`.
    """
    global _started_own_display

    name = borrowable_display()
    if name is not None and _started_own_display:
        # Giving up a visible window is a small loss; a board that silently falls
        # back to CDP halfway through a fan-out is not.
        logger.info(
            "this process already runs its own X server, so %s cannot also be "
            "used — python-xlib does not survive both. Staying on Xvfb.", name,
        )
        name = None

    if name is not None:
        display = BorrowedDisplay(name)
        _borrowed_by.add(name)
        logger.info(
            "using the existing display %s (%sx%s) — the browser window will be visible",
            name, display.width, display.height,
        )
        # A persona larger than this screen is fine here: the VIEWPORT is clamped
        # to fit (see fit_fingerprint_to_display), so the window stays on screen
        # and every part of it is somewhere a pointer can reach. Refusing to
        # borrow was the earlier answer and it was the wrong lever — it cost the
        # visible window, which is the only reason to run locally at all.
        return display

    if _borrowed_by:
        logger.warning(
            "starting an Xvfb while %s is already driving a browser in this "
            "process — python-xlib cannot hold both, so this board will fall "
            "back to CDP input. Set AGENT_X11_DISPLAY=none to run every board "
            "on its own Xvfb.", ", ".join(sorted(_borrowed_by)),
        )

    started = XvfbDisplay(width, height).start()
    _started_own_display = True
    return started


def screen_size_for(fingerprint) -> tuple[int, int]:
    """The framebuffer this persona needs. Falls back to a common desktop size
    when a run has no fingerprint (already a degraded path)."""
    if fingerprint is None:
        return 1920, 1080
    return int(fingerprint.screen_width), int(fingerprint.screen_height)


def window_geometry_for(fingerprint, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """Where to put the browser window on that screen, and how big.

    A deterministic, non-zero origin: at (0,0) the window's top-left coincides
    with the screen's, and every `screenX` a page reads back equals its
    `clientX` — the very reading that gives headless away. Real windows sit
    somewhere.
    """
    if fingerprint is None:
        return 60, 40, min(1280, screen_w), min(800, screen_h)

    width = min(int(fingerprint.viewport_width), screen_w)
    # The window is the viewport plus the browser's own chrome.
    height = min(int(fingerprint.viewport_height) + 120, screen_h)
    x = max(0, min(60, screen_w - width))
    y = max(0, min(40, screen_h - height))
    return x, y, width, height
