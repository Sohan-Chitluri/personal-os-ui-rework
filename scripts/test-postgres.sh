#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
database_url="${DATABASE_URL:-postgresql+asyncpg://personal_os@127.0.0.1:54329/personal_os_test}"

if [[ "$database_url" != postgresql+asyncpg://* ]]; then
  echo "Refusing database-backed tests: DATABASE_URL must use postgresql+asyncpg://" >&2
  exit 2
fi

cd "$repo_root"
if [[ ! -x .venv/bin/alembic || ! -x .venv/bin/pytest ]]; then
  echo "Install the project and development dependencies in .venv before running tests." >&2
  exit 1
fi
# This reset is deliberately scoped by the exact database-name guard above.
# No development database or Docker volume is dropped.
.venv/bin/python - "$database_url" <<'PY'
import asyncio
import sys

import asyncpg
from sqlalchemy.engine import make_url

url = make_url(sys.argv[1])
if url.database != "personal_os_test":
    raise SystemExit("Refusing reset: target database must be exactly personal_os_test")
if url.host not in {"localhost", "127.0.0.1", "::1"}:
    raise SystemExit("Refusing reset: target PostgreSQL host must be local")

async def main() -> None:
    try:
        connection = await asyncpg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            database=url.database,
            timeout=3,
        )
    except Exception as exc:
        print(
            "PostgreSQL test database is unavailable; start the local server and check DATABASE_URL.\n"
            "For Compose: docker compose -f docker-compose.postgres.yml up -d --wait\n"
            f"Connection error: {type(exc).__name__}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    try:
        await connection.execute('DROP SCHEMA public CASCADE')
        await connection.execute('CREATE SCHEMA public')
        await connection.execute('GRANT ALL ON SCHEMA public TO CURRENT_USER')
    finally:
        await connection.close()

asyncio.run(main())
PY

DATABASE_URL="$database_url" ENVIRONMENT=test .venv/bin/alembic upgrade head
DATABASE_URL="$database_url" ENVIRONMENT=test .venv/bin/pytest "$@"
