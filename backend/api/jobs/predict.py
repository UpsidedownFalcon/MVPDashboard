"""Forecast job (S2-T04): composite-only predictions per configured horizon.

`fit()` is the STABLE INTERFACE (BACKEND_SCHEMA.md §5) — the real model from a
later dedicated session replaces only that function. Tonight's stub: numpy
linear fit on (minutes_from_start, composite avg from metrics_1m), CI from
residual std × sqrt(1 + h/train_span).
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

MODEL_VERSION = "linreg-stub-1"
MIN_BUCKETS = 10


@dataclass
class Forecast:
    pred: float
    ci_low: float
    ci_high: float


def fit(history: pd.DataFrame, horizons: list[timedelta]) -> dict[timedelta, Forecast]:
    """history: metrics_1m rows (`bucket`, `composite`, …) over
    PREDICT_TRAIN_WINDOW. Returns per-horizon Forecast(pred, ci_low, ci_high).
    Values are 0-100 (composite scale); predictions are clipped to that range.
    """
    df = history.dropna(subset=["composite"]).sort_values("bucket")
    if len(df) < 2:
        raise ValueError("need at least 2 buckets to fit")
    t0 = df["bucket"].iloc[0]
    x = ((df["bucket"] - t0).dt.total_seconds() / 60.0).to_numpy(dtype=float)
    y = df["composite"].to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, deg=1)
    resid = y - (intercept + slope * x)
    resid_std = float(resid.std(ddof=2)) if len(df) > 2 else 0.0
    train_span = float(x[-1] - x[0]) or 1.0
    t_end = float(x[-1])

    out: dict[timedelta, Forecast] = {}
    for h in horizons:
        h_min = h.total_seconds() / 60.0
        pred = float(np.clip(intercept + slope * (t_end + h_min), 0.0, 100.0))
        ci = 1.96 * resid_std * math.sqrt(1.0 + h_min / train_span)
        out[h] = Forecast(
            pred=pred,
            ci_low=float(np.clip(pred - ci, 0.0, 100.0)),
            ci_high=float(np.clip(pred + ci, 0.0, 100.0)),
        )
    return out


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
        rows = await self._pool.fetch(
            """SELECT device_id, bucket, composite
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
            try:
                history = pd.DataFrame(
                    {"bucket": [r["bucket"] for r in dev_rows],
                     "composite": [r["composite"] for r in dev_rows]}
                )
                forecasts = fit(history, horizons)
                await self._pool.executemany(
                    """INSERT INTO forecasts (made_at, device_id, horizon, target_time,
                                              composite_pred, ci_low, ci_high, model_version)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                       ON CONFLICT DO NOTHING""",
                    [
                        (made_at, device_id, h, made_at + h,
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
