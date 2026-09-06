"""Fitting a persona's window onto whatever screen is actually there.

Playwright sizes the WINDOW to the requested viewport, so a 2560-wide persona
opens a 2560-wide window on a 1920-wide screen. The overhang is not cosmetic: a
pointer cannot travel past the edge of a screen, so everything out there is
unclickable and a click aimed at it lands on whatever sits at the boundary.
"""
from dataclasses import replace
from uuid import uuid4

import pytest

from auto_apply_app.infrastructures.agent.fingerprint_alignment import (
    fit_fingerprint_to_display,
)
from auto_apply_app.infrastructures.agent.fingerprint_generator import FingerprintGenerator


@pytest.fixture
def persona():
    fingerprint = FingerprintGenerator().generate_device(uuid4())
    return replace(fingerprint, screen_width=2560, screen_height=1440,
                   viewport_width=2560, viewport_height=1329)


def test_an_oversized_viewport_is_shrunk_to_fit(persona):
    fitted = fit_fingerprint_to_display(persona, 1920, 1080)

    assert fitted.viewport_width < persona.viewport_width
    assert fitted.viewport_height < persona.viewport_height
    assert fitted.viewport_width <= 1920 and fitted.viewport_height <= 1080


def test_the_persona_still_reports_its_own_screen(persona):
    """The window shrinks; the monitor does not. A browser smaller than its
    screen is what every un-maximised window looks like — and `innerWidth ==
    screen.width` is the less usual of the two readings, so this does not make
    the persona less plausible."""
    fitted = fit_fingerprint_to_display(persona, 1920, 1080)

    assert (fitted.screen_width, fitted.screen_height) == (2560, 1440)
    assert fitted.viewport_width < fitted.screen_width


def test_room_is_left_for_the_browser_chrome_and_the_frame(persona):
    """Measured on WSLg with real Chrome: the window is the viewport +8 wide and
    +85 tall, and Weston's frame adds a further +76/+97. A viewport sized to the
    screen exactly would still overhang."""
    fitted = fit_fingerprint_to_display(persona, 1920, 1080)

    assert 1920 - fitted.viewport_width >= 100
    assert 1080 - fitted.viewport_height >= 200


def test_a_persona_that_already_fits_is_untouched(persona):
    """Production never clamps: Cloud Run has no display to borrow, so its Xvfb
    is built to the persona's own size."""
    assert fit_fingerprint_to_display(persona, 3840, 2160) is persona


def test_it_only_ever_shrinks(persona):
    small = replace(persona, viewport_width=1024, viewport_height=700)

    assert fit_fingerprint_to_display(small, 3840, 2160) is small
    fitted = fit_fingerprint_to_display(small, 1920, 1080)
    assert fitted.viewport_width == 1024, "a fitting viewport was grown"


def test_no_fingerprint_is_not_an_error():
    """Already a degraded path; it must not become a failing one."""
    assert fit_fingerprint_to_display(None, 1920, 1080) is None


def test_a_tiny_screen_still_yields_a_usable_viewport(persona):
    """Below the floor the browser would be unusable, and a run with a 40px
    viewport is worse than one that overhangs."""
    fitted = fit_fingerprint_to_display(persona, 300, 200)

    assert fitted.viewport_width >= 640
    assert fitted.viewport_height >= 480


def test_it_says_what_it_changed(persona):
    """The window being an odd size is the kind of thing that looks like a bug
    six months later unless the run explains itself."""
    messages = []
    fit_fingerprint_to_display(persona, 1920, 1080, plog=messages.append)

    assert messages and "2560x1329" in messages[0] and "1920x1080" in messages[0]


def test_a_fitting_persona_says_nothing(persona):
    messages = []
    fit_fingerprint_to_display(persona, 3840, 2160, plog=messages.append)

    assert messages == []
