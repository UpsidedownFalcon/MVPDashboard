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
from api.jobs.predict import MODEL_VERSION, PredictJob, fit
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
    LINEARLY. Derived from the module constants, not written down: 0.1771
    points/min = 7.9677 points per 45-min half-life."""
    expected = (100.0 * math.log(2.0)
                / math.log(P.M3_HI / P.M3_LO) / P.DOSE_HALFLIFE_MIN)
    assert P.M3_DECAY_PTS_PER_MIN == pytest.approx(expected, rel=1e-12)
    assert P.M3_DECAY_PTS_PER_MIN == pytest.approx(0.1770591, abs=1e-6)
    assert P.M3_DECAY_PTS_PER_MIN * P.DOSE_HALFLIFE_MIN == pytest.approx(7.9677, abs=1e-3)


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


def test_dose_equilibrium_matches_the_biomech_recurrence() -> None:
    """Running biomech's own dose recurrence at constant intensity must
    converge to dose_eq = (I/100)^DOSE_EXPONENT / (60*lambda) = 64.92*(I/100)^3.
    This is the closed form the 'load continues' branch extrapolates with."""
    from ingest.biomech import DOSE_EXPONENT, DOSE_HALFLIFE_S

    lam = math.log(2.0) / DOSE_HALFLIFE_S          # per second
    for intensity in (30.0, 60.0, 100.0):
        dose, step = 0.0, 1.0 / 60.0
        for _ in range(int(6 * 3600 * 60)):        # 6 h at 60 Hz
            dose *= 0.5 ** (step / DOSE_HALFLIFE_S)
            dose += (intensity / 100.0) ** DOSE_EXPONENT * (step / 60.0)
        expected = (intensity / 100.0) ** DOSE_EXPONENT / (60.0 * lam)
        assert dose == pytest.approx(expected, rel=0.01)
    assert 1.0 / (60.0 * lam) == pytest.approx(64.92, abs=0.05)


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
# The two-component dose model.
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


def test_stopped_athlete_forecast_decays_rather_than_rising() -> None:
    """An athlete who worked and has now stopped must be projected DOWN, onto
    the decaying dose floor, at every horizon."""
    n_work, n_rest = 20, 10
    m3, acute = [], []
    for i in range(n_work):                       # working: dose climbing
        m3.append(2.0 * i)
        acute.append(60.0)
    for i in range(n_rest):                       # stopped: dose decaying
        m3.append(m3[n_work - 1] - P.M3_DECAY_PTS_PER_MIN * (i + 1))
        acute.append(0.0)
    df = dose_history(m3, acute)
    out = fit(df, HORIZONS)

    preds = [out[h].pred for h in HORIZONS]
    assert preds == sorted(preds, reverse=True), f"forecast must decay, got {preds}"
    assert preds[-1] < preds[0]
    assert preds[0] <= df["composite"].iloc[-1] + 1e-6


def test_rising_session_still_reports_an_honest_stop_floor() -> None:
    """A window ending mid-effort SHOULD project higher under 'load continues' —
    that is not the bug. What the old single-regression model could not express
    is the other scenario: where the composite settles if the athlete stops now.
    That floor is well below the current reading and is what ci_low carries.
    """
    n = 30
    m3 = [1.5 * i for i in range(n)]              # still climbing at the edge
    acute = [65.0] * n
    df = dose_history(m3, acute)
    out = fit(df, HORIZONS)
    last = df["composite"].iloc[-1]

    # OLS on this series extrapolates upward without bound; that is the regime
    # where the old model produced its runaway forecasts.
    x = np.arange(n, dtype=float)
    assert float(np.polyfit(x, df["composite"].to_numpy(float), 1)[0]) > 0.0

    for h in HORIZONS:
        assert out[h].ci_low < last            # "if they stop now" is lower
        assert out[h].ci_low == pytest.approx(
            0.50 * max(0.0, m3[-1] - P.M3_DECAY_PTS_PER_MIN * h.total_seconds() / 60.0),
            abs=1e-9,
        )
        assert out[h].ci_high >= out[h].pred


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
