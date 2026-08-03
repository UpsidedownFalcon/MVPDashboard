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


def make_windows(*, short_m=None, long_m=None, short_c=50.0, long_c=50.0,
                 sd=None, quality=1.0, baseline_quality=None, coverage=1.0,
                 mid_trend="flat"):
    """Three windows with independent short/long values, so within-athlete
    deviation rules can be driven directly."""
    short_m = short_m if short_m is not None else [None] * 5
    long_m = long_m if long_m is not None else [None] * 5
    sd = sd if sd is not None else [10.0] * 5
    if baseline_quality is None:
        baseline_quality = quality

    def w(label, m, c, q, trend):
        return {"window": label, "from": "2026-08-03T12:00:00.000Z",
                "m": list(m), "sd": list(sd),
                "composite": {"avg": c, "min": c, "max": c, "sd": 10.0},
                "quality": q, "coverage": coverage, "trend": trend}

    return [w("5m", short_m, short_c, quality, "flat"),
            w("30m", short_m, short_c, quality, mid_trend),
            w("2h", long_m, long_c, baseline_quality, "flat")]


def ctx_from(windows, forecasts=None, name="Alice") -> Ctx:
    return Ctx(device_id="30", display_name=name, windows=windows,
               forecasts=forecasts, settings=Settings(_env_file=None))


def make_ctx(comp_avg=50.0, quality=1.0, mid_trend="flat",
             forecasts=None, name="Alice", baseline_quality=None) -> Ctx:
    """`baseline_quality` sets the LONGEST window's quality independently, so
    the relative data_quality rule can be exercised. Default: same as `quality`,
    i.e. a device whose link has been uniformly this good (or bad) throughout."""
    if baseline_quality is None:
        baseline_quality = quality

    def window(label, avg, trend, q):
        return {"window": label, "from": "2026-08-02T12:00:00.000Z",
                "m": [None] * 5, "sd": [None] * 5,
                "composite": {"avg": avg, "min": avg, "max": avg, "sd": None},
                "quality": q, "coverage": 1.0, "trend": trend}
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


# --- the holistic / retune-immune layer --------------------------------------

def test_z_is_invariant_to_a_metric_rescale() -> None:
    """THE property the whole rule catalogue rests on.

    Every athlete-facing rule fires on `MetricView.z` — the deviation of the
    recent window from the athlete's own baseline, in units of their own spread.
    That is EXACTLY invariant under any affine rescale of the metric, which is
    what a change to a biomech normalisation bound (`M1_LO`, `M3_HI`, the acute
    curve, …) produces: `lo` shifts every value by a constant, `hi/lo` scales
    them all by a common factor, and both cancel in (now - baseline)/sd.

    So retuning the model changes the numbers an insight REPORTS without
    changing whether it fires. Absolute thresholds have no such property.
    """
    base = make_windows(short_m=[60.0] * 5, long_m=[30.0] * 5, sd=[10.0] * 5,
                        short_c=60.0, long_c=30.0)
    z_before = {k: v.z for k, v in ctx_from(base).metrics.items()}

    for gain, offset in ((2.0, 0.0), (0.5, 0.0), (1.0, 25.0), (3.0, -10.0)):
        scaled = make_windows(
            short_m=[60.0 * gain + offset] * 5, long_m=[30.0 * gain + offset] * 5,
            sd=[10.0 * gain] * 5,
            short_c=60.0 * gain + offset, long_c=30.0 * gain + offset,
        )
        # composite sd is fixed at 10.0 by make_windows, so check the primitives,
        # which carry the rescaled sd.
        z_after = {k: v.z for k, v in ctx_from(scaled).metrics.items()}
        for key in ("m1", "m2", "m3", "m4", "m5"):
            assert z_after[key] == pytest.approx(z_before[key], rel=1e-9), (
                f"{key} z changed under rescale gain={gain} offset={offset}"
            )


def test_metrics_view_spans_every_primitive_and_window() -> None:
    """An insight must be able to see the whole picture, not one number."""
    ctx = ctx_from(make_windows(short_m=[10.0, 20.0, 30.0, 40.0, 50.0],
                                long_m=[1.0, 2.0, 3.0, 4.0, 5.0]))
    assert set(ctx.metrics) == {"m1", "m2", "m3", "m4", "m5", "composite"}
    assert ctx.metrics["m3"].now == 30.0 and ctx.metrics["m3"].baseline == 3.0
    assert ctx.metrics["m4"].unvalidated and ctx.metrics["m5"].unvalidated
    assert not ctx.metrics["m1"].unvalidated


def test_load_spike_fires_on_within_athlete_deviation() -> None:
    rule = RULE["load_spike"]
    # inside the athlete's own spread -> silent, however large the absolute value
    assert rule.evaluate(ctx_from(make_windows(short_c=90.0, long_c=85.0))) is None
    # 3 sd above their own baseline -> alert
    ev = rule.evaluate(ctx_from(make_windows(short_c=60.0, long_c=30.0)))
    assert ev is not None and ev["severity"] == "alert"
    assert ev["z"] == pytest.approx(3.0)
    # 2 sd -> warning, not alert
    ev = rule.evaluate(ctx_from(make_windows(short_c=50.0, long_c=30.0)))
    assert ev["severity"] == "warning"


def test_accumulated_load_needs_both_high_and_rising() -> None:
    rule = RULE["accumulated_load"]
    high = dict(short_m=[0, 0, 60.0, 0, 0], long_m=[0, 0, 30.0, 0, 0])
    assert rule.evaluate(ctx_from(make_windows(**high, mid_trend="flat"))) is None
    ev = rule.evaluate(ctx_from(make_windows(**high, mid_trend="up")))
    assert ev is not None and ev["metric"] == "m3"
    assert ev["severity"] == "alert"


def test_residual_load_reads_the_stop_now_branch() -> None:
    """Fires on ci_low — where risk settles if the athlete stops NOW. That is
    decay of load already taken, not a prediction about behaviour."""
    rule = RULE["residual_load"]
    w = make_windows()
    quiet = [{"horizon": "1h", "pred": 95.0, "ci_low": 40.0, "ci_high": 99.0}]
    assert rule.evaluate(ctx_from(w, forecasts=quiet)) is None, (
        "a high central projection must NOT fire this rule — only the floor does"
    )
    loaded = [{"horizon": "10m", "pred": 99.0, "ci_low": 50.0, "ci_high": 99.0},
              {"horizon": "1h", "pred": 99.0, "ci_low": 88.0, "ci_high": 99.0}]
    ev = rule.evaluate(ctx_from(w, forecasts=loaded))
    assert ev["settles_at"] == pytest.approx(88.0) and ev["horizon"] == "1h"
    assert "stop" in rule.message(ctx_from(w, forecasts=loaded), ev).lower()


def test_impact_and_movement_rules_stay_info_and_marked() -> None:
    dev = dict(short_m=[60.0, 60.0, 0.0, 60.0, 60.0],
               long_m=[30.0, 30.0, 0.0, 30.0, 30.0])
    ctx = ctx_from(make_windows(**dev))

    ev = RULE["impact_deviation"].evaluate(ctx)
    assert ev["severity"] == "info" and ev["metric"] in ("m1", "m2")
    assert not ev.get("unvalidated")

    ev = RULE["movement_quality"].evaluate(ctx)
    assert ev["severity"] == "info" and ev["metric"] in ("m4", "m5")
    assert ev["unvalidated"] is True, "m4/m5 have no real-data validation"
    text = RULE["movement_quality"].rationale(ctx, ev)
    assert "no real-world validation" in text
    for banned in ("left leg", "right leg", "weaker"):
        assert banned not in text.lower(), "no directional L/R claim (SPEC §5.5)"


def test_athlete_rules_are_suppressed_on_mismatched_quality() -> None:
    """5% packet loss moves m1 -49% and m2 +34%, so comparing windows measured
    at different link quality compares artefacts, not the athlete."""
    spike = dict(short_c=60.0, long_c=30.0,
                 short_m=[60.0] * 5, long_m=[30.0] * 5)
    assert RULE["load_spike"].evaluate(ctx_from(make_windows(**spike))) is not None
    mismatched = ctx_from(make_windows(**spike, quality=0.3, baseline_quality=0.95))
    for rid in ("load_spike", "accumulated_load", "impact_deviation",
                "movement_quality"):
        assert RULE[rid].evaluate(mismatched) is None, f"{rid} fired on bad data"


def test_low_coverage_suppresses_athlete_rules() -> None:
    spike = dict(short_c=60.0, long_c=30.0)
    assert RULE["load_spike"].evaluate(
        ctx_from(make_windows(**spike, coverage=0.5))) is not None
    assert RULE["load_spike"].evaluate(
        ctx_from(make_windows(**spike, coverage=0.01))) is None


def test_every_rule_supplies_action_and_rationale_with_evidence() -> None:
    """UIUX §4 renders `action` as the headline and `rationale` beneath it, and
    SPEC §2 forbids an individual flag without the evidence that fired it."""
    for rule in RULES:
        assert rule.action is not None, f"{rule.rule_id} has no action"
        assert rule.rationale is not None, f"{rule.rule_id} has no rationale"
        assert rule.severity in ("info", "warning", "alert")


def test_firing_depends_on_dispersion_not_on_a_bare_ratio() -> None:
    """The behavioural difference from an acute:chronic ratio.

    ACWR fires on short/long alone. It is mathematically coupled (Lolli 2019)
    and has no evidence supporting its use for load management (Impellizzeri
    2020), and SPEC §6.3 excludes it. These rules instead ask whether the change
    is large FOR THIS ATHLETE, so the identical 2x ratio fires for a metronomic
    athlete and stays silent for a variable one — which a ratio cannot express.
    """
    steady = ctx_from(make_windows(short_c=60.0, long_c=30.0, sd=[3.0] * 5))
    variable = ctx_from(make_windows(short_c=60.0, long_c=30.0, sd=[40.0] * 5))
    # identical short/long ratio in both
    assert steady.metrics["m1"].now == variable.metrics["m1"].now

    m3_steady = dict(short_m=[0, 0, 60.0, 0, 0], long_m=[0, 0, 30.0, 0, 0],
                     sd=[3.0] * 5, mid_trend="up")
    m3_variable = dict(m3_steady, sd=[40.0] * 5)
    assert RULE["accumulated_load"].evaluate(
        ctx_from(make_windows(**m3_steady))) is not None
    assert RULE["accumulated_load"].evaluate(
        ctx_from(make_windows(**m3_variable))) is None


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
