"""S2-T04 — forecast model + job + endpoint tests.

Two families here. `rising_history` carries NO m3 column, so those tests
exercise the degenerate linear fallback (which is what a device with no dose
channel gets). The `dose_history` tests exercise the real two-component model.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import asyncpg
import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.jobs import predict as P
from api.jobs.predict import (
    MODEL_VERSION, MODEL_VERSION_BOOTSTRAP, PredictJob, fit,
)
from api.routes.forecasts import router as forecasts_router
from db_utils import connect_admin, create_scratch_db, drop_scratch_db
from migrations.migrate import dsn

T0 = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def rising_history(n: int = 30, start: float = 20.0, per_min: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame({
        "bucket": [T0 + timedelta(minutes=i) for i in range(n)],
        "composite": [start + per_min * i for i in range(n)],
    })


def compose(m3: float, acute: float) -> float:
    """biomech SPEC §6.1, verbatim: composite = floor + (100-floor)*acute/100."""
    floor = 0.50 * m3
    return floor + (100.0 - floor) * acute / 100.0


def dose_history(m3_series, acute_series) -> pd.DataFrame:
    """Build a history whose composite is EXACTLY consistent with (m3, acute),
    so the model's recovery of the acute factor can be checked against truth."""
    return pd.DataFrame({
        "bucket": [T0 + timedelta(minutes=i) for i in range(len(m3_series))],
        "composite": [compose(m, a) for m, a in zip(m3_series, acute_series)],
        "m3": list(m3_series),
    })


# =============================================================================
# The algebra the model rests on.
# =============================================================================

def test_dose_constants_track_biomech() -> None:
    """predict.py restates biomech's dose constants rather than importing the
    ingest package. This test is what stops the two drifting: a retune of the
    m3 range or the dose half-life fails HERE rather than silently changing
    every forecast."""
    from ingest import biomech as B

    assert (P.M3_LO, P.M3_HI) == (B.M3_LO, B.M3_HI)
    assert P.DOSE_HALFLIFE_MIN == pytest.approx(B.DOSE_HALFLIFE_S / 60.0)
    assert P.FLOOR_FACTOR == B.FLOOR_FACTOR


def test_m3_rest_decay_rate_is_derived_not_hardcoded() -> None:
    """m3 is a LOG score of an exponentially decaying dose, so at rest it falls
    LINEARLY.

    Derived from the module constants, never written down. The literal below is
    re-measured whenever the m3 range moves: it was 0.1771 pts/min while
    M3_LO was 0.01 with a 45-minute half-life; it is 0.6080 since M3_LO was
    re-anchored to 0.03 and the FAST pool's half-life set to 15 minutes.
    """
    expected = (100.0 * math.log(2.0)
                / math.log(P.M3_HI / P.M3_LO) / P.DOSE_HALFLIFE_MIN)
    assert P.M3_DECAY_PTS_PER_MIN == pytest.approx(expected, rel=1e-12)
    assert P.M3_DECAY_PTS_PER_MIN == pytest.approx(0.6079517, abs=1e-6)
    assert P.M3_DECAY_PTS_PER_MIN * P.DOSE_HALFLIFE_MIN == pytest.approx(9.1193, abs=1e-3)


@pytest.mark.parametrize("m3", [0.0, 1.0, 13.7, 40.0, 55.0, 100.0])
@pytest.mark.parametrize("acute", [0.0, 10.0, 50.0, 99.0, 100.0])
def test_headroom_identity_is_exact(m3: float, acute: float) -> None:
    """The whole model rests on this being an identity, not an approximation:

        1 - composite/100 == (1 - 0.005*m3) * (1 - acute/100)

    i.e. the composite is the noisy-OR of a dose term and an activity term.
    """
    composite = compose(m3, acute)
    assert 1.0 - composite / 100.0 == pytest.approx(
        (1.0 - 0.005 * m3) * (1.0 - acute / 100.0), abs=1e-12
    )


def test_dose_m3_roundtrip() -> None:
    for m3 in (0.5, 7.0, 13.7, 55.0, 99.0):
        assert P._m3_of_dose(P._dose_of_m3(m3)) == pytest.approx(m3, abs=1e-9)
    # m3 == 0 is the CLAMP, not a measurement: dose is only known to be <= M3_LO.
    assert P._dose_of_m3(0.0) == pytest.approx(P.M3_LO)
    assert P._m3_of_dose(P.M3_LO / 10.0) == 0.0


def test_dose_is_split_into_fast_and_slow_pools() -> None:
    """How long a dose lingers must depend on how it was EARNED.

    A single half-life scaled by RECENT activity got this backwards: two
    minutes after a sprint the recent-load memory had faded, so the sprint's
    dose then shed at the easy rate. Each increment is now filed to a pool at
    the moment it is earned, by intensity, and keeps that half-life forever.
    """
    from ingest import biomech as B

    assert B.DOSE_HALFLIFE_S < B.DOSE_HALFLIFE_SLOW_S, "hard work must linger"
    assert B.DOSE_HALFLIFE_S == pytest.approx(P.DOSE_HALFLIFE_MIN * 60.0), (
        "the forecast mirrors the FAST pool, which governs ordinary recovery"
    )
    # the split is sharp: hard running is all slow, a walk is almost all fast
    assert B._slow_fraction(1.0) == pytest.approx(1.0)
    assert B._slow_fraction(0.6) == pytest.approx(0.36)
    assert B._slow_fraction(0.2) == pytest.approx(0.04)
    assert B._slow_fraction(0.0) == 0.0
    assert B._slow_fraction(3.0) == pytest.approx(1.0), "clamped, never above 1"

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


# =============================================================================
# Trend projection. The two-component dose model was RETIRED 2026-08-03: it
# rested on the composite carrying dose as an additive floor, and dose now
# reduces capacity instead, so "if they stop now" is trivially 0.
# =============================================================================

HORIZONS = [timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=1)]


def resting_history(n: int = 30, m3_start: float = 55.0) -> pd.DataFrame:
    """An athlete who has stopped: acute == 0, so composite IS the dose floor,
    and m3 decays at exactly the analytic rest rate."""
    m3 = [max(0.0, m3_start - P.M3_DECAY_PTS_PER_MIN * i) for i in range(n)]
    return dose_history(m3, [0.0] * n)


def test_resting_forecast_follows_the_closed_form_decay() -> None:
    """With acute == 0 the lower scenario is exactly the dose floor,
    0.50 * clip(m3_end - 0.1771*h, 0, 100) -- no regression involved."""
    df = resting_history()
    out = fit(df, HORIZONS)
    m3_end = df["m3"].iloc[-1]
    for h in HORIZONS:
        h_min = h.total_seconds() / 60.0
        expected_m3 = max(0.0, m3_end - P.M3_DECAY_PTS_PER_MIN * h_min)
        assert out[h].ci_low == pytest.approx(0.50 * expected_m3, abs=1e-9)
    # acute is identically 0, so the central forecast sits on that same floor:
    # the observed source term is ~0 and the recovered acute factor is exactly 1.
    for h in HORIZONS:
        assert out[h].pred == pytest.approx(out[h].ci_low, abs=0.5)


def test_band_never_collapses_while_the_session_had_load() -> None:
    """If the athlete is resting NOW but worked earlier, 'if load resumes' is
    still a real scenario -- the upper branch is taken over the whole training
    window so the band does not render as false precision."""
    m3 = [2.0 * i for i in range(20)] + \
         [38.0 - P.M3_DECAY_PTS_PER_MIN * (i + 1) for i in range(10)]
    acute = [70.0] * 20 + [0.0] * 10
    out = fit(dose_history(m3, acute), HORIZONS)
    for h in HORIZONS:
        assert out[h].ci_high - out[h].ci_low > 1.0


def test_scenario_band_is_ordered_and_bounded() -> None:
    rng = np.random.default_rng(7)
    for _ in range(50):
        n = int(rng.integers(5, 40))
        m3 = np.clip(np.cumsum(rng.normal(0.5, 2.0, n)), 0.0, 100.0)
        acute = np.clip(rng.uniform(0.0, 100.0, n), 0.0, 100.0)
        out = fit(dose_history(m3, acute), HORIZONS)
        for f in out.values():
            assert 0.0 <= f.ci_low <= f.pred <= f.ci_high <= 100.0


def test_m3_decay_clamps_at_zero_over_long_horizons() -> None:
    """A naive straight line predicts a NEGATIVE dose floor: 3.0 points of m3
    decaying at 0.1771/min crosses zero after 17 min, well inside a 1 h horizon."""
    out = fit(resting_history(m3_start=3.0), [timedelta(hours=1), timedelta(hours=6)])
    for f in out.values():
        assert f.ci_low >= 0.0
        assert f.ci_low == pytest.approx(0.0, abs=1e-9)


def test_missing_m3_falls_back_rather_than_assuming_zero_dose() -> None:
    """An absent dose channel must NOT be read as 'this athlete has no
    accumulated load' -- that would silently zero the floor term."""
    n = 30
    all_null = pd.DataFrame({
        "bucket": [T0 + timedelta(minutes=i) for i in range(n)],
        "composite": [40.0] * n,
        "m3": [None] * n,
    })
    no_col = pd.DataFrame({
        "bucket": [T0 + timedelta(minutes=i) for i in range(n)],
        "composite": [40.0] * n,
    })
    a = fit(all_null, HORIZONS)
    b = fit(no_col, HORIZONS)
    for h in HORIZONS:
        assert a[h].pred == pytest.approx(b[h].pred)
        assert a[h].pred == pytest.approx(40.0, abs=1e-6)   # flat series, flat fit


def test_bucket_factorisation_error_is_bounded() -> None:
    """The identity is exact per tick. Per BUCKET both factors are averaged
    separately, so the covariance term is dropped. Bound the cost of that.

    MEASURED: worst case over these regimes is 0.63 composite points (mostly
    from a bucket that spans a hard onset, where m3 drifts while acute swings
    over its full range). Recorded rather than assumed -- if a retune makes m3
    move faster within a minute, this is the test that says so.
    """
    rng = np.random.default_rng(11)
    worst = 0.0
    for m3_drift in (0.0, P.M3_DECAY_PTS_PER_MIN, 2.0):      # pts across one bucket
        for acute_lo, acute_hi in ((0, 5), (20, 60), (0, 100), (90, 100)):
            for m3_base in (0.0, 20.0, 55.0, 90.0):
                m3 = np.linspace(m3_base, m3_base + m3_drift, 3600)
                acute = rng.uniform(acute_lo, acute_hi, 3600)
                true_h = np.mean([1.0 - compose(m, a) / 100.0
                                  for m, a in zip(m3, acute)])
                factorised = (1.0 - 0.005 * m3.mean()) * (1.0 - acute.mean() / 100.0)
                worst = max(worst, abs(true_h - factorised) * 100.0)
    assert worst < 1.5, f"factorisation error {worst:.3f} composite points"


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

    # target_time is anchored on the LAST OBSERVED BUCKET, not on made_at: the
    # aggregate runs at least a minute behind (end_offset = 1 minute plus
    # refresh lag), so labelling these made_at + h overstated every horizon.
    t_end = await conn.fetchval(
        "SELECT max(bucket) FROM metrics_1m WHERE device_id='30'")
    row = await conn.fetchrow(
        """SELECT made_at, target_time FROM forecasts
           WHERE device_id='30' AND horizon = interval '10 minutes'""")
    assert row["target_time"] == t_end + timedelta(minutes=10)
    assert row["target_time"] < row["made_at"] + timedelta(minutes=10)


async def _seed_raw(conn, device: str, minutes: float, hz: int = 60,
                    start: float = 20.0, rise: float = 10.0) -> None:
    """`minutes` of raw 60Hz-ish metrics ending at now(), rising linearly.

    Dense on purpose: the bootstrap query drops any bucket holding less than
    half a bucket's worth of samples, which is what stops a trailing partial
    bucket being averaged as though it were a full one.
    """
    t_now = (await conn.fetchrow("SELECT now() AS n"))["n"]
    n = int(minutes * 60 * hz)
    rows = [
        (t_now - timedelta(seconds=i / hz), device,
         None, None, None, None, None, start + rise * (n - i) / n, 1.0)
        for i in range(n)
    ]
    await conn.copy_records_to_table(
        "metrics", records=rows,
        columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                 "composite", "quality"),
    )


async def test_bootstrap_forecasts_minutes_after_first_data(scratch_app) -> None:
    """The latency fix, measured.

    The steady model trains on `metrics_1m` and needs 10 one-minute buckets, so
    it cannot speak until ~10 min of streaming plus 1-2 min of materialization
    lag. This asserts BOTH halves: the aggregate genuinely cannot serve this
    device yet, and a forecast is produced anyway.
    """
    settings, conn, pool, client = scratch_app
    await conn.execute("INSERT INTO devices (device_id, display_name) VALUES ('30','30')")
    await _seed_raw(conn, "30", minutes=3)
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")

    cagg_buckets = await conn.fetchval(
        "SELECT count(*) FROM metrics_1m WHERE device_id='30'")
    assert cagg_buckets < P.MIN_BUCKETS, (
        "precondition: the steady path must be unable to fire on 3 minutes of data"
    )

    job = PredictJob(settings, pool)
    assert await job.run_once() == 1, "the bootstrap path must forecast at ~3 min"
    assert job.last_error is None

    body = (await client.get("/api/forecasts/latest", params={"device": "30"})).json()
    assert body["model_version"] == MODEL_VERSION_BOOTSTRAP
    assert body["provisional"] is True, "early projections must be marked"
    assert body["points"], "a provisional forecast still carries points"
    for p in body["points"]:
        assert 0.0 <= p["pred"] <= 100.0
        assert p["ci_low"] <= p["pred"] <= p["ci_high"]


async def test_bootstrap_horizons_are_capped_by_observed_span(scratch_app) -> None:
    """Projecting an hour from three minutes of data is a straight line with an
    uninformative error bar, not a forecast. Horizons are bounded by the data
    actually observed, which is why the earliest projections are short ones."""
    settings, conn, pool, client = scratch_app
    await conn.execute("INSERT INTO devices (device_id, display_name) VALUES ('30','30')")
    await _seed_raw(conn, "30", minutes=3)

    assert await PredictJob(settings, pool).run_once() == 1
    body = (await client.get("/api/forecasts/latest", params={"device": "30"})).json()
    horizons = {p["horizon"] for p in body["points"]}
    # ~3 min of data -> span ~2.75 min: 1m and 2m are supported, 5m is not
    assert "1m" in horizons
    assert "5m" not in horizons, "a horizon longer than the data must not publish"
    assert horizons <= {"1m", "2m"}
    # and the unit rule itself, independent of the data above
    span = timedelta(minutes=2, seconds=30)
    assert P._capped_horizons(
        [timedelta(minutes=1), timedelta(minutes=5), timedelta(hours=1)], span
    ) == [timedelta(minutes=1)]


async def test_established_device_hands_over_to_the_aggregate(scratch_app) -> None:
    """Bootstrapping is a warm-up, not a replacement: once `metrics_1m` can
    serve the device, the steady model and the CONFIGURED horizons return, so
    steady-state behaviour is exactly what it was before."""
    settings, conn, pool, client = scratch_app
    await conn.execute("INSERT INTO devices (device_id, display_name) VALUES ('30','30')")
    m_now = (await conn.fetchrow("SELECT date_trunc('minute', now()) AS m"))["m"]
    rows = [
        (m_now - timedelta(minutes=20 - minute) + timedelta(seconds=6 * i),
         "30", None, None, None, None, None, 20.0 + minute, 1.0)
        for minute in range(20) for i in range(10)
    ]
    await conn.copy_records_to_table(
        "metrics", records=rows,
        columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                 "composite", "quality"),
    )
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")
    assert await conn.fetchval(
        "SELECT count(*) FROM metrics_1m WHERE device_id='30'") >= P.MIN_BUCKETS

    assert await PredictJob(settings, pool).run_once() == 1
    body = (await client.get("/api/forecasts/latest", params={"device": "30"})).json()
    assert body["model_version"] == MODEL_VERSION
    assert body["provisional"] is False
    assert {p["horizon"] for p in body["points"]} == {"2m", "5m", "10m"}, (
        "the configured FUTURE_HORIZONS, not the bootstrap ladder"
    )


async def test_stale_device_is_not_forecast(scratch_app) -> None:
    """Regression test for the observed live failure: a device with no sensors
    and no recent data kept forecasting 35 -> 46.7 -> 64.3 with a widening band
    every PREDICT_INTERVAL_S. MIN_BUCKETS is a raw count over the whole training
    window, so it enforces neither recency nor contiguity -- a device that
    streamed 30 minutes and then went silent still passes it.

    The gate is SESSION_GAP_S because that is the model's own session boundary:
    past it biomech resets `dose` to 0 on reconnect (SPEC §7.4), so any dose
    trajectory projected across the gap describes a state that will never occur.
    """
    settings, conn, pool, client = scratch_app
    await conn.execute("INSERT INTO devices (device_id, display_name) VALUES ('30','30')")

    m_now = (await conn.fetchrow("SELECT date_trunc('minute', now()) AS m"))["m"]
    stale_by = timedelta(seconds=settings.session_gap_s) + timedelta(minutes=5)
    rows = []
    for i in range(30):                       # 30 rising buckets, all stale
        t = m_now - stale_by - timedelta(minutes=30 - i)
        rows.append((t, "30", None, None, None, None, 20.0 + i, 20.0 + i, 1.0))
    await conn.copy_records_to_table(
        "metrics", records=rows,
        columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                 "composite", "quality"),
    )
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")

    job = PredictJob(settings, pool)
    assert await job.run_once() == 0, "a stale device must not be forecast"
    assert await conn.fetchval("SELECT count(*) FROM forecasts") == 0
    assert job.last_error is None            # skipped cleanly, not by exception

    # the same rows, fresh, DO produce a forecast -- proving the gate is what
    # suppressed it rather than the data being unusable
    await conn.execute("DELETE FROM metrics WHERE device_id='30'")
    fresh = [(t + stale_by, d, a, b, c, e, f, g, q)
             for (t, d, a, b, c, e, f, g, q) in rows]
    await conn.copy_records_to_table(
        "metrics", records=fresh,
        columns=("time", "device_id", "m1", "m2", "m3", "m4", "m5",
                 "composite", "quality"),
    )
    await conn.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")
    assert await job.run_once() == 1
