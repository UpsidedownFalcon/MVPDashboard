"""Idempotent migration runner (S2-T01).

Applies `NNN_*.sql` files from this directory in filename order, recording each
in `schema_migrations`. Each file runs inside one transaction, EXCEPT segments
introduced by a `-- NOTRANSACTION` marker line: those are executed
statement-by-statement in autocommit mode (Timescale forbids continuous-
aggregate DDL inside a transaction block).

`{{METRICS_RETENTION}}` is substituted from config (duration syntax `30d` →
Postgres interval `'30 days'`).

Run automatically by the api container entrypoint before uvicorn; also
runnable manually: `uv run python -m migrations.migrate`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

import asyncpg

from common.config import Settings, get_settings
from common.durations import parse_duration

log = logging.getLogger("migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent
_MARKER = "-- NOTRANSACTION"
_CONNECT_TIMEOUT_S = 60.0

_PG_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def pg_interval(duration: str) -> str:
    """`30d` -> `30 days` (validated via the shared duration parser first)."""
    parse_duration(duration)  # raises on bad syntax
    match = re.fullmatch(r"(\d+)([smhdw])", duration.strip())
    assert match is not None
    return f"{match.group(1)} {_PG_UNITS[match.group(2)]}"


def dsn(settings: Settings) -> str:
    return (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


def _split_segments(sql: str) -> list[tuple[bool, str]]:
    """-> [(in_transaction, sql_text), ...] — segment 0 transactional, every
    marker-introduced segment autocommit."""
    parts = re.split(rf"^{re.escape(_MARKER)}\s*$", sql, flags=re.MULTILINE)
    return [(i == 0, part.strip()) for i, part in enumerate(parts) if part.strip()]


def _statements(segment: str) -> list[str]:
    """`;`-split after dropping `--` comment lines (a `;` inside a comment would
    otherwise split mid-statement and emit comment-only statements, which crash
    asyncpg's simple-query path with a NULL command tag). Fine for our DDL —
    no function bodies, no `;` inside string literals."""
    code = "\n".join(
        line for line in segment.splitlines() if not line.lstrip().startswith("--")
    )
    return [s.strip() for s in code.split(";") if s.strip()]


# "already exists" SQLSTATEs: duplicate table/matview/index, duplicate object
# (policies), duplicate function. Skipping these per-statement makes a rerun
# self-heal after a partially applied file (e.g. crash between the committed
# transactional segment and the cagg segment).
_DUPLICATE_SQLSTATES = {"42P07", "42710", "42723"}


def _is_duplicate(exc: asyncpg.PostgresError) -> bool:
    return getattr(exc, "sqlstate", None) in _DUPLICATE_SQLSTATES or (
        "already exists" in str(exc)
    )


async def connect(settings: Settings, timeout_s: float = _CONNECT_TIMEOUT_S) -> asyncpg.Connection:
    """Retry until the db is up — covers first-boot races on manual runs
    (compose gates the api container on the db healthcheck already)."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return await asyncpg.connect(dsn(settings))
        except (OSError, asyncpg.PostgresError) as exc:
            if time.monotonic() >= deadline:
                raise
            log.info("db not ready (%s) — retrying", exc)
            await asyncio.sleep(1)


async def apply_migrations(conn: asyncpg.Connection, settings: Settings) -> list[str]:
    """Apply pending migrations; returns the filenames applied this run."""
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               filename   TEXT PRIMARY KEY,
               applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
           )"""
    )
    done = {
        r["filename"]
        for r in await conn.fetch("SELECT filename FROM schema_migrations")
    }
    applied: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if path.name in done:
            log.info("skip %s (already applied)", path.name)
            continue
        sql = path.read_text(encoding="utf-8").replace(
            "{{METRICS_RETENTION}}", pg_interval(settings.metrics_retention_raw)
        )
        log.info("applying %s", path.name)
        for in_txn, segment in _split_segments(sql):
            if in_txn:
                async with conn.transaction():
                    for stmt in _statements(segment):
                        try:
                            # nested block = savepoint, so a skipped duplicate
                            # doesn't abort the outer transaction
                            async with conn.transaction():
                                await conn.execute(stmt)
                        except asyncpg.PostgresError as exc:
                            if not _is_duplicate(exc):
                                raise
                            log.info("skip (already exists): %.60s…", stmt)
            else:
                # one statement per execute -> true autocommit, no implicit
                # multi-statement transaction (Timescale cagg requirement)
                for stmt in _statements(segment):
                    try:
                        await conn.execute(stmt)
                    except asyncpg.PostgresError as exc:
                        if not _is_duplicate(exc):
                            raise
                        log.info("skip (already exists): %.60s…", stmt)
        await conn.execute(
            "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
        )
        applied.append(path.name)
    return applied


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    conn = await connect(settings)
    try:
        applied = await apply_migrations(conn, settings)
    finally:
        await conn.close()
    log.info("migrations complete (%d applied)", len(applied))


if __name__ == "__main__":
    asyncio.run(main())
