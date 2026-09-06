#!/usr/bin/env bash
# Applies every pending migration on the active chain, up to TARGET_REVISION.
#
# Alembic walks the chain itself, so naming only the LAST revision applies all
# the ones in between, in order. Bump TARGET_REVISION when a migration is added.
#
# Two gotchas this handles for you:
#
# 1. TWO ALEMBIC HEADS. This repo has c3f88a17be40 and efd03edb878e, from a
#    pre-existing baseline that never retired the legacy chain. Plain
#    `alembic upgrade head` fails with "Multiple head revisions are present",
#    so the target revision is named explicitly.
#
# 2. UNREACHABLE DIRECT URL. alembic/env.py reads DATABASE_DIRECT_URL, which
#    points at Supabase's direct host (db.<ref>.supabase.co:5432). Supabase
#    serves that over IPv6 only, so on an IPv4-only network it fails with
#    "Network is unreachable" while the app itself is fine over the pooler.
#    If the direct host can't be reached, this falls back to the pooler in
#    SESSION mode (port 5432 on the pooler host) — transaction mode (6543)
#    is not safe for DDL.
#
# Override the connection explicitly with:  ALEMBIC_URL=postgresql://... ./scripts/migrate.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
TARGET_REVISION="f1c93d6a8b02"

# --- pick a reachable connection -------------------------------------------
if [ -z "${ALEMBIC_URL:-}" ]; then
  ALEMBIC_URL="$($PY - <<'EOF'
import os, re, socket
from dotenv import load_dotenv
# Explicit path: this runs from stdin (heredoc), where find_dotenv()'s
# caller-frame walk has no frame to walk and asserts. cwd is the repo root.
load_dotenv(".env")

direct = os.getenv("DATABASE_DIRECT_URL")
pooled = os.getenv("DATABASE_URL")

def endpoint(url):
    m = re.search(r"@([^/:?]+):(\d+)", url or "")
    return (m.group(1), int(m.group(2))) if m else None

def reachable(url):
    ep = endpoint(url)
    if not ep:
        return False
    try:
        socket.create_connection(ep, timeout=6).close()
        return True
    except OSError:
        return False

if direct and reachable(direct):
    print(direct)
elif pooled:
    # Pooler SESSION mode for DDL: port 5432 on the pooler host, never 6543.
    print(re.sub(r"(@[^/:?]+):\d+", r"\1:5432", pooled))
else:
    raise SystemExit("Neither DATABASE_DIRECT_URL nor DATABASE_URL is set")
EOF
)"
fi

SAFE_HOST="$(printf '%s' "$ALEMBIC_URL" | sed -E 's|.*@([^/?]+).*|\1|')"
echo "==> migrating against ${SAFE_HOST}"
export DATABASE_DIRECT_URL="$ALEMBIC_URL"

echo
echo "==> heads"
$PY -m alembic heads

echo
echo "==> current revision(s) before"
$PY -m alembic current

echo
echo "==> upgrading to ${TARGET_REVISION} (announcements)"
$PY -m alembic upgrade "$TARGET_REVISION"

echo
echo "==> current revision(s) after"
$PY -m alembic current

echo
echo "==> verifying with preflight"
$PY scripts/preflight.py
