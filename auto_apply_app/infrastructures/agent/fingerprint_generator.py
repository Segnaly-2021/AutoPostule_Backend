# auto_apply_app/infrastructures/agent/fingerprint_generator.py
import random
from uuid import UUID

from auto_apply_app.application.service_ports.fingerprint_generator_port import FingerprintGeneratorPort
from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint


# Realistic recent Chrome versions on common platforms.
# Each entry is a (user_agent, platform) pair to keep them consistent.
USER_AGENT_PROFILES = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Win32",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Win32",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Win32",
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "MacIntel",
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "MacIntel",
    ),
]

VIEWPORTS = [
    (1920, 1080),
    (1536, 864),
    (1440, 900),
    (1366, 768),
    (1680, 1050),
    (2560, 1440),
]

HARDWARE_CONCURRENCY = [4, 6, 8, 8, 8, 12, 16]

DEVICE_SCALE_FACTORS = [1.0, 1.0, 1.25, 1.5, 2.0]

WEBGL_PROFILES_WINDOWS = [
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
]

WEBGL_PROFILES_MAC = [
    ("Apple Inc.", "Apple M1"),
    ("Apple Inc.", "Apple M2"),
    ("Apple Inc.", "Apple M3"),
]


class FingerprintGenerator(FingerprintGeneratorPort):
    """
    Generates a deterministic, realistic browser fingerprint for a user.
    Same user_id always produces the same fingerprint.
    Different users get statistically different fingerprints.
    Platform/WebGL combinations are constrained to be physically plausible
    (e.g. Apple GPUs only on macOS user agents).
    """

    def generate_for_user(self, user_id: UUID) -> UserFingerprint:
        seed = int(str(user_id).replace("-", ""), 16)
        rng = random.Random(seed)

        user_agent, platform = rng.choice(USER_AGENT_PROFILES)
        viewport_width, viewport_height = rng.choice(VIEWPORTS)

        if platform == "MacIntel":
            webgl_vendor, webgl_renderer = rng.choice(WEBGL_PROFILES_MAC)
        else:
            webgl_vendor, webgl_renderer = rng.choice(WEBGL_PROFILES_WINDOWS)

        return UserFingerprint(
            user_id=user_id,
            user_agent=user_agent,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            device_scale_factor=rng.choice(DEVICE_SCALE_FACTORS),
            locale="fr-FR",
            timezone_id="Europe/Paris",
            hardware_concurrency=rng.choice(HARDWARE_CONCURRENCY),
            platform=platform,
            webgl_vendor=webgl_vendor,
            webgl_renderer=webgl_renderer,
        )