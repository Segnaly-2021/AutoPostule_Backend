"""Residential bandwidth: what the browser is allowed to pay for.

Residential proxy traffic is billed per GB and the agent reads the DOM, never a
picture — so hero images and autoplaying video are pure cost. This asserts the
three things that make blocking them safe rather than merely cheap: the switch is
off unless asked, a blocked image still LOOKS loaded to the page, and the handler
cannot starve the tracking blocker HelloWork registers on the same context.
"""
import os

import pytest

from auto_apply_app.infrastructures.agent.pacing import (
    ASSET_ALLOW_HOSTS,
    HumanPacing,
    blocked_asset_types,
)


class Probe(HumanPacing):
    """Just enough HumanPacing to call _conserve_bandwidth."""

    def __init__(self):
        self._source_name = "TEST"

    def _plog(self, message):
        pass


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("AGENT_BLOCK_HEAVY_ASSETS", raising=False)


# ----------------------------------------------------------------------
# The switch
# ----------------------------------------------------------------------

def test_default_blocks_nothing():
    """An unconfigured run must behave exactly as it did before this existed."""
    assert blocked_asset_types() == frozenset()


@pytest.mark.parametrize("value", ["on", "1", "true", "yes", "images", "media"])
def test_on_blocks_images_and_video(monkeypatch, value):
    monkeypatch.setenv("AGENT_BLOCK_HEAVY_ASSETS", value)
    assert blocked_asset_types() == {"image", "media"}


@pytest.mark.parametrize("value", ["aggressive", "max", "all"])
def test_aggressive_adds_fonts(monkeypatch, value):
    monkeypatch.setenv("AGENT_BLOCK_HEAVY_ASSETS", value)
    assert blocked_asset_types() == {"image", "media", "font"}


@pytest.mark.parametrize("value", ["off", "0", "false", "no", ""])
def test_off_values_block_nothing(monkeypatch, value):
    monkeypatch.setenv("AGENT_BLOCK_HEAVY_ASSETS", value)
    assert blocked_asset_types() == frozenset()


def test_a_typo_fetches_everything(monkeypatch):
    """Fail open, not closed: an unrecognised value must not silently break a run
    by blocking something. It warns and fetches normally."""
    monkeypatch.setenv("AGENT_BLOCK_HEAVY_ASSETS", "aggresive")  # misspelled
    assert blocked_asset_types() == frozenset()


def test_captcha_hosts_are_allowlisted():
    """A challenge that cannot draw itself is a login that cannot complete, and
    it fails looking like a selector bug."""
    for needle in ("turnstile", "captcha", "challenges.cloudflare.com"):
        assert needle in ASSET_ALLOW_HOSTS


# ----------------------------------------------------------------------
# Behaviour against a real browser
# ----------------------------------------------------------------------

PAGE = (
    b"<html><body>"
    b"<img src='/hero.jpg'>"
    b"<script src='/gtm-googletagmanager.com.js'></script>"
    b"<script src='/app.js'></script>"
    b"</body></html>"
)


async def _serve(reached):
    """A stand-in origin. Anything that arrives here is a byte we paid for."""
    import asyncio

    async def handle(reader, writer):
        line = await reader.readline()
        parts = line.split()
        path = parts[1].decode() if len(parts) > 1 else "/"
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        reached.append(path)
        body = PAGE if path == "/" else b"x" * 10_000
        ctype = b"text/html" if path == "/" else b"application/octet-stream"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
            b"Connection: close\r\n\r\n" % (ctype, len(body)) + body
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def _run(monkeypatch, policy, *, reverse_order=False):
    """Drive a real Chromium with the worker's handler chain."""
    from playwright.async_api import async_playwright

    if policy:
        os.environ["AGENT_BLOCK_HEAVY_ASSETS"] = policy
    reached = []
    server = await _serve(reached)
    port = server.sockets[0].getsockname()[1]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()

        async def block_tracking(route):
            # A copy of HelloWork's handler, including the continue_() that makes
            # ordering matter.
            if "googletagmanager.com" in route.request.url:
                await route.abort()
            else:
                await route.continue_()

        probe = Probe()
        if reverse_order:
            await probe._conserve_bandwidth(context)
            await context.route("**/*", block_tracking)
        else:
            await context.route("**/*", block_tracking)
            await probe._conserve_bandwidth(context)

        page = await context.new_page()
        await page.goto(f"http://127.0.0.1:{port}/", wait_until="load")
        await page.wait_for_timeout(300)
        # complete + naturalWidth, not an onload listener: the stand-in GIF is
        # 42 bytes and can finish before an inline script even attaches one.
        # An ABORTED image is also `complete`, but its naturalWidth stays 0 --
        # which is exactly the difference this is here to measure.
        img_loaded = await page.evaluate(
            "(() => { const i = document.querySelector('img');"
            " return i.complete && i.naturalWidth > 0; })()"
        )
        await browser.close()

    server.close()
    return reached, img_loaded, getattr(probe, "_blocked_assets", None)


@pytest.mark.asyncio
async def test_images_and_video_never_reach_the_network(monkeypatch):
    reached, _, saved = await _run(monkeypatch, "on")
    assert "/hero.jpg" not in reached
    assert saved["image"] == 1
    # The things the agent actually needs still arrive.
    assert "/" in reached and "/app.js" in reached


@pytest.mark.asyncio
async def test_a_blocked_image_still_looks_loaded(monkeypatch):
    """The reason images are fulfilled with a transparent GIF instead of aborted.
    Both save the same bytes; only one keeps onload firing, which is what the
    lazy-loading scroll on these boards waits for."""
    _, img_loaded, _ = await _run(monkeypatch, "on")
    assert img_loaded is True


@pytest.mark.asyncio
async def test_it_does_not_starve_the_tracking_blocker(monkeypatch):
    """Both handlers live on one context in HelloWork. This one uses fallback(),
    so the tracking blocker still sees every request it cares about."""
    reached, _, _ = await _run(monkeypatch, "on")
    assert not any("googletagmanager" in path for path in reached)


@pytest.mark.asyncio
async def test_registering_before_the_tracking_blocker_leaks(monkeypatch):
    """Guards the ordering comment in the workers. Playwright runs the most
    recently registered route first, and the tracking blocker ends its chain with
    continue_() — so registering this one first means images go straight out and
    the whole feature silently does nothing."""
    reached, _, saved = await _run(monkeypatch, "on", reverse_order=True)
    assert "/hero.jpg" in reached
    assert saved["image"] == 0


@pytest.mark.asyncio
async def test_unset_fetches_everything(monkeypatch):
    reached, _, saved = await _run(monkeypatch, None)
    assert "/hero.jpg" in reached
    assert saved is None
