#!/usr/bin/env python
"""Drive the same actions through both input backends and compare what a page sees.

    .venv/bin/python scripts/input_probe.py --backend cdp
    .venv/bin/python scripts/input_probe.py --backend x11
    .venv/bin/python scripts/input_probe.py --compare

Runs entirely offline against tests/harness/input_probe.html. No job board is
touched, and nothing here needs credentials.

The point is to make the backend choice measurable rather than argued. The
numbers that matter:

  screenOffset   0 means the window has no position on any screen — headless.
  coalesced      real pointers report several coalesced samples per event.
  media          hover/pointer as CSS sees them. Measured identical on both
                 (hover:hover, pointer:fine) — headless does NOT report a coarse
                 pointer, contrary to what the launch flags read like.
  keyDwell/Gap   Playwright's type(delay=X) makes dwell constant and gap ~0.
  curvature      mean deviation from a straight line, in px. 0 is a teleport.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from auto_apply_app.infrastructures.agent.human_behavior import (  # noqa: E402
    human_click,
    human_scroll,
    human_type,
)
from auto_apply_app.infrastructures.agent.input.backend import (  # noqa: E402
    attach_backend,
    detach_backend,
)
from auto_apply_app.infrastructures.agent.input.display import XvfbDisplay  # noqa: E402

PROBE = Path(__file__).resolve().parent.parent / "tests" / "harness" / "input_probe.html"
SCREEN_W, SCREEN_H = 1920, 1080


async def _exercise(page):
    """One fixed script, identical for both backends — the only variable is where
    the events are injected."""
    await page.evaluate("window.__reset()")

    # A click that has to be travelled to.
    await human_click(page.locator("#target"))
    await asyncio.sleep(0.3)

    # A scroll.
    await human_scroll(page, distance=420)
    await asyncio.sleep(0.3)

    # Typing, including the accented characters a French cover letter carries.
    await human_type(page.locator("#line"), "Ingénieur", min_delay=40, max_delay=120)
    await asyncio.sleep(0.2)
    await human_type(page.locator("#area"), "à très bientôt", min_delay=40, max_delay=120)
    await asyncio.sleep(0.4)

    return await page.evaluate("window.__report()")


async def run(backend_name: str, keep_open: bool = False) -> dict:
    from playwright.async_api import async_playwright

    headless = backend_name == "cdp"
    xvfb = None
    env = None

    if not headless:
        xvfb = XvfbDisplay(SCREEN_W, SCREEN_H).start()
        env = {**os.environ, "DISPLAY": xvfb.display}

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    f"--window-position=0,0",
                    f"--window-size={SCREEN_W},{SCREEN_H}",
                ],
                env=env,
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                screen={"width": SCREEN_W, "height": SCREEN_H},
            )
            page = await context.new_page()
            await page.goto(PROBE.as_uri())
            await page.wait_for_selector("#target")

            if backend_name == "x11":
                from auto_apply_app.infrastructures.agent.input.x11_backend import (
                    X11InputBackend,
                    X11Unavailable,
                )

                try:
                    backend = await X11InputBackend.create(page, display_name=xvfb.display)
                except X11Unavailable as exc:
                    print(f"  x11 unavailable: {exc}", file=sys.stderr)
                    raise
                attach_backend(page, backend)
                ok = await backend.verify()
                print(f"  mapping verified: {ok}")

            report = await _exercise(page)

            if keep_open:
                await asyncio.sleep(20)

            detach_backend(page)
            await context.close()
            await browser.close()
            return report
    finally:
        if xvfb is not None:
            xvfb.stop()


def _fmt(stat, unit=""):
    if not stat:
        return "-"
    return f"p50 {stat['p50']:.1f}{unit} sd {stat['sd']:.1f} (n={stat['n']})"


def show(name: str, r: dict) -> None:
    print(f"\n=== {name} ===")
    print(f"  moves            {r['moveCount']}")
    print(f"  move dt          {_fmt(r['moveDt'], 'ms')}")
    print(f"  move step        {_fmt(r['moveStep'], 'px')}")
    print(f"  curvature        {r['curvature'] if r['curvature'] is None else round(r['curvature'], 2)} px off the straight line")
    print(f"  all isTrusted    {r['allTrusted']}")
    print(f"  screenX-clientX  {r['screenOffset']}")
    print(f"  coalesced/event  {_fmt(r['coalesced'])}")
    print(f"  clicks landed    {r['clicks']}")
    print(f"  press dwell      {_fmt(r['pressDwell'], 'ms')}")
    print(f"  keys             {r['keyCount']}")
    print(f"  key dwell        {_fmt(r['keyDwell'], 'ms')}")
    print(f"  key gap          {_fmt(r['keyGap'], 'ms')}")
    print(f"  last inputType   {r['lastInputType']}")
    print(f"  wheel events     {r['wheelCount']}  delta {_fmt(r['wheelDelta'], 'px')}")
    print(f"  media            hover={r['media']['hover']} fine={r['media']['finePointer']} coarse={r['media']['coarsePointer']}")
    print(f"  navigator.webdriver {r['webdriver']}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["cdp", "x11"], default="cdp")
    parser.add_argument("--compare", action="store_true", help="run both and print both")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()

    if args.compare:
        for name in ("cdp", "x11"):
            try:
                show(name, await run(name))
            except Exception as exc:
                print(f"\n=== {name} ===\n  FAILED: {exc}")
        return 0

    show(args.backend, await run(args.backend, keep_open=args.keep_open))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
