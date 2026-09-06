"""Fingerprint generation, coherence, and rotation.

The bug these guard against: a fingerprint derived from the user id can never
change, and a rotation that changes the *device* rather than the *session* is
worse than no rotation at all when login cookies persist across runs.
"""
import ast
import json
import re
import subprocess
import shutil
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from auto_apply_app.domain.entities.user_fingerprint import (
    UserFingerprint,
    chrome_major_from_version,
)
from auto_apply_app.infrastructures.agent.fingerprint_alignment import (
    align_fingerprint_to_browser,
)

from tests.test_pacing import LIVE_WORKERS


DRAWS = 500


# ---------------------------------------------------------------------------
# Device coherence
# ---------------------------------------------------------------------------

def test_devices_are_not_derived_from_the_user_id(generator):
    """The original bug. Same user, repeated generation -> different devices.

    The old implementation seeded random.Random with the user's UUID, so this
    produced one identical device every time, forever.
    """
    user_id = uuid4()
    devices = [generator.generate_device(user_id) for _ in range(30)]
    signatures = {
        (d.platform, d.webgl_renderer, d.screen_width, d.chrome_major)
        for d in devices
    }
    assert len(signatures) > 1, "generator is still deterministic per user"


@pytest.mark.parametrize("draw", range(1))
def test_device_is_physically_coherent(generator, draw):
    """No device may describe a machine that cannot exist."""
    for _ in range(DRAWS):
        d = generator.generate_device(uuid4())

        if d.platform == "Win32":
            assert "Apple" not in d.webgl_vendor, "Apple GPU on Windows"
            assert d.device_scale_factor != 2.0, "retina scale factor on Windows"
        else:
            assert "Apple" in d.webgl_vendor, "non-Apple GPU on macOS"

        assert d.viewport_width <= d.screen_width, "window wider than its screen"
        assert d.viewport_height <= d.screen_height, "window taller than its screen"
        assert d.viewport_width >= 1024 and d.viewport_height >= 600
        assert d.hardware_concurrency in (4, 6, 8, 12, 16)
        assert d.device_memory in (8, 16, 32)
        assert d.canvas_seed and d.audio_seed
        assert d.canvas_seed != d.audio_seed


def test_user_agent_matches_platform_and_chrome_major(generator):
    for _ in range(50):
        d = generator.generate_device(uuid4())
        ua = d.user_agent
        assert f"Chrome/{d.chrome_major}.0.0.0" in ua
        if d.platform == "MacIntel":
            assert "Macintosh" in ua
        else:
            assert "Windows NT" in ua


def test_pool_prefers_a_distinct_second_device(generator):
    user_id = uuid4()
    first = generator.generate_device(user_id)
    second = generator.generate_device(user_id, existing=[first])
    assert second.platform != first.platform
    assert second.slot == first.slot + 1


# ---------------------------------------------------------------------------
# Session variants: what rotates and what must not
# ---------------------------------------------------------------------------

def test_session_variant_rotates_only_the_volatile_surface(generator):
    device = generator.generate_device(uuid4())
    a = generator.derive_session_variant(device, "run-a")
    b = generator.derive_session_variant(device, "run-b")

    # The device is the thing a real machine never changes between sessions.
    for field in ("platform", "webgl_vendor", "webgl_renderer",
                  "hardware_concurrency", "device_memory",
                  "screen_width", "screen_height", "locale", "timezone_id"):
        assert getattr(a, field) == getattr(b, field) == getattr(device, field), field

    # The session surface is what makes two visits look like two visits.
    assert a.canvas_seed != b.canvas_seed
    assert a.audio_seed != b.audio_seed


def test_session_variant_keeps_the_persona_id(generator):
    """The cookie jar and the sticky proxy session are both keyed on this id.
    A variant that renumbered itself would orphan both."""
    device = generator.generate_device(uuid4())
    variant = generator.derive_session_variant(device, "run-a")
    assert variant.id == device.id


def test_session_variant_is_reproducible_for_the_same_run(generator):
    """A human-review resume re-derives the variant. If it were not stable the
    browser would visibly change mid-session."""
    device = generator.generate_device(uuid4())
    a = generator.derive_session_variant(device, "run-x")
    b = generator.derive_session_variant(device, "run-x")
    assert (a.canvas_seed, a.audio_seed) == (b.canvas_seed, b.audio_seed)
    assert (a.viewport_width, a.viewport_height) == (b.viewport_width, b.viewport_height)


def test_variant_window_still_fits_the_screen(generator):
    for _ in range(200):
        device = generator.generate_device(uuid4())
        v = generator.derive_session_variant(device, uuid4().hex)
        assert v.viewport_width <= v.screen_width
        assert v.viewport_height <= v.screen_height


def test_chrome_major_drifts_upward_with_age_and_is_capped(generator):
    from auto_apply_app.infrastructures.agent.fingerprint_generator import CHROME_MAJOR_MAX

    device = generator.generate_device(uuid4())
    device.chrome_major = 138
    device.created_at = datetime.now(timezone.utc) - timedelta(days=400)

    majors = {generator.derive_session_variant(device, uuid4().hex).chrome_major
              for _ in range(40)}
    assert max(majors) > 138, "a year-old device never took a Chrome update"
    assert max(majors) <= CHROME_MAJOR_MAX

    fresh = generator.generate_device(uuid4())
    fresh.created_at = datetime.now(timezone.utc)
    v = generator.derive_session_variant(fresh, "run-a")
    assert v.chrome_major == fresh.chrome_major


# ---------------------------------------------------------------------------
# Init script
# ---------------------------------------------------------------------------

def _fingerprint(**overrides) -> UserFingerprint:
    base = dict(
        user_id=uuid4(), viewport_width=1920, viewport_height=1080,
        screen_width=1920, screen_height=1080, device_scale_factor=1.0,
        locale="fr-FR", timezone_id="Europe/Paris", hardware_concurrency=8,
        device_memory=16, platform="Win32", chrome_major=141,
        webgl_vendor="Google Inc. (NVIDIA)", webgl_renderer="ANGLE (NVIDIA...)",
        canvas_seed="abc123", audio_seed="def456",
    )
    base.update(overrides)
    return UserFingerprint(**base)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_init_script_is_valid_javascript(tmp_path):
    script = tmp_path / "fp.js"
    script.write_text(_fingerprint().to_init_script())
    subprocess.run(["node", "--check", str(script)], check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_init_script_escapes_hostile_values(tmp_path):
    """to_init_script used to build JS by raw f-string. Values are ours today,
    but a quote in a renderer string would have produced a syntax error at best
    and injected code at worst."""
    fp = _fingerprint(webgl_renderer="""ANGLE'; alert("x"); //""")
    script = tmp_path / "fp.js"
    script.write_text(fp.to_init_script())
    subprocess.run(["node", "--check", str(script)], check=True, capture_output=True)
    assert json.dumps(fp.webgl_renderer) in fp.to_init_script()


def test_init_script_patches_both_webgl_prototypes():
    """Patching only WebGL1 leaves the two contexts disagreeing about the GPU."""
    script = _fingerprint().to_init_script()
    assert "WebGLRenderingContext" in script
    assert "WebGL2RenderingContext" in script


def test_init_script_keeps_user_agent_data_platform_coherent():
    """A Win32 navigator.platform paired with a macOS userAgentData brand list
    is a one-line detection. Match the actual assignment, not the surrounding
    prose, which mentions both names."""
    pattern = r"userAgentData,\s*'platform',\s*(\"[^\"]+\")"

    win = re.search(pattern, _fingerprint(platform="Win32").to_init_script())
    mac = re.search(pattern, _fingerprint(platform="MacIntel").to_init_script())
    assert win and win.group(1) == '"Windows"'
    assert mac and mac.group(1) == '"macOS"' 


def test_context_args_are_one_source_for_both_tracks():
    """The scrape and submit tracks used to build different context kwargs, so a
    single run presented two slightly different browsers."""
    args = _fingerprint().to_playwright_context_args()
    assert set(args) == {
        "user_agent", "viewport", "screen", "device_scale_factor",
        "locale", "timezone_id", "has_touch", "is_mobile",
    }
    assert args["viewport"]["width"] <= args["screen"]["width"]


# ---------------------------------------------------------------------------
# Aligning the claimed Chrome version with the browser that actually launched
# ---------------------------------------------------------------------------

def test_chrome_major_is_parsed_from_a_playwright_version():
    assert chrome_major_from_version("149.0.7827.53") == 149
    assert chrome_major_from_version("141.0.7390.37") == 141


@pytest.mark.parametrize("garbage", ["", "not-a-version", None, "x.y.z"])
def test_unparseable_version_is_not_a_crash(garbage):
    """Cosmetic alignment must never be what kills a run."""
    assert chrome_major_from_version(garbage) is None


def _persona(**overrides):
    base = dict(
        user_id=uuid4(), viewport_width=1280, viewport_height=720,
        screen_width=1920, screen_height=1080, device_scale_factor=1.0,
        locale="fr-FR", timezone_id="Europe/Paris", hardware_concurrency=8,
        device_memory=8, platform="Win32", chrome_major=138,
        webgl_vendor="Google Inc.", webgl_renderer="ANGLE",
        canvas_seed="abc123", audio_seed="def456", slot=0, board="apec",
    )
    base.update(overrides)
    return UserFingerprint(**base)


def test_ua_claims_the_version_actually_running():
    """A UA claiming 138 on an engine that answers like 149 is a self-tell."""
    aligned = align_fingerprint_to_browser(_persona(chrome_major=138), "149.0.7827.53")
    assert aligned.chrome_major == 149
    assert "Chrome/149.0.0.0" in aligned.user_agent


def test_alignment_keeps_the_device_identity():
    """Only the claimed version moves — the persona is still the same machine,
    and the id is what cookie jars and the sticky proxy are keyed on."""
    persona = _persona(chrome_major=138)
    aligned = align_fingerprint_to_browser(persona, "149.0.7827.53")
    assert aligned.id == persona.id
    assert (aligned.slot, aligned.board) == (persona.slot, persona.board)
    assert (aligned.canvas_seed, aligned.audio_seed) == (persona.canvas_seed, persona.audio_seed)
    assert aligned.platform == persona.platform
    # the stored row is untouched; only the variant presents the new major
    assert persona.chrome_major == 138


def test_alignment_is_a_noop_without_a_fingerprint_or_version():
    assert align_fingerprint_to_browser(None, "149.0.7827.53") is None
    persona = _persona()
    assert align_fingerprint_to_browser(persona, "garbage") is persona


@pytest.mark.parametrize("path", LIVE_WORKERS, ids=lambda p: p.stem)
def test_every_live_worker_aligns_before_building_its_context(path):
    """Static guard: a worker that launches a browser must align the persona to
    it before the context is built, or the UA silently contradicts the engine.
    """
    tree = ast.parse(path.read_text())
    aligns, consumers = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name == "align_fingerprint_to_browser":
            aligns.append(node.lineno)
        elif name in ("to_playwright_context_args", "to_init_script"):
            consumers.append(node.lineno)

    assert aligns, f"{path} never aligns its fingerprint to the launched browser"
    for line in consumers:
        assert any(a < line for a in aligns), (
            f"{path}:{line} builds from the fingerprint before any alignment"
        )
