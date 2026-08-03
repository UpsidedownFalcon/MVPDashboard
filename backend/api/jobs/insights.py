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


METRIC_KEYS = ("m1", "m2", "m3", "m4", "m5")
METRIC_NAMES = {
    "m1": "impact", "m2": "loading rate", "m3": "accumulated load",
    "m4": "movement control", "m5": "left/right balance",
    "composite": "injury-risk load",
}
# m4/m5 have no real-data validation (biomech SPEC Section 11.1) -- synthetic
# fixtures only. Any evidence derived from them is marked so the UI can say so.
UNVALIDATED_METRICS = frozenset({"m4", "m5"})

# A deviation must clear this many of the athlete's OWN standard deviations
# before it is called a change. Below it, the difference is inside the spread
# the metric shows anyway. ~2 sd is the conventional "unusual" boundary and is
# deliberately conservative: at realistic injury base rates roughly 90% of
# individual alerts are false (SPEC Section 2), so specificity beats sensitivity.
Z_NOTABLE = 2.0
Z_STRONG = 3.0
# sd floors, in metric points. A window that happens to be nearly constant
# produces a tiny sd and would turn any difference into a huge z.
SD_FLOOR = 3.0
# Rules that describe the ATHLETE are suppressed when the windows being compared
# were measured at materially different link quality. Measured 2026-08-03: 5%
# packet loss moves m1 -49% and m2 +34% (SPEC Section 13 item 11), so comparing
# across quality regimes compares measurement artefacts, not the athlete.
QUALITY_MATCH_RATIO = 0.75
MIN_COVERAGE = 0.10


@dataclass
class MetricView:
    """One metric seen across the whole time axis at once.

    `z` is the load-bearing field, and the reason this layer exists: it states
    how far the recent window sits from the athlete's own longer-run baseline in
    units of that athlete's own spread. That is EXACTLY invariant to the metric's
    0-100 normalisation bounds -- rescaling `lo`/`hi` multiplies `now`,
    `baseline` and `sd` by the same factor and shifts them by the same constant,
    both of which cancel. So a future retune of the biomech reference ranges (or
    of the composite curve) changes the numbers a rule REPORTS without changing
    whether it fires. Absolute thresholds have no such property, which is why
    they are used only where the scale is definitional (the 0-1 quality ratio)
    or configured by the operator (the composite warn/alert thresholds).
    """
    key: str
    now: float | None          # shortest window
    baseline: float | None     # longest window
    sd: float | None           # spread over the longest window
    z: float | None            # (now - baseline) / max(sd, SD_FLOOR)
    trend: str                 # of the mid window, or the shortest if only one

    @property
    def name(self) -> str:
        return METRIC_NAMES.get(self.key, self.key)

    @property
    def unvalidated(self) -> bool:
        return self.key in UNVALIDATED_METRICS


def _metric_view(windows: list[dict], key: str, idx: int | None) -> MetricView:
    def pick(w: dict | None, field: str):
        if w is None:
            return None
        if idx is None:
            return w["composite"][field]
        return w[field][idx] if w.get(field) else None

    short, long_ = windows[0], windows[-1]
    mid = windows[1] if len(windows) > 1 else windows[0]
    now = pick(short, "avg" if idx is None else "m")
    base = pick(long_, "avg" if idx is None else "m")
    sd = pick(long_, "sd")
    z = None
    if now is not None and base is not None:
        z = (now - base) / max(sd or 0.0, SD_FLOOR)
    return MetricView(key=key, now=now, baseline=base, sd=sd, z=z,
                      trend=mid.get("trend", "flat"))


@dataclass
class Ctx:
    """Everything a rule may look at, for one device.

    Rules read `metrics` / `horizons` rather than indexing `windows` directly:
    that is what makes an insight reflect the whole picture -- every primitive,
    every configured past window, and every projected horizon -- instead of one
    number. `windows` and `forecasts` stay available for anything bespoke.
    """
    device_id: str
    display_name: str
    windows: list[dict]            # queries.windows() entries, PAST_WINDOWS order
    forecasts: list[dict] | None   # latest run's points [{horizon,pred,ci_low,…}]
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

    @property
    def metrics(self) -> dict[str, MetricView]:
        """m1..m5 + composite, each across every configured window."""
        if not self.windows:
            return {}
        out = {k: _metric_view(self.windows, k, i)
               for i, k in enumerate(METRIC_KEYS)}
        out["composite"] = _metric_view(self.windows, "composite", None)
        return out

    @property
    def horizons(self) -> list[dict]:
        return self.forecasts or []

    @property
    def furthest(self) -> dict | None:
        """The last projected horizon -- the one that says where this is going."""
        return self.horizons[-1] if self.horizons else None

    @property
    def trustworthy(self) -> bool:
        """Is the athlete-state picture worth acting on at all?

        Guards every rule that makes a claim about the PERSON (not about the
        data): enough of the window actually has data, and the two windows being
        compared were measured at comparable link quality.
        """
        short, long_ = self.shortest, self.longest
        if short is None or long_ is None:
            return False
        cov = short.get("coverage")
        if cov is not None and cov < MIN_COVERAGE:
            return False
        qs, ql = short.get("quality"), long_.get("quality")
        if qs is None or ql is None or ql <= 0.0:
            return True          # nothing to compare against; do not block
        return min(qs, ql) / max(qs, ql) >= QUALITY_MATCH_RATIO


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


# --- rule catalogue -----------------------------------------------------------
#
# Design rules, all load-bearing:
#
# 1. WITHIN-ATHLETE, NOT POPULATION. Every rule about the athlete compares them
#    to their own longer-run baseline in units of their own spread (MetricView.z).
#    That is what the field recommends over absolute cut-offs, what SPEC §6.3
#    requires, and what makes these rules survive a future metric retune
#    unchanged -- see MetricView's docstring for why the invariance is exact.
# 2. HOLISTIC. Rules read `ctx.metrics` (every primitive across every configured
#    past window) and `ctx.horizons` (every projection), not a single number.
# 3. SPECIFICITY OVER SENSITIVITY. At realistic injury base rates ~90% of
#    individual alerts are false even for a genuinely good composite (SPEC §2),
#    so rules require ~2 sd of their own spread and AND-conditions rather than
#    firing on any excursion.
# 4. NO ACWR, IN ANY FORM. The ratio is mathematically coupled (Lolli 2019) and
#    has no evidence supporting its use for load management (Impellizzeri 2020).
#    Absent by intent, not by omission (SPEC §6.3).
# 5. EVIDENCE ALWAYS. Every rule returns the numbers that fired it, because
#    SPEC §2 forbids an individual flag without the component panel behind it.
#    `rationale` renders those numbers; `action` is what to do about them.


def _base_evidence(ctx: Ctx, m: MetricView) -> Evidence:
    """Evidence common to every within-athlete deviation rule."""
    short, long_ = ctx.shortest or {}, ctx.longest or {}
    ev: Evidence = {
        "metric": m.key,
        "metric_name": m.name,
        "window": short.get("window"),
        "baseline_window": long_.get("baseline_window", long_.get("window")),
        "value": None if m.now is None else round(m.now, 1),
        "baseline": None if m.baseline is None else round(m.baseline, 1),
        "sd": None if m.sd is None else round(m.sd, 1),
        "z": None if m.z is None else round(m.z, 2),
        "quality": short.get("quality"),
        "coverage": short.get("coverage"),
    }
    if m.unvalidated:
        ev["unvalidated"] = True
    return ev


def _deviation_rationale(ctx: Ctx, ev: Evidence) -> str:
    """Shared plain-language body: what moved, versus what, by how much."""
    bits = [
        f"{ev['metric_name'].capitalize()} over the last {ev['window']} is"
        f" {ev['value']:.0f} against a {ev['baseline_window']} baseline of"
        f" {ev['baseline']:.0f} — {ev['z']:.1f}× this athlete's own typical"
        f" spread."
    ]
    if ev.get("settles_at") is not None:
        bits.append(
            f"Even with no further load it settles around"
            f" {ev['settles_at']:.0f} over the next {ev['horizon']}."
        )
    elif ev.get("projected") is not None:
        bits.append(
            f"If the recent level continues it reaches about"
            f" {ev['projected']:.0f} within {ev['horizon']}."
        )
    if ev.get("unvalidated"):
        bits.append(
            "This metric has no real-world validation yet — treat as a prompt to"
            " look, not as a finding."
        )
    return " ".join(bits)


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


def _load_spike(ctx: Ctx) -> Evidence | None:
    """A session materially harder than this athlete's own recent norm.

    This is the best-evidenced signal available. Current running research finds
    injuries are driven by doing too much in a SINGLE SESSION relative to recent
    history rather than by weekly totals -- a >=30% single-run spike carried a
    64% higher injury rate, while week-to-week change and the acute:chronic
    ratio had little or no predictive value. Expressed against the athlete's own
    spread, so it is a within-athlete change and not a population cut-off
    (SPEC Section 6.3 forbids fixed population thresholds).
    """
    if not ctx.trustworthy:
        return None
    m = ctx.metrics.get("composite")
    if m is None or m.z is None:
        return None
    if m.z < Z_NOTABLE:
        return None
    ev = _base_evidence(ctx, m)
    ev["severity"] = "alert" if m.z >= Z_STRONG else "warning"
    # what the projection says about where this goes
    far = ctx.furthest
    if far is not None:
        ev["horizon"] = far["horizon"]
        ev["projected"] = round(far["pred"], 1)
    return ev


def _accumulated_load(ctx: Ctx) -> Evidence | None:
    """Accumulated mechanical dose is high AND still climbing.

    m3 is the dose term: a power-law-weighted, 45-minute-half-life accumulation.
    Kalkhoven's first-principles model is the grounding -- repetitive loading
    accumulates damage, damage lowers the tissue's critical threshold, and
    injury occurs when load exceeds that declining threshold. So a dose that is
    both high and still rising is the mechanistically meaningful state, which is
    also why the composite carries m3 as a floor it decays onto rather than as
    another averaged term.

    Uses PAST (dose vs the athlete's baseline, and its trend) and FUTURE (where
    the dose settles if they stop now) together.
    """
    if not ctx.trustworthy:
        return None
    m = ctx.metrics.get("m3")
    if m is None or m.z is None or m.now is None:
        return None
    if m.z < Z_NOTABLE or m.trend != "up":
        return None
    ev = _base_evidence(ctx, m)
    ev["trend"] = m.trend
    ev["severity"] = "alert" if m.z >= Z_STRONG else "warning"
    far = ctx.furthest
    if far is not None and far.get("ci_low") is not None:
        ev["horizon"] = far["horizon"]
        ev["settles_at"] = round(far["ci_low"], 1)
    return ev


def _residual_load(ctx: Ctx) -> Evidence | None:
    """Even if the athlete stops right now, risk stays elevated.

    `ci_low` under `dose-scenario-1` is the "if they stop now" branch: closed-
    form decay of the already-accumulated dose onto the composite's floor. It is
    arithmetic about load already taken, NOT a prediction about what the athlete
    will do -- which is what makes it safe to act on under SPEC Section 2, where
    behavioural prediction is not defensible.

    The actionable content is recovery scheduling rather than stopping.
    """
    far = ctx.furthest
    if far is None or far.get("ci_low") is None:
        return None
    floor = far["ci_low"]
    warn = ctx.settings.insight_warn_threshold
    if floor < warn:
        return None
    return {"severity": "warning", "horizon": far["horizon"],
            "settles_at": round(floor, 1), "threshold": warn,
            "window": (ctx.shortest or {}).get("window")}


def _impact_deviation(ctx: Ctx) -> Evidence | None:
    """Impact magnitude or loading rate deviating from this athlete's baseline.

    Loading rate -- not peak magnitude -- is the ground-reaction variable most
    consistently associated with running injury, which is why m2 is watched
    alongside m1. Held to `info` deliberately: IMU jerk has never been validated
    against GRF loading rate (SPEC Section 5.2), and peak tibial acceleration
    does not track internal tibial load (Matijevich 2019, r = -0.29 +- 0.37).
    These are surrogates for EXTERNAL impact loading, so the honest action is to
    look at mechanics, not to assert tissue damage.
    """
    if not ctx.trustworthy:
        return None
    hits = [m for k in ("m1", "m2")
            if (m := ctx.metrics.get(k)) and m.z is not None and m.z >= Z_NOTABLE]
    if not hits:
        return None
    worst = max(hits, key=lambda m: m.z or 0.0)
    ev = _base_evidence(ctx, worst)
    ev["severity"] = "info"
    ev["also"] = [m.key for m in hits if m.key != worst.key] or None
    return ev


def _movement_quality(ctx: Ctx) -> Evidence | None:
    """Movement control or L/R balance drifting from this athlete's baseline.

    `info` only, and explicitly flagged unvalidated. Two independent reasons:
    m4/m5 have no real-data validation at all (SPEC Section 11.1, synthetic
    fixtures only), and the largest prospective test of asymmetry (Malisoux
    2024, n = 836, 107 injuries) found GREATER asymmetry associated with LOWER
    injury risk. So this can be surfaced as something to look at, and must never
    be presented as risk -- nor given a direction, since which limb dominates
    does not agree across sessions (SPEC Section 5.5).
    """
    if not ctx.trustworthy:
        return None
    hits = [m for k in ("m4", "m5")
            if (m := ctx.metrics.get(k)) and m.z is not None and m.z >= Z_NOTABLE]
    if not hits:
        return None
    worst = max(hits, key=lambda m: m.z or 0.0)
    ev = _base_evidence(ctx, worst)
    ev["severity"] = "info"
    ev["unvalidated"] = True
    return ev


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
        rule_id="load_spike",
        severity="warning",
        evaluate=_load_spike,
        message=lambda ctx, ev: (
            f"{ctx.display_name}: load over the last {ev['window']} is well above"
            f" their own recent norm ({ev['value']:.0f} vs {ev['baseline']:.0f})."
        ),
        action=lambda ctx, ev: "Ease off for the rest of this session",
        rationale=lambda ctx, ev: (
            _deviation_rationale(ctx, ev)
            + " Doing too much in a single session relative to recent history is"
              " the load pattern most consistently linked to injury."
        ),
    ),
    Rule(
        rule_id="accumulated_load",
        severity="warning",
        evaluate=_accumulated_load,
        message=lambda ctx, ev: (
            f"{ctx.display_name}: accumulated load is high and still climbing"
            f" ({ev['value']:.0f} over {ev['window']})."
        ),
        action=lambda ctx, ev: "Cap this session and protect recovery",
        rationale=lambda ctx, ev: (
            _deviation_rationale(ctx, ev)
            + " Accumulated load is still trending up, so each additional bout is"
              " landing on tissue that has less capacity than it started with."
        ),
    ),
    Rule(
        rule_id="residual_load",
        severity="warning",
        evaluate=_residual_load,
        message=lambda ctx, ev: (
            f"{ctx.display_name}: risk stays around {ev['settles_at']:.0f} over the"
            f" next {ev['horizon']} even if they stop now."
        ),
        action=lambda ctx, ev: "Schedule recovery before the next session",
        rationale=lambda ctx, ev: (
            f"Load already accumulated decays slowly: even with no further"
            f" training, projected risk only falls to about"
            f" {ev['settles_at']:.0f} over the next {ev['horizon']}, still above"
            f" the {ev['threshold']:.0f} review threshold. This is decay of load"
            f" already taken, not a prediction of what they will do next."
        ),
    ),
    Rule(
        rule_id="impact_deviation",
        severity="info",
        evaluate=_impact_deviation,
        message=lambda ctx, ev: (
            f"{ctx.display_name}: {ev['metric_name']} over the last {ev['window']}"
            f" is above their baseline ({ev['value']:.0f} vs {ev['baseline']:.0f})."
        ),
        action=lambda ctx, ev: "Review landing mechanics",
        rationale=lambda ctx, ev: (
            _deviation_rationale(ctx, ev)
            + " These measure external impact loading at the shank, not tissue"
              " stress — worth a look at technique and surface rather than a"
              " conclusion about the athlete."
        ),
    ),
    Rule(
        rule_id="movement_quality",
        severity="info",
        evaluate=_movement_quality,
        message=lambda ctx, ev: (
            f"{ctx.display_name}: {ev['metric_name']} has drifted from their"
            f" fresh baseline over the last {ev['window']}."
        ),
        action=lambda ctx, ev: "Flag for review at the next check-in",
        rationale=lambda ctx, ev: (
            _deviation_rationale(ctx, ev)
            + " Reported as magnitude only — which side leads does not agree"
              " between sessions, and greater asymmetry has not been shown to"
              " predict injury."
        ),
    ),
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
        """Latest run's points, earliest horizon first.

        `ci_low`/`ci_high` are carried through because under `dose-scenario-1`
        they are not estimator error but two counterfactuals, and `ci_low` in
        particular -- "where risk settles if the athlete stops right now" -- is
        the single most actionable number the forecast produces. It is closed-
        form dose decay, not a prediction about behaviour.
        """
        rows = await self._pool.fetch(
            """SELECT horizon, composite_pred, ci_low, ci_high, model_version
               FROM forecasts
               WHERE device_id = $1
                 AND made_at = (SELECT max(made_at) FROM forecasts WHERE device_id = $1)
               ORDER BY horizon""",
            device_id,
        )
        if not rows:
            return None
        return [{"horizon": format_duration(r["horizon"]),
                 "pred": float(r["composite_pred"]),
                 "ci_low": None if r["ci_low"] is None else float(r["ci_low"]),
                 "ci_high": None if r["ci_high"] is None else float(r["ci_high"]),
                 "model_version": r["model_version"]} for r in rows]

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
