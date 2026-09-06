"""Input backends: how a synthesized action actually reaches the browser.

`human_behavior` decides the SHAPE of an action — the curve the cursor takes, the
dwell on a press, the interval between keys. This package decides where those
events are injected:

- `cdp` (default): Playwright's `page.mouse` / `page.keyboard`, i.e. CDP
  `Input.dispatchMouseEvent`. The events are trusted and go through Chrome's real
  input pipeline, but nothing outside the browser produced them — there is no
  pointer on any desktop, and in headless there is not even a window.
- `x11`: XTEST fake input against the X display the browser is running on. The
  events come from an actual X device, so the browser receives them the same way
  it receives a real mouse, and `screenX`/`screenY` are real screen coordinates.

Only the injection changes. Trajectories, timings and the pacing tiers are shared
by both, because the shape of the motion is what a page measures most easily and
it should not depend on which backend is mounted.
"""
from auto_apply_app.infrastructures.agent.input.backend import (
    InputBackend,
    attach_backend,
    detach_backend,
    get_backend,
    resolve_backend_name,
)

__all__ = [
    "InputBackend",
    "attach_backend",
    "detach_backend",
    "get_backend",
    "resolve_backend_name",
]
