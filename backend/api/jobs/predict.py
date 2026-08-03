"""Forecast job (S2-T04): composite-only predictions per configured horizon.

`fit()` is the STABLE INTERFACE (BACKEND_SCHEMA.md §5) — its signature does not
move; the model lives entirely inside it.

THE MODEL: the composite is EXACTLY separable, which is what this file exploits.
With `floor = 0.50*m3` (biomech SPEC §6.1):

    composite = floor + (100 - floor) * acute/100

so, defining headroom `H = 1 - composite/100`,

    H = (1 - 0.005*m3) * (1 - acute/100)                    <- exact, not a fit

i.e. the composite is the noisy-OR of a SLOW accumulated-dose term and a FAST
activity term, and log H is additive in the two. That matters because the two
halves have completely different forecastability:

  * The dose term is CLOSED FORM. `dose` obeys d(dose)/dt = -lambda*dose + S
    with a 45-minute half-life, so at rest m3 falls LINEARLY at
    M3_DECAY_PTS_PER_MIN and under continued load it approaches S/lambda
    exponentially. No regression is needed or wanted.
  * The acute term is NOT forecastable beyond persistence. It is driven by what
    the athlete chooses to do next.

Fitting one straight line to their SUM -- which is what `linreg-stub-1` did --
therefore fits a mixture of a knowable and an unknowable quantity. Worse, OLS
over a session fits the RISING LIMB, so the post-session decay tail cannot pull
the slope down: a device that had stopped streaming was observed forecasting
35 -> 46.7 -> 64.3 with widening bands off a stale trend.

So the band this model reports is a SCENARIO BAND, not a confidence interval:
  ci_low   "if they stop now"          -> acute -> 0, dose decays. The composite
                                          settles onto the dose floor 0.50*m3.
  pred     "if recent load continues"  -> dose follows the observed source term,
                                          acute held at its recent mean.
  ci_high  "if the recent hard part continues" -> acute held at its recent p90.

They reuse the `ci_low`/`ci_high` columns because those are just numbers, but
they are two counterfactuals rather than a statement about estimator error --
the frontend switches its label on `model_version` so no 95%-probability claim
is made about them (biomech SPEC §2 forbids it).
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg
import numpy as np
import pandas as pd

from common.config import Settings

log = logging.getLogger("api.jobs.predict")

MODEL_VERSION = "trend-ols-1"
MIN_BUCKETS = 10

# --- dose-model constants -----------------------------------------------------
# Deliberately restated here rather than imported from ingest.biomech: the api
# process does not otherwise depend on the ingest package, and this file must
# not be the thing that couples them. test_predict.py asserts every one of these
# against the biomech module, so a retune there fails the suite rather than
# silently desynchronising the forecast.
M3_LO, M3_HI = 0.03, 60.0            # biomech M3_LO / M3_HI (dose-minutes)
DOSE_HALFLIFE_MIN = 10.0             # biomech DOSE_HALFLIFE_S / 60 (REST value)
FLOOR_FACTOR = 0.50                  # biomech FLOOR_FACTOR

LN_M3_SPAN = math.log(M3_HI / M3_LO)                        # ln(6000) = 8.699515
LAMBDA_PER_MIN = math.log(2.0) / DOSE_HALFLIFE_MIN          # 0.0154033 / min
# m3 is a LOG score of an exponentially decaying quantity, so at rest it falls
# LINEARLY: 100*ln2/ln(6000) = 7.9677 points per half-life = 0.1771 pts/min.
M3_DECAY_PTS_PER_MIN = 100.0 * math.log(2.0) / LN_M3_SPAN / DOSE_HALFLIFE_MIN

# Buckets at the end of the training window used to estimate "recent" load.
RECENT_BUCKETS = 10
ACUTE_HIGH_PCT = 10.0    # 10th pct of the headroom factor == 90th pct of acute


@dataclass
class Forecast:
    pred: float
    ci_low: float
    ci_high: float


def _dose_of_m3(m3: float) -> float:
    """Invert biomech's log_score. m3 == 0 is the CLAMP, not a measurement, so
    this returns the clamp boundary -- all we honestly know is dose <= M3_LO."""
    return M3_LO * math.exp(max(m3, 0.0) / 100.0 * LN_M3_SPAN)


def _m3_of_dose(dose: float) -> float:
    if dose <= M3_LO:
        return 0.0
    return min(100.0, 100.0 * math.log(dose / M3_LO) / LN_M3_SPAN)


def _compose(m3: float, acute_factor: float) -> float:
    """The exact identity, run forwards: composite = 100*(1 - D*A)."""
    return float(np.clip(100.0 * (1.0 - (1.0 - 0.005 * m3) * acute_factor), 0.0, 100.0))


def _fit_linear_fallback(x, y, horizons) -> dict[timedelta, Forecast]:
    """Degenerate branch: no usable m3, so the decomposition is unavailable and
    there is nothing to do but extrapolate the composite itself. Kept honest
    with a CORRECT OLS prediction interval -- the old one was
    `1.96*sigma*sqrt(1 + h/span)`, whose leverage term is linear in h where the
    truth is quadratic, and which has no dependence on n at all (so it never
    narrowed as data accumulated). At n = MIN_BUCKETS that was ~38% too narrow
    before autocorrelation is even considered.
    """
    n = len(x)
    slope, intercept = np.polyfit(x, y, deg=1)
    resid = y - (intercept + slope * x)
    # ddof=2 is the correct unbiased sigma-hat for a 2-parameter fit.
    sigma = float(resid.std(ddof=2)) if n > 2 else 0.0
    xbar = float(x.mean())
    sxx = float(((x - xbar) ** 2).sum()) or 1.0
    t_end = float(x[-1])
    # Student t, not the normal 1.96: at n=10, t(.975, 8) = 2.306 is 18% larger.
    tq = _t_quantile_975(max(n - 2, 1))

    out: dict[timedelta, Forecast] = {}
    for h in horizons:
        h_min = h.total_seconds() / 60.0
        x_star = t_end + h_min
        pred = float(np.clip(intercept + slope * x_star, 0.0, 100.0))
        se = sigma * math.sqrt(1.0 + 1.0 / n + (x_star - xbar) ** 2 / sxx)
        ci = tq * se
        out[h] = Forecast(
            pred=pred,
            ci_low=float(np.clip(pred - ci, 0.0, 100.0)),
            ci_high=float(np.clip(pred + ci, 0.0, 100.0)),
        )
    return out


# t(.975, df) for small df; beyond the table the normal limit is close enough
# (t(.975, 30) = 2.042 vs 1.960, and df is >= n-2 with n >= MIN_BUCKETS = 10).
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
         8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
         14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
         20: 2.086, 25: 2.060, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980}


def _t_quantile_975(df: int) -> float:
    if df in _T975:
        return _T975[df]
    keys = [k for k in sorted(_T975) if k <= df]
    return _T975[keys[-1]] if keys else 12.706


def fit(history: pd.DataFrame, horizons: list[timedelta]) -> dict[timedelta, Forecast]:
    """history: metrics_1m rows (`bucket`, `composite`, `m3`, …) over
    PREDICT_TRAIN_WINDOW. Returns per-horizon Forecast(pred, ci_low, ci_high),
    all 0-100 on the composite scale. See the module docstring for the model.
    """
    df = history.dropna(subset=["composite"]).sort_values("bucket")
    if len(df) < 2:
        raise ValueError("need at least 2 buckets to fit")
    t0 = df["bucket"].iloc[0]
    x = ((df["bucket"] - t0).dt.total_seconds() / 60.0).to_numpy(dtype=float)
    y = df["composite"].to_numpy(dtype=float)

    # 🚩 The dose decomposition was RETIRED on 2026-08-03 with the composite
    # rebuild. It rested on the identity
    #     1 - composite/100 == (1 - 0.005*m3) * (1 - acute/100)
    # which held only while `m3` entered the composite as an additive FLOOR.
    # It no longer does: dose now reduces CAPACITY instead, so the composite is
    # `acute` alone and goes to 0 at rest whatever the accumulated load. Under
    # that model "if they stop now" is trivially 0 and the scenario band carries
    # no information, so continuing to publish it would be false precision.
    #
    # What replaces it is a plain trend projection with a CORRECT OLS prediction
    # interval. Less informative than the scenario band, but true.
    #
    # The better successor -- projecting CAPACITY (knowable: dose decays at a
    # known, now intensity-dependent rate) against persisted demand (unknowable)
    # -- is the natural next model and is recorded as an open item rather than
    # guessed at here.
    return _fit_linear_fallback(x, y, horizons)


class PredictJob:
    """Every PREDICT_INTERVAL_S: per device with ≥10 buckets in
    PREDICT_TRAIN_WINDOW, fit → one forecasts row per horizon (same made_at).
    One bad device never kills the loop."""

    def __init__(self, settings: Settings, pool: asyncpg.Pool) -> None:
        self._settings = settings
        self._pool = pool
        self._task: asyncio.Task | None = None
        self.last_run: datetime | None = None
        self.last_error: str | None = None
        self.runs = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="predict-job")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._settings.predict_interval_s)
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — job must survive anything
                self.last_error = str(exc)
                log.exception("predict run failed")

    async def run_once(self) -> int:
        """One prediction sweep; returns number of devices forecast."""
        made_at = datetime.now(tz=timezone.utc)
        train_window = self._settings.predict_train_window
        horizons = self._settings.future_horizons
        # SESSION_GAP_S is the model's own session boundary: past it,
        # biomech.compute() resets `dose` to 0 on reconnect (SPEC §7.4), so any
        # dose trajectory projected across that gap describes a state the model
        # will never actually produce.
        max_staleness = timedelta(seconds=self._settings.session_gap_s)
        rows = await self._pool.fetch(
            """SELECT device_id, bucket, composite, m3
               FROM metrics_1m
               WHERE bucket >= $1
               ORDER BY device_id, bucket""",
            made_at - train_window,
        )
        by_device: dict[str, list] = {}
        for r in rows:
            by_device.setdefault(r["device_id"], []).append(r)

        forecast_count = 0
        for device_id, dev_rows in by_device.items():
            if len(dev_rows) < MIN_BUCKETS:
                log.info("skip %s: only %d/%d buckets in train window",
                         device_id, len(dev_rows), MIN_BUCKETS)
                continue
            # MIN_BUCKETS is a raw count over the whole train window: it says
            # nothing about RECENCY, so a device that streamed for 20 minutes
            # and then went offline an hour ago still passes it. Without this
            # gate such a device kept producing a fresh rising forecast every
            # PREDICT_INTERVAL_S off a dead trend.
            t_end = dev_rows[-1]["bucket"]
            if made_at - t_end > max_staleness:
                log.info("skip %s: newest bucket is %s old (> %s)",
                         device_id, made_at - t_end, max_staleness)
                continue
            try:
                history = pd.DataFrame(
                    {"bucket": [r["bucket"] for r in dev_rows],
                     "composite": [r["composite"] for r in dev_rows],
                     "m3": [r["m3"] for r in dev_rows]}
                )
                forecasts = fit(history, horizons)
                await self._pool.executemany(
                    """INSERT INTO forecasts (made_at, device_id, horizon, target_time,
                                              composite_pred, ci_low, ci_high, model_version)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                       ON CONFLICT DO NOTHING""",
                    [
                        # target_time is anchored on the LAST OBSERVED BUCKET,
                        # not on made_at. fit() extrapolates from the end of the
                        # data, and metrics_1m runs at least a minute behind
                        # (end_offset = 1 minute plus refresh lag), so labelling
                        # these `made_at + h` overstated every horizon by the
                        # size of that lag.
                        (made_at, device_id, h, t_end + h,
                         f.pred, f.ci_low, f.ci_high, MODEL_VERSION)
                        for h, f in forecasts.items()
                    ],
                )
                forecast_count += 1
            except Exception as exc:  # noqa: BLE001 — isolate per device
                self.last_error = f"{device_id}: {exc}"
                log.exception("predict failed for device %s", device_id)
        self.last_run = made_at
        self.runs += 1
        return forecast_count
