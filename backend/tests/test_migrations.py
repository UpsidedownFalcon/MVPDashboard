"""S2-T01 — migration runner tests. Need the db reachable on 127.0.0.1:5432
(`docker compose --profile debug up -d db db-debug`); skip otherwise.

Runs against a throwaway database created per test run, so the dev database is
never touched.
"""

from __future__ import annotations

from pathlib import Path

import asyncio
import uuid

import asyncpg
import pytest

from common.config import Settings
from migrations import migrate as migrate_mod
from migrations.migrate import apply_migrations, dsn, pg_interval

HOST = "127.0.0.1"


def _settings(db_name: str) -> Settings:
    # password comes from the repo .env (compose used the same values)
    base = Settings()
    return Settings(postgres_host=HOST, postgres_db=db_name,
                    postgres_password=base.postgres_password,
                    postgres_user=base.postgres_user)


@pytest.fixture()
async def scratch_db():
    admin_settings = _settings(Settings().postgres_db)
    try:
        admin = await asyncio.wait_for(asyncpg.connect(dsn(admin_settings)), timeout=3)
    except (OSError, asyncio.TimeoutError, asyncpg.PostgresError):
        pytest.skip("db not reachable on 127.0.0.1:5432 (debug profile down)")
    name = f"migtest_{uuid.uuid4().hex[:12]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    settings = _settings(name)
    conn = await asyncpg.connect(dsn(settings))
    try:
        yield conn, settings
    finally:
        await conn.close()
        await admin.execute(f'DROP DATABASE "{name}" (FORCE)')
        await admin.close()


def test_pg_interval() -> None:
    assert pg_interval("30d") == "30 days"
    assert pg_interval("90s") == "90 seconds"
    assert pg_interval("2h") == "2 hours"
    with pytest.raises(ValueError):
        pg_interval("30x")


async def test_migrate_idempotent_and_cagg(scratch_db) -> None:
    conn, settings = scratch_db

    applied = await apply_migrations(conn, settings)
    assert applied == ["001_init.sql", "002_insight_actions.sql",
                       "003_insight_action_grouping.sql",
                       "004_insight_decisions.sql",
                       "005_sleeve_units.sql",
                       "006_sleeve_side_backfill.sql"]

    # 002: action-first insight columns exist (nullable TEXT)
    # 003: action_id groups rules onto one imperative; reason is the short bullet
    insight_cols = {
        r["column_name"]
        for r in await conn.fetch(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name = 'insights'"""
        )
    }
    assert {"action", "rationale", "action_id", "reason"} <= insight_cols

    # all tables exist (insight_decisions: migration 004, Adopt/Override;
    # sleeve_units: migration 005, unilateral knee sleeves)
    tables = {
        r["tablename"]
        for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    assert {"users", "devices", "metrics", "forecasts", "insights",
            "insight_decisions", "sleeve_units", "schema_migrations"} <= tables

    # metrics is a hypertable with a retention policy from METRICS_RETENTION
    hyper = await conn.fetchrow(
        "SELECT * FROM timescaledb_information.hypertables WHERE hypertable_name='metrics'"
    )
    assert hyper is not None
    retention = await conn.fetchrow(
        """SELECT config ->> 'drop_after' AS drop_after
           FROM timescaledb_information.jobs
           WHERE proc_name = 'policy_retention' AND hypertable_name = 'metrics'"""
    )
    assert retention is not None
    assert retention["drop_after"] == pg_interval(settings.metrics_retention_raw)

    # second run is a no-op
    assert await apply_migrations(conn, settings) == []

    # a metrics row lands in the cagg after a manual refresh
    await conn.execute(
        """INSERT INTO metrics (time, device_id, m1, m2, m3, m4, m5, composite, quality)
           VALUES (now() - interval '2 minutes', '30', 10, 20, 30, NULL, NULL, 42.5, 0.97)"""
    )
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")
    row = await conn.fetchrow("SELECT * FROM metrics_1m WHERE device_id='30'")
    assert row is not None
    assert row["composite"] == pytest.approx(42.5)
    assert row["m4"] is None          # avg() skips NULLs / all-NULL -> NULL
    assert row["n"] == 1


async def test_006_seeds_the_side_of_legacy_unpaired_sleeves(scratch_db) -> None:
    """Rows registered before decision H (side NULL = "not set yet") get the
    side their wire source_id implies; explicit and paired sides are kept."""
    conn, settings = scratch_db
    await apply_migrations(conn, settings)
    await conn.execute(
        """INSERT INTO sleeve_units
               (unit_id, wire_device_id, wire_source_id, rig_id, side, accel_fs_g, gyro_fs_dps)
           VALUES ('u7-0', 7, 0, 'u7-0', NULL,    32, 4000),
                  ('u7-1', 7, 1, 'u7-1', NULL,    32, 4000),
                  ('u8-0', 8, 0, 'u8-0', 'right', 32, 4000),
                  ('u9-0', 9, 0, 'u9-0', 'left',  32, 4000),
                  ('u9-1', 9, 1, 'u9-0', 'right', 32, 4000)"""
    )
    sql = (Path(migrate_mod.__file__).parent / "006_sleeve_side_backfill.sql").read_text()
    await conn.execute(sql)
    rows = {r["unit_id"]: r["side"] for r in await conn.fetch(
        "SELECT unit_id, side FROM sleeve_units ORDER BY unit_id")}
    assert rows == {"u7-0": "left", "u7-1": "right", "u8-0": "right",
                    "u9-0": "left", "u9-1": "right"}
