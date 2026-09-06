# auto_apply_app/infrastructures/agent/fingerprint_generator.py
import hashlib
import random
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from auto_apply_app.application.service_ports.fingerprint_generator_port import FingerprintGeneratorPort
from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint


# Chrome majors currently plausible in the wild. A device is born somewhere in
# this band and walks forward from there; keep the band moving as Chrome ships.
CHROME_MAJOR_MIN = 138
CHROME_MAJOR_MAX = 142

# Screens, not viewports. The window is sized inside one of these, which is what
# makes viewport/screen coherent instead of independently random.
SCREENS_WINDOWS = [
    (1920, 1080),
    (1920, 1080),
    (1920, 1080),
    (2560, 1440),
    (1600, 900),
    (1366, 768),
    (3840, 2160),
]

SCREENS_MAC = [
    (1440, 900),
    (1512, 982),
    (1680, 1050),
    (1728, 1117),
    (2560, 1440),
]

# Windows laptops/desktops overwhelmingly run at 1.0; 1.25/1.5 are the common
# Windows display-scaling settings. 2.0 is a Mac retina value and must never
# appear on Win32 — the old table drew scale factors independent of platform.
SCALE_WINDOWS = [1.0, 1.0, 1.0, 1.25, 1.5]
SCALE_MAC = [2.0, 2.0, 1.0]

# (vendor, renderer, plausible core counts, plausible RAM in GB)
WEBGL_PROFILES_WINDOWS = [
    (
        "Google Inc. (Intel)",
        "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        [4, 8],
        [8],
    ),
    (
        "Google Inc. (Intel)",
        "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
        [8, 12],
        [8, 16],
    ),
    (
        "Google Inc. (NVIDIA)",
        "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        [6, 8, 12],
        [8, 16],
    ),
    (
        "Google Inc. (NVIDIA)",
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        [8, 12, 16],
        [16, 32],
    ),
    (
        "Google Inc. (NVIDIA)",
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        [12, 16],
        [16, 32],
    ),
    (
        "Google Inc. (AMD)",
        "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        [6, 8],
        [8, 16],
    ),
]

WEBGL_PROFILES_MAC = [
    ("Apple Inc.", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)", [8], [8, 16]),
    ("Apple Inc.", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)", [8], [8, 16]),
    ("Apple Inc.", "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)", [8, 12], [16]),
]


def _new_seed(*parts: str) -> str:
    """Short, stable hex seed. Consumed by the noise RNG inside the init script."""
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


class FingerprintGenerator(FingerprintGeneratorPort):
    """
    Generates realistic browser fingerprints.

    Devices are drawn from an UNSEEDED RNG. The previous implementation seeded
    `random.Random` with the user's UUID, which made a user's fingerprint a pure
    function of their identity — permanently frozen, and identical on every run
    for the life of the account. Rotation is impossible under that scheme, so the
    seeding is gone.

    Coherence is enforced, not hoped for: Apple GPUs only on macOS, retina scale
    factors only on Mac, core count and RAM drawn from what the chosen GPU
    actually ships with, and the window always fitting inside its own screen.
    """

    def generate_device(
        self, user_id: UUID, existing: Sequence[UserFingerprint] = ()
    ) -> UserFingerprint:
        rng = random.Random()  # unseeded: entropy from the OS, not from the user id

        # Prefer a platform the user doesn't already own, so a two-device pool
        # reads as a laptop plus a desktop rather than the same machine twice.
        taken = {fp.platform for fp in existing if not fp.is_retired}
        candidates = ["Win32", "MacIntel"]
        fresh = [p for p in candidates if p not in taken]
        platform = rng.choice(fresh or candidates)

        if platform == "MacIntel":
            screen_width, screen_height = rng.choice(SCREENS_MAC)
            device_scale_factor = rng.choice(SCALE_MAC)
            vendor, renderer, cores, memory = rng.choice(WEBGL_PROFILES_MAC)
        else:
            screen_width, screen_height = rng.choice(SCREENS_WINDOWS)
            device_scale_factor = rng.choice(SCALE_WINDOWS)
            vendor, renderer, cores, memory = rng.choice(WEBGL_PROFILES_WINDOWS)

        viewport_width, viewport_height = self._window_within(
            rng, screen_width, screen_height
        )

        now = datetime.now(timezone.utc)
        slot = max((fp.slot for fp in existing), default=-1) + 1

        return UserFingerprint(
            user_id=user_id,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            screen_width=screen_width,
            screen_height=screen_height,
            device_scale_factor=device_scale_factor,
            locale="fr-FR",
            timezone_id="Europe/Paris",
            hardware_concurrency=rng.choice(cores),
            device_memory=rng.choice(memory),
            platform=platform,
            chrome_major=rng.randint(CHROME_MAJOR_MIN, CHROME_MAJOR_MAX),
            webgl_vendor=vendor,
            webgl_renderer=renderer,
            canvas_seed=_new_seed(str(user_id), "canvas", str(rng.random())),
            audio_seed=_new_seed(str(user_id), "audio", str(rng.random())),
            slot=slot,
            session_count=0,
            created_at=now,
        )

    def derive_session_variant(
        self, device: UserFingerprint, run_token: str
    ) -> UserFingerprint:
        # Seeded on the run token so a human-review resume of the same run
        # reproduces the same window and the same noise — mid-run the browser
        # must not appear to change underneath the board.
        rng = random.Random(_new_seed(str(device.id), run_token))

        viewport_width, viewport_height = self._window_within(
            rng, device.screen_width, device.screen_height
        )

        return device.with_variant(
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            chrome_major=self._drift_chrome_major(device, rng),
            canvas_seed=_new_seed(str(device.id), run_token, "canvas"),
            audio_seed=_new_seed(str(device.id), run_token, "audio"),
        )

    # -----------------------------------------------------------------------
    # Coherence helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _window_within(rng: random.Random, screen_width: int, screen_height: int):
        """A browser window sized inside its screen.

        Most sessions are maximized; the rest are a partly-sized window. Either
        way the viewport can never exceed the screen, which the old independent
        draw of viewport and (absent) screen could not guarantee.
        """
        # Chrome's own chrome (tab strip, omnibox) eats vertical space even
        # maximized, and the OS bar takes a little more.
        browser_chrome_height = rng.randint(85, 135)
        os_bar_height = 40

        if rng.random() < 0.62:  # maximized
            width = screen_width
            height = screen_height - browser_chrome_height - os_bar_height
        else:  # windowed
            width = int(screen_width * rng.uniform(0.68, 0.94))
            height = int(
                (screen_height - os_bar_height) * rng.uniform(0.66, 0.92)
            ) - browser_chrome_height

        # Even/sane values; nobody browses in a 300px-tall window.
        width = max(1024, (width // 2) * 2)
        height = max(600, (height // 2) * 2)
        return width, height

    @staticmethod
    def _drift_chrome_major(device: UserFingerprint, rng: random.Random) -> int:
        """Walk a long-lived persona's Chrome version forward.

        Chrome ships a major roughly monthly and updates silently, so a device
        that has been around for months and still reports its birth version is
        an anomaly all by itself. Bump probabilistically with age, and never
        past the top of the plausible band.
        """
        major = device.chrome_major
        if device.created_at is None:
            return major

        created = device.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).days
        due = age_days // 30  # roughly one release per month

        for _ in range(due):
            # Real users take an update within days of it landing, not instantly.
            if rng.random() < 0.8:
                major += 1

        return min(major, CHROME_MAJOR_MAX)
