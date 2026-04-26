# auto_apply_app/infrastructures/agent/human_behavior.py
import asyncio
import random
from playwright.async_api import Locator


async def human_delay(min_ms: int = 500, max_ms: int = 2000) -> None:
    """
    Sleep for a random duration. Use BETWEEN actions, not as a substitute
    for semantic waits (wait_for_selector, etc.).
    """
    delay_seconds = random.uniform(min_ms / 1000, max_ms / 1000)
    await asyncio.sleep(delay_seconds)


async def human_type(locator: Locator, text: str, min_delay: int = 50, max_delay: int = 150) -> None:
    """
    Types text character-by-character with realistic per-key jitter.
    Replaces locator.fill() for credential and form inputs.
    fill() pastes the entire string instantly via JavaScript, which is
    highly detectable. type() simulates real keystrokes.
    """
    if not text:
        return
    delay = random.randint(min_delay, max_delay)
    await locator.type(text, delay=delay)


async def human_click(locator: Locator, hesitation: bool = True) -> None:
    """
    Clicks an element with a small human-like hesitation before the click.
    Real users don't click the moment an element appears — they take a
    fraction of a second to register it visually and move their cursor.
    """
    if hesitation:
        await human_delay(200, 800)
    await locator.click()


async def human_scroll(page, distance: int = None) -> None:
    """
    Scrolls the page by a random amount to simulate browsing behavior.
    """
    if distance is None:
        distance = random.randint(200, 600)

    steps = random.randint(3, 6)
    step_size = distance // steps

    for _ in range(steps):
        await page.mouse.wheel(0, step_size)
        await human_delay(80, 200)


async def human_warmup(page, base_url: str) -> None:
    """
    Performs a brief 'warmup' on the landing page after navigation.
    Real users don't immediately interact — they look around, scroll
    a little. Mimics that pattern.
    """
    await human_delay(800, 2000)
    try:
        await human_scroll(page, distance=random.randint(150, 400))
        await human_delay(500, 1500)
    except Exception:
        pass