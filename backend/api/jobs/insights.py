"""Insight rules engine (S2-T05): actionable, non-spammy messages from
window aggregates + latest forecasts.

`RULES` is the STABLE EXTENSION POINT (BACKEND_SCHEMA.md §5) — the real rule
catalogue from a later session only appends/replaces entries in that list.
Thresholds are on the composite's 0-100 scale (TRD §7:
INSIGHT_WARN_THRESHOLD / INSIGHT_ALERT_THRESHOLD).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import asyncpg

from api import queries
from common.config import Settings
from common.durations import format_duration

log = logging.getLogger("api.jobs.insights")

Evidence = dict   # JSONB-serializable values that fired the rule

# Ordering for cooldown escalation (see InsightJob._in_cooldown). The DDL
# constrains `severity` to exactly these three (BACKEND_SCHEMA.md §1).
SEVERITY_RANK = {"info": 1, "warning": 2, "alert": 3}
_SEVERITY_RANK_SQL = (
    "CASE severity WHEN 'alert' THEN 3 WHEN 'warning' THEN 2 ELSE 1 END"
)


@dataclass
class Ctx:
    """Everything a rule may look at, for one device."""
    device_id: str
    display_name: str
    windows: list[dict]            # queries.windows() entries, PAST_WINDOWS order
    forecasts: list[dict] | None   # latest run's points [{horizon,pred,…}] or None
    settings: Settings

    @property
    def shortest(self) -> dict | None:
        return self.windows[0] if self.windows else None

    @property
    def mid(self) -> dict | None:
        return self.windows[1] if len(self.windows) > 1 else None

    @property
    def longest(self) -> dict | None:
        return self.windows[-1] if self.windows else None


@dataclass
class Rule:
    rule_id: str
    severity: str                                  # default; evidence may override
    evaluate: Callable[[Ctx], Evidence | None]
    message: Callable[[Ctx, Evidence], str]
    # S3-T10 (BACKEND_SCHEMA §1 migration 002): action-first rendering. `action`
    # is a short imperative ("Reduce intensity"); `rationale` is the why, with
    # the numbers. Optional so third-party/legacy rules keep working — the UI
    # falls back to `message` when they are null.
    action: Callable[[Ctx, Evidence], str] | None = None
    rationale: Callable[[Ctx, Evidence], str] | None = None


# --- starter rules (catalogue TBD in a later session) -------------------------

def _composite_high(ctx: Ctx) -> Evidence | None:
    w = ctx.shortest
    avg = w and w["composite"]["avg"]
    if avg is None:
        return None
    s = ctx.settings
    if avg >= s.insight_alert_threshold:
        return {"severity": "alert", "window": w["window"], "composite_avg": round(avg, 2),
                "threshold": s.insight_alert_threshold}
    if avg >= s.insight_warn_threshold:
        return {"severity": "warning", "window": w["window"], "composite_avg": round(avg, 2),
                "threshold": s.insight_warn_threshold}
    return None


def _rising_risk(ctx: Ctx) -> Evidence | None:
    if ctx.mid is None or ctx.mid["trend"] != "up" or not ctx.forecasts:
        return None
    threshold = ctx.settings.insight_alert_threshold
    crossing = [p for p in ctx.forecasts if p["pred"] >= threshold]
    if not crossing:
        return None
    # _latest_forecast_points ORDERs BY the raw horizon interval, so the list is
    # already earliest-first and crossing[0] is the SOONEST crossing. Selecting
    # min(pred) instead only coincides with that while predictions rise
    # monotonically; fit() clips at 100, so once several horizons saturate the
    # tie-break is arbitrary and the message names the wrong horizon.
    first = crossing[0]
    return {"window": ctx.mid["window"], "trend": "up",
            "pred": round(first["pred"], 1), "horizon": first["horizon"],
            "threshold": threshold}


# `quality` is a ratio against the CONFIGURED constant EXPECTED_INPUT_HZ, not
# against anything measured, so an absolute threshold tests the constant as much
# as the link: a live 0.35 is equally consistent with 65% packet loss and with
# EXPECTED_INPUT_HZ being wrong. Worse, an absolute rule on hardware that sits
# permanently below the line re-fires every cooldown forever, which is feed noise
# rather than information -- and the absolute figure is already on screen
# continuously as the quality badge and in /api/health.
#
# So the rule now reports a CHANGE against the device's own longest window: data
# quality that has dropped materially below this device's own recent baseline is
# actionable (a strap moved, a sensor is failing) and self-clears when it
# recovers. A uniformly poor link no longer spams the feed.
QUALITY_DROP_RATIO = 0.8


def _data_quality(ctx: Ctx) -> Evidence | None:
    w, base = ctx.shortest, ctx.longest
    if w is None or base is None or w is base:
        return None
    quality, baseline = w["quality"], base["quality"]
    if quality is None or baseline is None or baseline <= 0.0:
        return None
    if quality >= QUALITY_DROP_RATIO * baseline:
        return None
    return {"window": w["window"], "quality": round(quality, 3),
            "baseline_window": base["window"], "baseline_quality": round(baseline, 3),
            "ratio": round(quality / baseline, 3)}


RULES: list[Rule] = [
    Rule(
        rule_id="composite_high",
        severity="warning",
        evaluate=_composite_high,
        message=lambda ctx, ev: (
            f"{ctx.display_name}: sustained high load (composite {ev['composite_avg']:.0f}"
            f" over last {ev['window']}) — consider reducing intensity."
        ),
        action=lambda ctx, ev: "Reduce training intensity",
        rationale=lambda ctx, ev: (
            f"Injury-risk load averaged {ev['composite_avg']:.0f} over the last"
            f" {ev['window']}, above the {ev['threshold']:.0f} threshold. Sustained"
            f" levels this high reflect accumulated dose, not just a momentary effort."
        ),
    ),
    Rule(
        rule_id="rising_risk",
        severity="warning",
        evaluate=_rising_risk,
        message=lambda ctx, ev: (
            f"{ctx.display_name}: risk rising and projected to reach {ev['pred']:.0f}"
            f" within {ev['horizon']} — schedule rest."
        ),
        action=lambda ctx, ev: "Schedule rest before the next block",
        rationale=lambda ctx, ev: (
            f"Risk has been trending up over the last {ev['window']} and is projected"
            f" to reach {ev['pred']:.0f} within {ev['horizon']}"
            f" (alert threshold {ev['threshold']:.0f})."
        ),
    ),
    Rule(
        rule_id="data_quality",
        severity="info",
        evaluate=_data_quality,
        message=lambda ctx, ev: (
            f"{ctx.display_name}: data quality {ev['quality']:.0%} over last"
            f" {ev['window']}, down from {ev['baseline_quality']:.0%} over"
            f" {ev['baseline_window']} — check sensor fit."
        ),
        action=lambda ctx, ev: "Check sensor fit",
        rationale=lambda ctx, ev: (
            f"Data quality over the last {ev['window']} is {ev['quality']:.0%}, down"
            f" from {ev['baseline_quality']:.0%} over {ev['baseline_window']} — a"
            f" strap may have moved or a sensor may be failing."
        ),
    ),
]


# --- engine -------------------------------------------------------------------

class InsightJob:
    """Every INSIGHT_INTERVAL_S: evaluate RULES per device; insert only if no
    same (device, rule_id) insight within INSIGHT_COOLDOWN_S."""

    def __init__(self, settings: Settings, pool: asyncpg.Pool,
                 rules: list[Rule] | None = None) -> None:
        self._settings = settings
        self._pool = pool
        self._rules = RULES if rules is None else rules
        self._task: asyncio.Task | None = None
        self.last_run: datetime | None = None
        self.last_error: str | None = None
        self.runs = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="insights-job")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._settings.insight_interval_s)
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — job must survive anything
                self.last_error = str(exc)
                log.exception("insight run failed")

    async def _latest_forecast_points(self, device_id: str) -> list[dict] | None:
        rows = await self._pool.fetch(
            """SELECT horizon, composite_pred FROM forecasts
               WHERE device_id = $1
                 AND made_at = (SELECT max(made_at) FROM forecasts WHERE device_id = $1)
               ORDER BY horizon""",
            device_id,
        )
        if not rows:
            return None
        return [{"horizon": format_duration(r["horizon"]),
                 "pred": float(r["composite_pred"])} for r in rows]

    async def run_once(self) -> int:
        """One evaluation sweep; returns number of insights inserted."""
        inserted = 0
        devices = await self._pool.fetch(
            "SELECT device_id, display_name FROM devices ORDER BY device_id"
        )
        for dev in devices:
            device_id = dev["device_id"]
            try:
                windows = (await queries.windows(
                    self._pool, self._settings, device_id))["windows"]
                ctx = Ctx(
                    device_id=device_id,
                    display_name=dev["display_name"],
                    windows=windows,
                    forecasts=await self._latest_forecast_points(device_id),
                    settings=self._settings,
                )
                for rule in self._rules:
                    evidence = rule.evaluate(ctx)
                    if evidence is None:
                        continue
                    severity = evidence.get("severity", rule.severity)
                    if await self._in_cooldown(device_id, rule.rule_id, severity):
                        continue
                    await self._pool.execute(
                        """INSERT INTO insights (device_id, severity, rule_id,
                                                 message, context, action, rationale)
                           VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)""",
                        device_id,
                        severity,
                        rule.rule_id,
                        rule.message(ctx, evidence),
                        json.dumps(evidence),
                        rule.action(ctx, evidence) if rule.action else None,
                        rule.rationale(ctx, evidence) if rule.rationale else None,
                    )
                    inserted += 1
            except Exception as exc:  # noqa: BLE001 — isolate per device
                self.last_error = f"{device_id}: {exc}"
                log.exception("insights failed for device %s", device_id)
        self.last_run = datetime.now(tz=timezone.utc)
        self.runs += 1
        return inserted

    async def _in_cooldown(self, device_id: str, rule_id: str, severity: str) -> bool:
        """Suppress unless this firing is STRICTLY MORE SEVERE than anything
        already recorded for (device, rule) inside the cooldown window.

        Keying on (device, rule) alone -- the original -- meant a `composite_high`
        WARNING silently swallowed a genuine ALERT arriving 60 s later for the
        full 600 s cooldown: the escalation a trainer most needs to see was the
        one case guaranteed to be dropped.

        Keying on (device, rule, severity) instead would fix that but introduce
        the reverse noise, letting a warning re-fire in the shadow of a recent
        alert. Ranking is the form that suppresses in one direction only.
        """
        max_rank = await self._pool.fetchval(
            f"""SELECT max({_SEVERITY_RANK_SQL}) FROM insights
                WHERE device_id = $1 AND rule_id = $2
                  AND created_at > now() - $3::interval""",
            device_id, rule_id,
            timedelta(seconds=self._settings.insight_cooldown_s),
        )
        if max_rank is None:
            return False
        return SEVERITY_RANK.get(severity, 0) <= int(max_rank)
