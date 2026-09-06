"""Preflight for the fingerprint-rotation + pacing release.

Checks the things that silently degrade instead of failing loudly, which is the
whole difficulty here: a missing column, an unresolved fingerprint, or a proxy
adapter that never initialised all let a run complete "successfully" while doing
the wrong thing.

    .venv/bin/python scripts/preflight.py            # check only
    .venv/bin/python scripts/preflight.py --resolve  # also resolve a real
                                                     # fingerprint for a user
Exit code 0 = ready, 1 = something needs attention.
"""
import argparse
import asyncio
import os
import sys
from uuid import UUID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

# Imported after load_dotenv: rotation_mode() reads the env at call time.
from auto_apply_app.application.use_cases.fingerprint_use_cases import (
    MODE_PER_RUN,
    rotation_mode,
)
from auto_apply_app.infrastructures.agent.input.backend import (
    BACKEND_X11,
    resolve_backend_name,
)

OK, WARN, BAD = "  [ok]  ", "  [warn]", "  [FAIL]"
problems = []
warnings = []


def ok(msg):
    print(f"{OK} {msg}")


def warn(msg):
    print(f"{WARN} {msg}")
    warnings.append(msg)


def bad(msg):
    print(f"{BAD} {msg}")
    problems.append(msg)


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------


def check_env():
    section("Environment")

    pace = os.getenv("AGENT_HUMAN_PACE")
    budget = os.getenv("AGENT_HUMAN_BUDGET_S")
    if pace is None:
        warn("AGENT_HUMAN_PACE unset -> defaults to 1.0 (production pace)")
    else:
        ok(f"AGENT_HUMAN_PACE={pace}")
        if float(pace) < 0.5:
            warn(f"  pace {pace} is a LOCAL testing value; prod behaviour is 1.0")
    ok(f"AGENT_HUMAN_BUDGET_S={budget or '6300 (default)'}")

    mode = rotation_mode()
    if mode == MODE_PER_RUN:
        warn("FINGERPRINT_MODE=per_run -> a brand-new device every run. The cookie "
             "jar and the sticky exit IP are keyed on the persona id, so every run "
             "logs in fresh from a new IP and the board sees the account hop "
             "machines. Experiment only; 'pool' is the default")
    else:
        ok(f"FINGERPRINT_MODE={mode} (device stable per board, session surface rotates)")

    repo = os.getenv("REPOSITORY_TYPE", "memory")
    if repo.lower() == "memory":
        warn("REPOSITORY_TYPE=memory -> personas live in RAM and vanish on exit; "
             "set it to 'database' to exercise the real rotation")
    else:
        ok(f"REPOSITORY_TYPE={repo}")

    provider = os.getenv("PROXY_PROVIDER", "none").lower()
    if provider == "twocaptcha":
        missing = [k for k in ("TWOCAPTCHA_HOST", "TWOCAPTCHA_PORT",
                               "TWOCAPTCHA_USERNAME_TEMPLATE",
                               "TWOCAPTCHA_PASSWORD_TEMPLATE")
                   if not os.getenv(k)]
        if missing:
            bad(f"PROXY_PROVIDER=twocaptcha but missing {', '.join(missing)} -> "
                f"TwoCaptchaProxyAdapter raises at startup and the agent cannot boot")
        else:
            ok("PROXY_PROVIDER=twocaptcha with all four TWOCAPTCHA_* vars present")
            _check_sticky_window()
    else:
        warn(f"PROXY_PROVIDER={provider} -> running WITHOUT a proxy. Every persona "
             f"shares one exit IP, which defeats the point of rotating fingerprints")

    if not os.getenv("GCP_SESSION_BUCKET"):
        warn("GCP_SESSION_BUCKET unset -> no durable cookie jars; every run logs in fresh")
    else:
        ok(f"GCP_SESSION_BUCKET={os.getenv('GCP_SESSION_BUCKET')}")


def _check_sticky_window():
    """The exit IP must outlive the pacing budget, or it rotates mid-run."""
    import re

    template = os.getenv("TWOCAPTCHA_USERNAME_TEMPLATE", "")
    budget = float(os.getenv("AGENT_HUMAN_BUDGET_S", 6300))
    match = re.search(r"sessTime-(\d+)", template)
    if not match:
        warn("  no sessTime found in TWOCAPTCHA_USERNAME_TEMPLATE — cannot verify "
             "the exit IP outlives the run")
        return
    sticky_s = int(match.group(1)) * 60
    if budget >= sticky_s:
        bad(f"  budget {budget:.0f}s >= sticky window {sticky_s}s: the exit IP will "
            f"change MID-RUN, which is a worse signal than not rotating at all")
    else:
        ok(f"  sticky IP holds {sticky_s}s > pacing budget {budget:.0f}s")


def check_input():
    """The input backend, and whether the machine can actually provide it."""
    from auto_apply_app.infrastructures.agent.input.backend import x11_capable

    section("Input backend")

    raw = (os.getenv("AGENT_INPUT_BACKEND") or "").strip().lower()
    forced = raw in (BACKEND_X11, "xtest", "os", "cdp")
    backend = resolve_backend_name()
    capable, why = x11_capable()
    how = f"AGENT_INPUT_BACKEND={raw}" if forced else "detected from this machine"

    if backend != BACKEND_X11:
        warn(f"cdp ({how}) -> Playwright's own input (Input.dispatchMouseEvent)")
        print(f"         because: {why}")
        print("         trusted events, but no OS pointer: a page reading "
              "screenX-clientX gets 0, and the browser's own UI is unreachable "
              "(Alt+Left, the address bar)")
        if capable:
            print("         this machine COULD do x11 — unset AGENT_INPUT_BACKEND "
                  "to let it")
        return

    ok(f"x11 ({how}) -> XTEST events from a real X device")

    # Under a forced x11 the tooling may still be absent, and each piece fails
    # soft at runtime by dropping back to CDP — which is the failure mode worth
    # catching HERE, because a run that quietly downgrades looks exactly like a
    # run that worked.
    if not capable:
        bad(f"x11 forced but unusable: {why} -> every run silently falls back to "
            "CDP input, and nav_back cannot press Alt+Left at all")
        return

    ok("Xvfb, xdotool and python-xlib all present")

    # Which display it lands on is the difference between a run you can watch
    # and a run that happens invisibly, so preflight has to say it out loud.
    from auto_apply_app.infrastructures.agent.input.display import borrowable_display

    borrowed = borrowable_display()
    if borrowed:
        ok(f"browser will launch HEADFUL on {borrowed} -> you will SEE the window")
        print("         set AGENT_X11_DISPLAY=none for a private Xvfb instead "
              "(needed to run boards in parallel here)")
    else:
        from auto_apply_app.infrastructures.agent.input.display import (
            display_has_window_manager,
        )

        existing = (os.getenv("DISPLAY") or "").strip()
        ok("browser will launch HEADFUL inside a per-board Xvfb")
        if existing and display_has_window_manager(existing):
            # Not "nothing to inherit" — we are declining something, and the
            # reason is worth a line because the alternative is a run that clicks
            # perfectly and types nothing.
            print(f"         {existing} exists but is window-managed: XTEST sends keys "
                  f"to whatever window you last clicked, so typing would be lost")
            print(f"         AGENT_X11_DISPLAY={existing} forces it anyway (then do not "
                  f"click away mid-run)")
        else:
            print("         nothing to inherit on this machine")

        from auto_apply_app.infrastructures.agent.input.display import (
            VIEW_OFF, viewer_mode,
        )
        import shutil as _shutil

        mode = viewer_mode()
        if mode == VIEW_OFF:
            print("         AGENT_X11_VIEW=window opens a live view of the run "
                  "(needs x11vnc + tigervnc-viewer)")
        elif _shutil.which("x11vnc") is None:
            bad("AGENT_X11_VIEW is set but x11vnc is not installed "
                "(apt install x11vnc tigervnc-viewer)")
        else:
            ok(f"AGENT_X11_VIEW={mode} -> the run is served over VNC on localhost")
    print("         (headless=True launches chromium-headless-shell, which has "
          "no window for a pointer to be over)")


async def check_schema():
    section("Database schema")
    if os.getenv("REPOSITORY_TYPE", "memory").lower() == "memory":
        warn("skipped (REPOSITORY_TYPE=memory)")
        return

    from sqlalchemy import text
    from auto_apply_app.infrastructures.persistence.database.session import engine

    required = {
        "slot", "board", "session_count", "created_at", "last_used_at",
        "retired_at", "chrome_major", "screen_width", "screen_height",
        "device_memory", "canvas_seed", "audio_seed",
    }
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'user_fingerprints'"
            ))
            columns = {r[0] for r in rows}

            if not columns:
                bad("user_fingerprints table not found")
                return

            missing = required - columns
            if missing:
                bad(f"migration NOT applied — missing columns: {sorted(missing)}. "
                    f"Run scripts/migrate.sh")
                return
            ok("all rotation columns present")

            if "user_agent" in columns:
                warn("user_agent column still present (harmless; it is now derived)")

            # The load-bearing change: user_id must NOT be unique any more.
            idx = await conn.execute(text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'user_fingerprints'"
            ))
            defs = {name: d for name, d in idx}
            user_idx = defs.get("ix_user_fingerprints_user_id", "")
            if "UNIQUE" in user_idx.upper():
                bad("ix_user_fingerprints_user_id is STILL UNIQUE -> a user can only "
                    "hold one persona and rotation cannot work")
            else:
                ok("user_id index is non-unique (a user can hold a pool)")

            if any("uq_user_fingerprints_user_slot" in n for n in defs):
                ok("unique constraint on (user_id, slot) present")
            else:
                bad("missing unique constraint uq_user_fingerprints_user_slot")

            counts = await conn.execute(text(
                "SELECT count(*), count(distinct user_id) FROM user_fingerprints"
            ))
            total, users = counts.first()
            ok(f"{total} persona row(s) across {users} user(s)")
    except Exception as exc:
        bad(f"could not inspect the schema: {exc!r}")
    finally:
        await engine.dispose()


def check_wiring():
    section("Code wiring")
    try:
        from auto_apply_app.infrastructures.configuration.container import (
            create_worker_application,
        )
        app = create_worker_application()
        agent = app._agent_service
        ok("worker application builds")
    except Exception as exc:
        bad(f"worker application failed to build: {exc!r}")
        return

    from auto_apply_app.infrastructures.agent.pacing import HumanPacing, Tier

    for w in (agent._apec, agent._hw, agent._wttj):
        if not isinstance(w, HumanPacing):
            bad(f"{w._source_name} is not paced")
            continue
        card = w._scaled(2.5, Tier.CARD)
        login = w._scaled(4.0, Tier.LOGIN)
        ok(f"{w._source_name:9} pace={w._pace} budget={w._human_budget_s:.0f}s "
           f"| card pause ~{card:.0f}s | login ~{login:.1f}s")

    from auto_apply_app.infrastructures.proxy.no_proxy_adapter import NoProxyAdapter
    if isinstance(agent.proxy_service, NoProxyAdapter):
        warn("proxy service resolved to NoProxyAdapter (no proxy will be used)")
    else:
        ok(f"proxy service = {type(agent.proxy_service).__name__}")

    if agent.session_store is None:
        warn("master has no session_store -> retired personas' cookie jars will be "
             "left orphaned in the bucket")
    else:
        ok("master can clean up retired personas' cookie jars")


async def check_resolve(user_id: str):
    section(f"Live fingerprint resolution for user {user_id}")
    from auto_apply_app.infrastructures.configuration.container import (
        create_worker_application,
    )

    app = create_worker_application()
    uc = app._agent_service.resolve_run_fingerprint

    for board in ("apec", "wttj"):
        result = await uc.execute(UUID(user_id), board, "preflight-token")
        if not result.is_success:
            bad(f"{board}: resolution FAILED -> the run would go out unfingerprinted "
                f"({getattr(result.error, 'message', result.error)})")
            continue
        fp = result.value.fingerprint
        ok(f"{board:5} -> {fp.platform:9} chrome {fp.chrome_major} | "
           f"{fp.hardware_concurrency}c/{fp.device_memory}GB | "
           f"{fp.viewport_width}x{fp.viewport_height} in {fp.screen_width}x{fp.screen_height}")
        print(f"          gpu={fp.webgl_renderer[:60]}")
        print(f"          persona={fp.id} slot={fp.slot} sessions={fp.session_count}")

    # Prove the rotation: same board, a different run token. What "correct" means
    # here is the opposite in each mode, so assert against the configured one
    # rather than against the pool's invariant.
    a = await uc.execute(UUID(user_id), "apec", "token-1")
    b = await uc.execute(UUID(user_id), "apec", "token-2")
    if a.is_success and b.is_success:
        fa, fb = a.value.fingerprint, b.value.fingerprint
        same_device = fa.id == fb.id and fa.webgl_renderer == fb.webgl_renderer
        rotated = fa.canvas_seed != fb.canvas_seed

        if rotation_mode() == MODE_PER_RUN:
            (ok if not same_device else bad)(
                f"device RE-MINTED each run: {not same_device}")
            if b.value.retired_ids:
                ok(f"outgoing persona retired ({len(b.value.retired_ids)}) -> its "
                   f"cookie jar is dropped, not orphaned")
            else:
                bad("per_run minted a new device without retiring the old one -> "
                    "stale jars accumulate in the bucket")
        else:
            (ok if same_device else bad)(
                f"device STABLE across runs: {same_device}")
        (ok if rotated else bad)(
            f"session surface ROTATES across runs: {rotated}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolve", metavar="USER_ID", nargs="?", const=True,
                        help="also resolve a real fingerprint for this user id")
    args = parser.parse_args()

    check_env()
    check_input()
    await check_schema()
    check_wiring()

    if args.resolve and args.resolve is not True:
        await check_resolve(args.resolve)
    elif args.resolve is True:
        warn("--resolve needs a user id, e.g. --resolve 4f0e...-...")

    print("\n" + "=" * 64)
    if problems:
        print(f"NOT READY — {len(problems)} blocking issue(s):")
        for p in problems:
            print(f"  - {p}")
    else:
        print("READY to run.")
    if warnings:
        print(f"\n{len(warnings)} warning(s) (non-blocking):")
        for w in warnings:
            print(f"  - {w}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
