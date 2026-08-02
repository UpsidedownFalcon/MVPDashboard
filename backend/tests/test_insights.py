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
             forecasts=None, name="Alice", baseline_quality=None) -> Ctx:
    """`baseline_quality` sets the LONGEST window's quality independently, so
    the relative data_quality rule can be exercised. Default: same as `quality`,
    i.e. a device whose link has been uniformly this good (or bad) throughout."""
    if baseline_quality is None:
        baseline_quality = quality

    def window(label, avg, trend, q):
        return {"window": label, "from": "2026-08-02T12:00:00.000Z",
                "m": [None] * 5,
                "composite": {"avg": avg, "min": avg, "max": avg},
                "quality": q, "trend": trend}
    return Ctx(
        device_id="30", display_name=name,
        windows=[window("5m", comp_avg, "flat", quality),
                 window("30m", comp_avg, mid_trend, quality),
                 window("2h", comp_avg, "flat", baseline_quality)],
        forecasts=forecasts,
        settings=Settings(_env_file=None),
    )


# --- rule predicates on synthetic ctx ----------------------------------------

def test_composite_high_thresholds() -> None:
    rule = RULE["composite_high"]
    # Values straddle the configured thresholds (warn 85 / alert 92). 75 used to
    # be a warning; after the SPEC §6.1 composite rescale that is ordinary hard
    # training and must NOT fire — which is the whole point of raising them.
    assert rule.evaluate(make_ctx(comp_avg=50.0)) is None
    assert rule.evaluate(make_ctx(comp_avg=77.0)) is None    # hard interval work
    warn = rule.evaluate(make_ctx(comp_avg=88.0))
    assert warn["severity"] == "warning"
    alert = rule.evaluate(make_ctx(comp_avg=95.0))
    assert alert["severity"] == "alert"
    msg = rule.message(make_ctx(comp_avg=95.0, name="Bob"), alert)
    assert "Bob" in msg and "reducing intensity" in msg
    # empty window (no data) never fires
    ctx = make_ctx()
    ctx.windows[0]["composite"]["avg"] = None
    assert rule.evaluate(ctx) is None


def test_rising_risk_needs_trend_and_forecast() -> None:
    rule = RULE["rising_risk"]
    crossing = [{"horizon": "10m", "pred": 60.0}, {"horizon": "30m", "pred": 94.0}]
    assert rule.evaluate(make_ctx(mid_trend="flat", forecasts=crossing)) is None
    assert rule.evaluate(make_ctx(mid_trend="up", forecasts=None)) is None
    assert rule.evaluate(
        make_ctx(mid_trend="up", forecasts=[{"horizon": "10m", "pred": 60.0}])
    ) is None
    ev = rule.evaluate(make_ctx(mid_trend="up", forecasts=crossing))
    assert ev["pred"] == pytest.approx(94.0) and ev["horizon"] == "30m"
    msg = rule.message(make_ctx(name="Cara"), ev)
    assert "Cara" in msg and "94" in msg and "30m" in msg and "rest" in msg

    # The reported horizon must be the SOONEST crossing, not the lowest
    # prediction. Once fit() clips several horizons at 100 those differ, and
    # picking by prediction names the wrong horizon in the trainer's message.
    saturated = [{"horizon": "10m", "pred": 100.0}, {"horizon": "30m", "pred": 100.0}]
    ev = rule.evaluate(make_ctx(mid_trend="up", forecasts=saturated))
    assert ev["horizon"] == "10m"


def test_data_quality_reports_a_drop_not_an_absolute_level() -> None:
    """The rule tests quality against the device's OWN longest-window baseline,
    not an absolute 0.8.

    The absolute form fired forever on the current hardware (measured link
    quality averaged 0.35 over an 11-minute worn session at 47-73% UDP loss), so
    it carried no information — and `quality` is a ratio against the CONFIGURED
    EXPECTED_INPUT_HZ, so an absolute threshold partly tests that constant
    rather than the link.
    """
    rule = RULE["data_quality"]
    assert rule.severity == "info"

    # healthy and steady -> silent
    assert rule.evaluate(make_ctx(quality=0.95)) is None
    # uniformly POOR but steady -> still silent: nothing has changed, and the
    # absolute figure is already on screen as the quality badge.
    assert rule.evaluate(make_ctx(quality=0.35)) is None
    assert rule.evaluate(make_ctx(quality=0.5)) is None
    # a real degradation against the device's own baseline -> fires
    ev = rule.evaluate(make_ctx(quality=0.5, baseline_quality=0.9))
    assert ev["quality"] == pytest.approx(0.5)
    assert ev["baseline_quality"] == pytest.approx(0.9)
    assert ev["ratio"] == pytest.approx(0.556, abs=1e-3)
    msg = rule.message(make_ctx(quality=0.5, baseline_quality=0.9), ev)
    assert "check sensor fit" in msg and "50%" in msg and "90%" in msg
    # a drop smaller than the ratio does not fire (0.75 >= 0.8 * 0.9 = 0.72)
    assert rule.evaluate(make_ctx(quality=0.75, baseline_quality=0.9)) is None
    # missing data never fires
    assert rule.evaluate(make_ctx(quality=None, baseline_quality=0.9)) is None
    assert rule.evaluate(make_ctx(quality=0.2, baseline_quality=None)) is None


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
    # Recent 2 minutes: composite 95 (above alert threshold 92) at quality 0.5.
    # Preceded by 23 minutes of quiet, GOOD-quality data, which is what the
    # data_quality rule now measures the drop against — a uniformly poor link no
    # longer fires it (see test_data_quality_reports_a_drop_not_an_absolute_level).
    m_now = (await conn.fetchrow("SELECT date_trunc('minute', now()) AS m"))["m"]
    rows = []
    for minute in range(25, 2, -1):
        for i in range(10):
            t = m_now - timedelta(minutes=minute) + timedelta(seconds=6 * i)
            rows.append((t, "30", None, None, None, None, None, 30.0, 0.95))
    for minute in (2, 1):
        for i in range(10):
            t = m_now - timedelta(minutes=minute) + timedelta(seconds=6 * i)
            rows.append((t, "30", None, None, None, None, None, 95.0, 0.5))
    await conn.copy_records_to_table(
        "metrics", records=rows,
        columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                 "composite", "quality"),
    )
    # the 30m baseline window reads metrics_1m, which is materialized-only
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")

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
    assert high["context"]["composite_avg"] == pytest.approx(95.0)
    assert by_rule["data_quality"]["severity"] == "info"

    # newest-first ordering + limit + device filter
    assert body[0]["insight_id"] >= body[1]["insight_id"]
    assert len((await client.get("/api/insights", params={"limit": 1})).json()) == 1
    assert (await client.get("/api/insights", params={"device": "99"})).json() == []


async def test_cooldown_lets_severity_escalate_but_not_regress(scratch_app) -> None:
    """A warning must NOT swallow a genuine alert.

    The cooldown originally keyed on (device_id, rule_id) alone, so a
    `composite_high` WARNING suppressed an ALERT arriving seconds later for the
    full INSIGHT_COOLDOWN_S — the escalation a trainer most needs to see was the
    one case guaranteed to be dropped. Ranking suppresses in one direction only:
    up is allowed through, down and sideways are not.
    """
    settings, conn, pool, client = scratch_app
    await conn.execute("INSERT INTO devices (device_id, display_name) VALUES ('30','A')")
    m_now = (await conn.fetchrow("SELECT date_trunc('minute', now()) AS m"))["m"]

    async def set_composite(value: float) -> None:
        await conn.execute("DELETE FROM metrics WHERE device_id='30'")
        rows = [
            (m_now - timedelta(minutes=minute) + timedelta(seconds=6 * i),
             "30", None, None, None, None, None, value, 1.0)
            for minute in (2, 1) for i in range(10)
        ]
        await conn.copy_records_to_table(
            "metrics", records=rows,
            columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                     "composite", "quality"),
        )

    job = InsightJob(settings, pool)

    await set_composite(88.0)                      # warn 85 <= 88 < alert 92
    assert await job.run_once() == 1
    await set_composite(88.0)
    assert await job.run_once() == 0               # same severity -> suppressed

    await set_composite(95.0)                      # escalation to alert
    assert await job.run_once() == 1, "an alert must not be swallowed by a warning"

    await set_composite(88.0)                      # de-escalation
    assert await job.run_once() == 0, "a warning must not re-fire under an alert"

    rows = (await client.get("/api/insights", params={"device": "30"})).json()
    assert [r["severity"] for r in rows] == ["alert", "warning"]   # newest first
