"""Align a persona's claimed Chrome version with the browser that actually launched.

A persona's `chrome_major` is chosen when the device is born (CHROME_MAJOR_MIN..MAX
in fingerprint_generator) and walked forward as it ages, so it is a plausible guess —
not a fact. The fact is whatever binary Playwright just started.

That gap used to be harmless: the bundled Chromium sat inside the generated range.
It stopped being harmless when APEC_BROWSER_CHANNEL let a worker drive a real Chrome
install, which is several majors ahead. A UA claiming 140 on an engine that answers
feature checks like 149 is a self-inconsistency a board can read straight off the
page — worse than either version on its own.

So each worker calls this right after launch and before it builds the context: the
variant presents the version it is really running. The stored persona row is left
alone — the device's own major still ages the way it always did, and stays the value
a future run starts from.
"""
import logging
from dataclasses import replace
from typing import Optional

from auto_apply_app.domain.entities.user_fingerprint import (
    UserFingerprint,
    chrome_major_from_version,
)

logger = logging.getLogger(__name__)


def align_fingerprint_to_browser(
    fingerprint: Optional[UserFingerprint],
    browser_version: str,
    plog=None,
) -> Optional[UserFingerprint]:
    """Return the fingerprint with `chrome_major` set to the running browser's.

    Cosmetic on the happy path and never worth failing a run over: an
    unparseable version, or no fingerprint at all, returns the input unchanged.
    """
    if fingerprint is None:
        return None

    real_major = chrome_major_from_version(browser_version)
    if real_major is None or real_major == fingerprint.chrome_major:
        return fingerprint

    if plog:
        plog(
            f"aligning fingerprint to the real browser: "
            f"Chrome {fingerprint.chrome_major} -> {real_major}"
        )
    logger.info(
        "Aligned fingerprint %s chrome_major %s -> %s",
        fingerprint.id, fingerprint.chrome_major, real_major,
    )
    return fingerprint.with_variant(chrome_major=real_major)


# Room the browser needs around its content on a borrowed screen. Measured on
# WSLg with real Chrome: the window is the viewport +8 wide and +85 tall, and
# Weston's frame adds a further +76/+97. Rounded up, because a window that
# overhangs the screen by even a few pixels puts content where no pointer can go.
_CHROME_MARGIN_X = 120
_CHROME_MARGIN_Y = 220


def fit_fingerprint_to_display(
    fingerprint: Optional[UserFingerprint],
    screen_w: int,
    screen_h: int,
    plog=None,
) -> Optional[UserFingerprint]:
    """Shrink the viewport so the browser window fits on `screen_w x screen_h`.

    Playwright sizes the WINDOW to the requested viewport, so a 2560-wide persona
    opens a 2560-wide window on a 1920-wide screen. The overhang is not cosmetic:
    a pointer cannot travel past the edge of a screen, so everything out there is
    unclickable, and a click aimed at it lands on whatever sits at the boundary.

    Only the viewport moves. `screen_width`/`screen_height` keep describing the
    persona's monitor, because a window is ALLOWED to be smaller than its screen —
    that is what every browser that is not maximised looks like, and it is the
    more common of the two readings. Nothing here makes the persona less
    plausible; if anything a viewport equal to the screen was the odd one.

    Only ever shrinks. A persona that already fits is returned untouched, which is
    every persona in production: Cloud Run has no display to borrow, so its Xvfb
    is built to the persona's own size.
    """
    if fingerprint is None:
        return None

    max_w = max(640, screen_w - _CHROME_MARGIN_X)
    max_h = max(480, screen_h - _CHROME_MARGIN_Y)
    width = min(int(fingerprint.viewport_width), max_w)
    height = min(int(fingerprint.viewport_height), max_h)

    if width == fingerprint.viewport_width and height == fingerprint.viewport_height:
        return fingerprint

    if plog:
        plog(
            f"fitting the window to this {screen_w}x{screen_h} display: viewport "
            f"{fingerprint.viewport_width}x{fingerprint.viewport_height} -> "
            f"{width}x{height} (the persona still reports a "
            f"{fingerprint.screen_width}x{fingerprint.screen_height} screen)"
        )
    logger.info(
        "viewport clamped to %sx%s for a %sx%s display", width, height, screen_w, screen_h
    )
    return replace(fingerprint, viewport_width=width, viewport_height=height)
