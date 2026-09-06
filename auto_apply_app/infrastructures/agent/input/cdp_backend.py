"""Playwright's own input, i.e. CDP `Input.dispatch*Event`.

This is what every run used before the seam existed, kept as the default and as
the fallback whenever an X display is unavailable — which is every headless run.

Worth being precise about what it does and does not give you. The events are
`isTrusted: true` and travel Chrome's real input pipeline, so they are not
"synthetic" in the JS sense. What they lack is provenance below the browser: no X
device generated them, no pointer exists on any desktop, and `screenX`/`screenY`
are whatever the browser derives for a window that, in headless, has no position
on any screen.
"""
from auto_apply_app.infrastructures.agent.input.backend import BACKEND_CDP


class CdpInputBackend:
    name = BACKEND_CDP

    def __init__(self, page):
        self._page = page

    async def move(self, x: float, y: float) -> None:
        await self._page.mouse.move(x, y)

    async def button_down(self, button: str = "left") -> None:
        await self._page.mouse.down(button=button)

    async def button_up(self, button: str = "left") -> None:
        await self._page.mouse.up(button=button)

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        await self._page.mouse.wheel(delta_x, delta_y)

    async def key_press(self, key: str, dwell_s: float, locator=None) -> None:
        # Playwright's `delay` sits between keydown and keyup, so it IS the dwell.
        delay_ms = max(0.0, dwell_s * 1000.0)
        if locator is not None:
            await locator.press(key, delay=delay_ms)
        else:
            await self._page.keyboard.press(key, delay=delay_ms)

    async def type_char(self, char: str, locator=None, dwell_s: float = 0.0) -> None:
        """One character, held for `dwell_s`.

        Playwright's `delay` on a single character is the gap between ITS keydown
        and ITS keyup — the hold, not the interval to the next key. Passing 0
        (which is what this did before the probe measured it) releases every key
        2ms after pressing it.

        press_sequentially stays targeted at the element, unlike page.keyboard,
        which follows focus if anything steals it mid-word.
        """
        delay_ms = max(0.0, dwell_s * 1000.0)
        if locator is not None:
            await locator.press_sequentially(char, delay=delay_ms)
        else:
            await self._page.keyboard.type(char, delay=delay_ms)

    async def close(self) -> None:
        return None
