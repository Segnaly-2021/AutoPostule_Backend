"""Cookie jars, exit IPs and fingerprints must all key on the same device.

Before this change they keyed on three different things — fingerprint on
user_id, sticky proxy IP on search_id, cookie jar on (user_id, board) — so a
cached cookie jar was replayed from a new exit IP on every new search, under a
fingerprint that never changed. That disagreement is the thing a job board
actually looks for.
"""
import re
from pathlib import Path
from uuid import uuid4

import pytest

from auto_apply_app.infrastructures.agent.session import browser_session_store
from auto_apply_app.infrastructures.proxy.twocaptcha_proxy_adapter import (
    TwoCaptchaProxyAdapter,
)

WORKER_DIR = Path("auto_apply_app/infrastructures/agent/workers")
LIVE_WORKERS = [
    WORKER_DIR / "apec" / "apec_worker.py",
    WORKER_DIR / "wttj" / "wttj_worker.py",
    WORKER_DIR / "hellowork" / "hw_worker.py",
]


def test_jar_key_includes_the_device():
    """Two personas on the same board must not share a jar."""
    user_id = uuid4()
    a = browser_session_store._key(user_id, "apec", "device-a")
    b = browser_session_store._key(user_id, "apec", "device-b")
    assert a != b, "different devices resolved to the same cookie jar"
    assert "device-a" in a


def test_jar_key_is_stable_for_one_device():
    user_id = uuid4()
    first = browser_session_store._key(user_id, "apec", "device-a")
    second = browser_session_store._key(user_id, "apec", "device-a")
    assert first == second, "a device would never find its own jar again"


def test_jar_key_separates_boards():
    user_id = uuid4()
    assert browser_session_store._key(user_id, "apec", "d") != \
           browser_session_store._key(user_id, "wttj", "d")


def test_store_exposes_a_delete_for_retired_devices():
    """A retired persona's jar can never be presented again, so it must be
    removable rather than orphaned in the bucket holding auth cookies."""
    assert hasattr(browser_session_store.BrowserSessionStore, "delete")


@pytest.mark.parametrize("path", LIVE_WORKERS, ids=lambda p: p.stem)
def test_worker_session_paths_are_scoped_to_the_device(path):
    src = path.read_text()
    assert "_fingerprint_id" in src, f"{path} does not track its persona"
    match = re.search(r"def _get_session_file_path.*?return os\.path\.join\([^)]*\)",
                      src, re.S)
    assert match, f"{path}: could not find _get_session_file_path"
    assert "fp" in match.group(0), (
        f"{path}: local jar path is not scoped by persona, so a rotated device "
        f"would pick up the previous one's cookies"
    )


@pytest.mark.parametrize("path", LIVE_WORKERS, ids=lambda p: p.stem)
def test_worker_reads_its_own_board_identity(path):
    """Each worker takes its slice of the per-board maps the master builds."""
    src = path.read_text()
    assert '(state.get("user_fingerprints") or {}).get(self._board_key)' in src
    assert '(state.get("proxy_configs") or {}).get(self._board_key)' in src
    assert 'state.get("user_fingerprint")' not in src, "stale single-value read"


def test_proxy_session_is_derived_from_whatever_key_it_is_handed(monkeypatch):
    """The master now passes the PERSONA id rather than the search id, so a
    device keeps its exit IP across runs."""
    monkeypatch.setenv("TWOCAPTCHA_HOST", "proxy.example")
    monkeypatch.setenv("TWOCAPTCHA_PORT", "1080")
    monkeypatch.setenv("TWOCAPTCHA_USERNAME_TEMPLATE", "user-session-{session_id}")
    monkeypatch.setenv("TWOCAPTCHA_PASSWORD_TEMPLATE", "pw")

    adapter = TwoCaptchaProxyAdapter()
    persona = str(uuid4())

    first = adapter.get_proxy_for_run("user-1", persona)
    second = adapter.get_proxy_for_run("user-1", persona)
    other = adapter.get_proxy_for_run("user-1", str(uuid4()))

    assert first["username"] == second["username"], "same device changed exit IP"
    assert first["username"] != other["username"], "different devices shared an IP"


def test_master_keys_the_proxy_on_the_persona_not_the_search():
    src = Path("auto_apply_app/infrastructures/agent/master/master_agent.py").read_text()
    assert "get_proxy_for_run(\n                str(user.id), str(run_fp.fingerprint.id)\n            )" in src \
        or "str(run_fp.fingerprint.id)" in src, "proxy is not keyed on the persona"
    assert "get_proxy_for_run(str(user.id), str(search.id))" not in src, (
        "proxy still keyed on search id — the IP and the fingerprint would "
        "rotate on unrelated schedules"
    )


@pytest.mark.parametrize("path", LIVE_WORKERS, ids=lambda p: p.stem)
def test_stealth_is_applied_before_the_fingerprint(path):
    """playwright_stealth registers its own WebGL getParameter patch hardcoded to
    "Intel Inc." / "Intel Iris OpenGL Engine". Init scripts run in registration
    order, so registering the fingerprint FIRST let stealth silently replace the
    persona's GPU — every run reported the same fictional Intel card regardless
    of which device it was supposed to be presenting.
    """
    src = path.read_text()
    for match in re.finditer(r"add_init_script\(fingerprint\.to_init_script\(\)\)", src):
        preceding = src[:match.start()]
        last_stealth = preceding.rfind("apply_stealth_async")
        # The stealth call must be the nearest preceding one inside this block,
        # i.e. closer than the previous context creation.
        last_context = preceding.rfind("new_context(")
        assert last_stealth > last_context, (
            f"{path}: fingerprint init script at offset {match.start()} is "
            f"registered before apply_stealth_async — stealth will clobber the GPU"
        )
