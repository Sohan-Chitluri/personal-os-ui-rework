# PostgreSQL development and integration testing

The backend's canonical database is PostgreSQL 16, matching the current RDS
engine target in `deploy/terraform/rds.tf`. SQLite remains available only for
dialect compilation and pure type-semantics tests; application DB operations
require PostgreSQL.

## Docker Compose

The Compose file pins `postgres:16.4-bookworm`, publishes only on loopback port
54329, and waits for `pg_isready`. First initialization creates
`personal_os_dev` and a separate `personal_os_test` database. The development
database uses a named volume and is retained by normal `down` operations.

```bash
docker compose -f docker-compose.postgres.yml up -d --wait
docker compose -f docker-compose.postgres.yml ps
docker compose -f docker-compose.postgres.yml logs postgres
docker compose -f docker-compose.postgres.yml down
```

Compose uses PostgreSQL `trust` authentication only inside this local container,
bound to `127.0.0.1`; never reuse these settings for a shared or deployed server.
No application password is embedded in the repository. For local API work, set
the URL explicitly:

```bash
export DATABASE_URL='postgresql+asyncpg://personal_os@127.0.0.1:54329/personal_os_dev'
export ENVIRONMENT=local
uv run alembic upgrade head
uv run uvicorn personal_os.api.main:app --reload
```

## PostgreSQL integration tests

Install the project and development extras in `.venv`, then use the dedicated
test database. The command validates the URL scheme and exact database name,
checks PostgreSQL readiness, recreates only the `public` schema in that named
test database, applies migrations, then runs pytest. Each test fixture gets its
own temporary schema inside the test database. The command refuses SQLite URLs
and other database names; it never touches the dev database or Compose volume.

```bash
docker compose -f docker-compose.postgres.yml up -d --wait
scripts/test-postgres.sh tests/db tests/api tests/services
```

To target another local PostgreSQL installation, set `DATABASE_URL` to a local
connection whose database is exactly `personal_os_test`. The reset script
requires a loopback hostname. Make sure the target is disposable. Tests that
use the `db_engine` fixture create a temporary PostgreSQL schema per test;
database-independent tests need no PostgreSQL server.

## Native PostgreSQL option

If Docker is unavailable, use an already available PostgreSQL 16 installation
or an ephemeral package environment such as Nix (`nix shell nixpkgs#postgresql_16`).
Run `initdb` and `pg_ctl` with their data and socket directories under a private
temporary directory, bind only to `127.0.0.1`, and create `personal_os_dev` plus
`personal_os_test` owned by the local test user. Set `DATABASE_URL` to the
matching local URL. Do not install system-wide packages unless separately
authorized. Stop the server with `pg_ctl -D <temporary-data-dir> stop`; remove
only the temporary cluster directory you created.

## Migrations and schema changes

Apply migrations with `uv run alembic upgrade head`. Review a migration against
a fresh PostgreSQL 16 database before using it against persistent data. The
integration suite checks migration upgrade/downgrade behavior and database
constraints against PostgreSQL. SQLite migration execution is not supported.

## Why SQLite async tests were removed

The previous tests stalled on their first `aiosqlite` connection. A minimal
reproduction showed `sqlite3.connect` finishing on the aiosqlite worker thread
and `call_soon_threadsafe` queueing the connection future, while the Python
3.11.16 asyncio selector loop remained blocked waiting and never processed the
callback. Wrapping that same await in `asyncio.wait_for` made it complete, which
identified a lost selector wakeup in the current local runtime rather than a
slow SQLite operation. To avoid concealing the defect with a timeout and to
match production semantics, async database tests now require PostgreSQL. Pure
SQLite dialect compilation and timezone type checks remain database-free.
