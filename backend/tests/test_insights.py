"""S2-T05 — insight rules, cooldown suppression, endpoint."""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.jobs.insights import Ctx, InsightJob, RULES
from api.routes.insights import router as insights_router
from common.config import Settings
from db_utils import connect_admin, create_scratch_db, drop_scratch_db
from migrations.migrate import dsn

RULE = {r.rule_id: r for r in RULES}


def make_ctx(comp_avg=50.0, quality=1.0, mid_trend="flat",
             forecasts=None, name="Alice") -> Ctx:
    def window(label, avg, trend):
        return {"window": label, "from": "2026-08-02T12:00:00.000Z",
                "m": [None] * 5,
                "composite": {"avg": avg, "min": avg, "max": avg},
                "quality": quality, "trend": trend}
    return Ctx(
        device_id="30", display_name=name,
        windows=[window("5m", comp_avg, "flat"),
                 window("30m", comp_avg, mid_trend),
                 window("2h", comp_avg, "flat")],
        forecasts=forecasts,
        settings=Settings(_env_file=None),
    )


# --- rule predicates on synthetic ctx ----------------------------------------

def test_composite_high_thresholds() -> None:
    rule = RULE["composite_high"]
    assert rule.evaluate(make_ctx(comp_avg=50.0)) is None
    warn = rule.evaluate(make_ctx(comp_avg=75.0))
    assert warn["severity"] == "warning"
    alert = rule.evaluate(make_ctx(comp_avg=90.0))
    assert alert["severity"] == "alert"
    msg = rule.message(make_ctx(comp_avg=90.0, name="Bob"), alert)
    assert "Bob" in msg and "reducing intensity" in msg
    # empty window (no data) never fires
    ctx = make_ctx()
    ctx.windows[0]["composite"]["avg"] = None
    assert rule.evaluate(ctx) is None


def test_rising_risk_needs_trend_and_forecast() -> None:
    rule = RULE["rising_risk"]
    crossing = [{"horizon": "10m", "pred": 60.0}, {"horizon": "30m", "pred": 88.0}]
    assert rule.evaluate(make_ctx(mid_trend="flat", forecasts=crossing)) is None
    assert rule.evaluate(make_ctx(mid_trend="up", forecasts=None)) is None
    assert rule.evaluate(
        make_ctx(mid_trend="up", forecasts=[{"horizon": "10m", "pred": 60.0}])
    ) is None
    ev = rule.evaluate(make_ctx(mid_trend="up", forecasts=crossing))
    assert ev["pred"] == pytest.approx(88.0) and ev["horizon"] == "30m"
    msg = rule.message(make_ctx(name="Cara"), ev)
    assert "Cara" in msg and "88.00" in msg and "30m" in msg and "rest" in msg


def test_data_quality_info() -> None:
    rule = RULE["data_quality"]
    assert rule.evaluate(make_ctx(quality=0.95)) is None
    ev = rule.evaluate(make_ctx(quality=0.5))
    assert ev["quality"] == pytest.approx(0.5)
    assert rule.severity == "info"
    assert "check sensor fit" in rule.message(make_ctx(), ev)


# --- engine against a scratch db ---------------------------------------------

@pytest.fixture()
async def scratch_app():
    admin = await connect_admin()
    name, settings, conn = await create_scratch_db(admin)
    settings = settings.model_copy(update={"past_windows_raw": "2m,10m,30m"})
    pool = await asyncpg.create_pool(dsn(settings), min_size=0, max_size=3)
    app = FastAPI()
    app.include_router(insights_router)
    app.state.pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            yield settings, conn, pool, client
        finally:
            await pool.close()
            await conn.close()
            await drop_scratch_db(admin, name)
            await admin.close()


async def test_job_fires_once_per_cooldown_and_endpoint(scratch_app) -> None:
    settings, conn, pool, client = scratch_app
    await conn.execute(
        "INSERT INTO devices (device_id, display_name) VALUES ('30','Renamed Athlete')"
    )
    # 2 minutes of composite 90 (above alert threshold 85), quality 0.5 (<0.8)
    m_now = (await conn.fetchrow("SELECT date_trunc('minute', now()) AS m"))["m"]
    rows = []
    for minute in (2, 1):
        for i in range(10):
            t = m_now - timedelta(minutes=minute) + timedelta(seconds=6 * i)
            rows.append((t, "30", None, None, None, None, None, 90.0, 0.5))
    await conn.copy_records_to_table(
        "metrics", records=rows,
        columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                 "composite", "quality"),
    )

    job = InsightJob(settings, pool)
    assert await job.run_once() == 2      # composite_high (alert) + data_quality
    assert await job.run_once() == 0      # cooldown suppresses both
    assert job.runs == 2 and job.last_error is None

    resp = await client.get("/api/insights", params={"device": "30"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_rule = {i["rule_id"]: i for i in body}
    high = by_rule["composite_high"]
    assert high["severity"] == "alert"
    assert "Renamed Athlete" in high["message"]     # message uses display_name
    assert high["context"]["composite_avg"] == pytest.approx(90.0)
    assert by_rule["data_quality"]["severity"] == "info"

    # newest-first ordering + limit + device filter
    assert body[0]["insight_id"] >= body[1]["insight_id"]
    assert len((await client.get("/api/insights", params={"limit": 1})).json()) == 1
    assert (await client.get("/api/insights", params={"device": "99"})).json() == []
