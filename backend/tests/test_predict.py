"""S2-T04 — forecast stub + job + endpoint tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pandas as pd
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.jobs.predict import MODEL_VERSION, PredictJob, fit
from api.routes.forecasts import router as forecasts_router
from db_utils import connect_admin, create_scratch_db, drop_scratch_db
from migrations.migrate import dsn


def rising_history(n: int = 30, start: float = 20.0, per_min: float = 1.0) -> pd.DataFrame:
    t0 = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    return pd.DataFrame({
        "bucket": [t0 + timedelta(minutes=i) for i in range(n)],
        "composite": [start + per_min * i for i in range(n)],
    })


def test_fit_rising_trend_increases_with_horizon() -> None:
    horizons = [timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=1)]
    out = fit(rising_history(), horizons)
    assert set(out) == set(horizons)
    preds = [out[h].pred for h in horizons]
    # 1/min slope: +10, +30, +60 min beyond t_end=29min from composite 49
    assert preds == sorted(preds)
    assert preds[0] == pytest.approx(59.0, abs=1e-6)
    assert preds[2] == pytest.approx(100.0)          # 109 clipped to 100
    # perfect fit -> zero residual -> CI width ~0 (float noise only)
    widths = [out[h].ci_high - out[h].ci_low for h in horizons]
    assert max(widths) < 1e-6


def test_fit_noisy_ci_widens_and_clips() -> None:
    df = rising_history(n=40, start=90.0, per_min=0.5)
    df.loc[::2, "composite"] += 3.0                  # noise -> residual std > 0
    horizons = [timedelta(minutes=5), timedelta(hours=2)]
    out = fit(df, horizons)
    w5 = out[horizons[0]].ci_high - out[horizons[0]].ci_low
    w2h = out[horizons[1]].ci_high - out[horizons[1]].ci_low
    assert 0.0 < w5 < w2h or out[horizons[1]].ci_high == 100.0   # clipped at scale
    for f in out.values():
        assert 0.0 <= f.ci_low <= f.pred <= f.ci_high <= 100.0


def test_fit_too_few_buckets_raises() -> None:
    with pytest.raises(ValueError):
        fit(rising_history(n=1), [timedelta(minutes=10)])


@pytest.fixture()
async def scratch_app():
    admin = await connect_admin()
    name, settings, conn = await create_scratch_db(admin)
    settings = settings.model_copy(update={
        "future_horizons_raw": "2m,5m,10m",
        "predict_train_window_raw": "2h",
    })
    pool = await asyncpg.create_pool(dsn(settings), min_size=0, max_size=3)
    app = FastAPI()
    app.include_router(forecasts_router)
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


async def test_job_and_endpoint(scratch_app) -> None:
    settings, conn, pool, client = scratch_app
    await conn.execute(
        "INSERT INTO devices (device_id, display_name) VALUES ('30','30'), ('31','31')"
    )
    # no data yet -> 404-shaped empty
    resp = await client.get("/api/forecasts/latest", params={"device": "30"})
    assert resp.status_code == 404

    # 30 minutes of rising composite, minute-aligned, then materialize the cagg
    m_now = (await conn.fetchrow("SELECT date_trunc('minute', now()) AS m"))["m"]
    rows = []
    for i in range(30):
        t = m_now - timedelta(minutes=30 - i)
        rows.append((t, "30", None, None, None, None, None, 20.0 + i, 1.0))
    await conn.copy_records_to_table(
        "metrics", records=rows,
        columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                 "composite", "quality"),
    )
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")

    job = PredictJob(settings, pool)
    assert await job.run_once() == 1     # device 31 skipped (no buckets)
    assert job.runs == 1 and job.last_error is None

    n_rows = await conn.fetchval("SELECT count(*) FROM forecasts WHERE device_id='30'")
    assert n_rows == 3                   # one per horizon, same made_at

    resp = await client.get("/api/forecasts/latest", params={"device": "30"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_version"] == MODEL_VERSION
    horizons = [p["horizon"] for p in body["points"]]
    assert horizons == ["2m", "5m", "10m"]
    preds = [p["pred"] for p in body["points"]]
    assert preds == sorted(preds)        # rising trend -> rising forecast
    for p in body["points"]:
        assert p["ci_low"] <= p["pred"] <= p["ci_high"]

    # unknown device is a 404 regardless of data
    assert (await client.get("/api/forecasts/latest",
                             params={"device": "99"})).status_code == 404
