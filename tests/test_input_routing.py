"""Every input a live worker sends must go through the pacing mixin.

`human_behavior` is a bag of free functions, so nothing ever stopped a worker
calling `locator.click()` directly — and roughly a third of them did, including
every login submit and every final application submit. Those are the two moments
a board is most certainly watching, and they were the two least human things in
the run: a raw `.click()` teleports the cursor and fires mousedown/mouseup with
no approach and no dwell, and a raw `.fill()` sets the value through
`Input.insertText`, producing no key events at all.

Routing everything through `HumanPacing._click` / `_type` / `_press` / `_scroll_to`
/ `_select` / `_check` / `_upload` is also what gives the input backend a single
seam to swap (AGENT_INPUT_BACKEND). A call site holding a Playwright locator and
calling it directly cannot be redirected to an OS-level backend at all.

This test is the enforcement. It is deliberately source-level: it fails on the
call being written, not on it being reached at runtime.
"""
import ast
from pathlib import Path

import pytest

WORKER_DIR = Path("auto_apply_app/infrastructures/agent/workers")
LIVE_WORKERS = [
    WORKER_DIR / "apec" / "apec_worker.py",
    WORKER_DIR / "wttj" / "wttj_worker.py",
    WORKER_DIR / "hellowork" / "hw_worker.py",
]

# Method name -> the primitive that replaces it.
BANNED = {
    "click": "self._click(locator)",
    "fill": "self._type(locator, text)",
    "type": "self._type(locator, text)",
    "press_sequentially": "self._type(locator, text)",
    "press": "self._press(key, locator=...)",
    "check": "self._check(locator)",
    "uncheck": "self._uncheck(locator)",
    "select_option": "self._select(locator, value)",
    "scroll_into_view_if_needed": "self._scroll_to(locator)",
    "set_input_files": "self._upload(locator, files)",
    "hover": "human_hover(locator)",
    "tap": "self._click(locator)",
    "insert_text": "self._type(locator, text)",
}


def _offending_calls(path: Path):
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attr = getattr(node.func, "attr", None)
        if attr in BANNED:
            found.append((node.lineno, attr))
    return found


@pytest.mark.parametrize("worker", LIVE_WORKERS, ids=lambda p: p.stem)
def test_no_worker_touches_the_page_directly(worker):
    offenders = _offending_calls(worker)
    if offenders:
        detail = "\n".join(
            f"  {worker}:{line} calls .{attr}() — use {BANNED[attr]}"
            for line, attr in offenders
        )
        pytest.fail(
            f"{len(offenders)} raw Playwright input call(s) bypassing HumanPacing:\n{detail}"
        )


def test_the_guard_actually_catches_a_bypass():
    """A lint nobody has seen fail is a lint nobody should trust."""
    import tempfile

    source = (
        "class W:\n"
        "    async def go(self):\n"
        "        await self.page.locator('#submit').click()\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(source)
        path = Path(fh.name)

    try:
        offenders = _offending_calls(path)
        assert [attr for _, attr in offenders] == ["click"]
    finally:
        path.unlink()


def test_the_guard_accepts_the_primitives():
    """The replacements must not themselves trip the check."""
    import tempfile

    source = (
        "class W:\n"
        "    async def go(self):\n"
        "        await self._click(self.page.locator('#submit'))\n"
        "        await self._type(self.page.locator('#cv'), 'text')\n"
        "        await self._press('Enter')\n"
        "        await self._scroll_to(self.page.locator('#card'))\n"
        "        await self._check(self.page.locator('#consent'))\n"
        "        await self._uncheck(self.page.locator('#save'))\n"
        "        await self._select(self.page.locator('#kind'), 'cdi')\n"
        "        await self._upload(self.page.locator('#file'), {})\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(source)
        path = Path(fh.name)

    try:
        assert _offending_calls(path) == []
    finally:
        path.unlink()


@pytest.mark.parametrize("worker", LIVE_WORKERS, ids=lambda p: p.stem)
def test_cover_letters_are_typed_not_filled(worker):
    """The single worst offender, called out on its own so a regression here is
    unambiguous: `fill()` is `Input.insertText`, which enters an entire cover
    letter with zero keydown, keypress or keyup events."""
    source = worker.read_text()
    assert ".fill(" not in source, f"{worker} still uses fill() somewhere"
