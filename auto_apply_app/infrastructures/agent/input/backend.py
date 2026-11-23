# ==============================================================================
# FILE: auto_apply_app/infrastructures/agent/input/backend.py
#
# WHAT IT DOES:
#   Defines the InputBackend interface every input method must implement, detects
#   whether this machine can use X11 input, and keeps the page-to-backend registry.
#   human_behavior.py calls get_backend(page) and does not care which one it gets.
#
# USED BY:
#   - auto_apply_app/infrastructures/agent/human_behavior.py: gets each page's backend
#   - auto_apply_app/infrastructures/agent/pacing.py: selects, attaches, detaches backends
#   - auto_apply_app/infrastructures/agent/input/__init__.py: re-exports the API
#   - auto_apply_app/infrastructures/agent/input/cdp_backend.py: backend name constant
#   - auto_apply_app/infrastructures/agent/input/x11_backend.py: backend name constant
#   - scripts/input_probe.py: input measurement tool
#   - scripts/preflight.py: environment checks
#   - tests/test_input_backends.py: backend tests
#
# EXTERNAL DEPENDENCIES:
#   - python-xlib (Xlib): presence check only
#   - Xvfb, xdotool: system tools whose presence enables the x11 backend
# ==============================================================================
"""The seam: one interface, one registry, one env knob.

Coordinates crossing this boundary are always **CSS pixels in the page's
viewport** — the space `bounding_box()` reports in. A backend that needs
something else (X11 needs device pixels on the root window) owns that conversion,
because only the backend knows the window origin, the browser chrome height and
the device pixel ratio it is working against.
"""
import logging
import os
import shutil
import sys
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

BACKEND_CDP = "cdp"
BACKEND_X11 = "x11"


@runtime_checkable
class InputBackend(Protocol):
    """Every method takes viewport CSS pixels and emits ONE event.

    Deliberately dumb: no curves, no delays, no retries. The caller
    (`human_behavior`) owns all of that, so both backends produce identically
    shaped motion and only the injection differs.
    """

    name: str

    async def move(self, x: float, y: float) -> None: ...

    async def button_down(self, button: str = "left") -> None: ...

    async def button_up(self, button: str = "left") -> None: ...

    async def wheel(self, delta_x: float, delta_y: float) -> None: ...

    async def key_press(self, key: str, dwell_s: float, locator=None) -> None: ...

    async def type_char(self, char: str, locator=None, dwell_s: float = 0.0) -> None: ...

    async def close(self) -> None: ...


def x11_capable() -> tuple[bool, str]:
    """Whether this machine can actually deliver XTEST events, and why not.

    Three hard requirements, checked separately because each one fails in a
    different place: without Xvfb there is no display to be headful on, without
    xdotool there is no keyboard, without python-xlib there is no pointer.
    """
    if sys.platform != "linux":
        return False, f"{sys.platform} has no X server to inject into"
    for tool in ("Xvfb", "xdotool"):
        if shutil.which(tool) is None:
            return False, f"{tool} is not installed"
    try:
        import Xlib  # noqa: F401
    except ImportError:
        return False, "python-xlib is not installed"
    return True, "linux with Xvfb, xdotool and python-xlib"


def resolve_backend_name() -> str:
    """Which backend to use — detected, not assumed.

    This used to default to CDP whenever AGENT_INPUT_BACKEND was unset, and
    nothing ever set it: no entry in `.env`, and `deploy-browser.yml` passed
    'cdp' as its own default. So every run in both environments took the CDP
    path and the OS-level work was unreachable — which is not a thing anybody
    notices, because a CDP run looks exactly like a working run right up until
    something needs the browser's own UI (Alt+Left has no CDP equivalent: a
    dispatched key event goes to the page, and the page has no Back button).

    So ask the machine instead. WSL here and the worker container on GCP are both
    Linux with the tooling installed, and both get x11. A mac laptop or a
    stripped image gets CDP and logs which requirement was missing.

    The env var still wins in both directions: 'x11' forces it on, 'cdp' forces
    it off, and forcing it off is the rollback lever, so it has to keep working.
    """
    raw = (os.getenv("AGENT_INPUT_BACKEND") or "").strip().lower()

    if raw in (BACKEND_X11, "xtest", "os"):
        return BACKEND_X11
    if raw == BACKEND_CDP:
        return BACKEND_CDP
    if raw not in ("", "auto", "detect"):
        logger.warning("Unknown AGENT_INPUT_BACKEND=%r -> detecting instead", raw)

    capable, why = x11_capable()
    logger.info(
        "input backend detected: %s (%s)", BACKEND_X11 if capable else BACKEND_CDP, why
    )
    return BACKEND_X11 if capable else BACKEND_CDP


# Keyed by id(page), the same convention `human_behavior._last_mouse_pos` uses.
# A page holds no user-defined attributes, and threading a backend through every
# helper signature would mean touching every call site again.
_backends: dict[int, InputBackend] = {}


# Entries leave this dict only through detach_backend, so teardown must call it
# (HumanPacing._close_display does). A leftover entry could match a reused id().
def attach_backend(page, backend: InputBackend) -> None:
    _backends[id(page)] = backend
    logger.info("input backend %r attached to page %s", backend.name, id(page))


def detach_backend(page) -> Optional[InputBackend]:
    return _backends.pop(id(page), None)


def get_backend(page) -> InputBackend:
    """The page's backend, defaulting to CDP.

    Defaulting rather than raising is deliberate: a helper called on a page whose
    worker never attached anything must still click, and clicking through CDP is
    exactly the behaviour that existed before this package.
    """
    backend = _backends.get(id(page))
    if backend is None:
        from auto_apply_app.infrastructures.agent.input.cdp_backend import CdpInputBackend

        backend = CdpInputBackend(page)
        _backends[id(page)] = backend
    return backend
