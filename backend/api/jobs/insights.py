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


def _metric_view(windows: list[dict], key: str, idx: int | None,
                 now_window: dict | None = None) -> MetricView:
    """`now_window` overrides which window supplies `now` (the live window).

    Only the "now" side moves: `baseline`, `sd` and `trend` still come from the
    configured PAST_WINDOWS, because a 30-second window is far too short to
    define what is normal for an athlete or which way they are heading. Reading
    a short "now" against a long baseline is the whole point — it is what lets
    an insight appear within a window instead of within a 5-minute mean.
    """
    def pick(w: dict | None, field: str):
        if w is None:
            return None
        if idx is None:
            return w["composite"][field]
        return w[field][idx] if w.get(field) else None

    short = now_window if now_window is not None else windows[0]
    long_ = windows[-1]
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
    # queries.live_window() — INSIGHT_LIVE_WINDOW (default 30s) off the raw
    # hypertable. None only when a caller builds a Ctx by hand; then everything
    # falls back to the shortest configured window, i.e. the pre-2026-08-04
    # behaviour.
    live: dict | None = None

    @property
    def shortest(self) -> dict | None:
        return self.windows[0] if self.windows else None

    @property
    def now_window(self) -> dict | None:
        """The window every rule means by "now"."""
        return self.live if self.live is not None else self.shortest

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
        out = {k: _metric_view(self.windows, k, i, self.live)
               for i, k in enumerate(METRIC_KEYS)}
        out["composite"] = _metric_view(self.windows, "composite", None, self.live)
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
        short, long_ = self.now_window, self.longest
        if short is None or long_ is None:
            return False
        cov = short.get("coverage")
        if cov is not None and cov < MIN_COVERAGE:
            return False
        qs, ql = short.get("quality"), long_.get("quality")
        if qs is None or ql is None or ql <= 0.0:
            return True          # nothing to compare against; do not block
        return min(qs, ql) / max(qs, ql) >= QUALITY_MATCH_RATIO


# --- action catalogue ---------------------------------------------------------
#
# The panel shows AT MOST `INSIGHT_MAX_ACTIONS` imperatives, each justified by
# every rule that currently supports it. That inversion is the reason this
# catalogue exists: rules are diagnostic (one per mechanism) but advice is not.
# `load_spike` and `composite_high` are different findings that lead to the SAME
# instruction, and showing "Ease off" twice with two rationales is noise, while
# showing it once with two reasons underneath is evidence.
#
# 🚩 `text` was deliberately 2-3 words ("Ease off", "Check mechanics") on the
# reasoning that a headline long enough to wrap stops reading as an instruction.
# REVERSED 2026-08-04 by product-owner decision: at that length the headline was
# not actionable -- a trainer reading "Check mechanics" learns nothing they can
# do. Headlines now name the LEVER the trainer controls. Keep them one short
# clause; do not let them grow into sentences.
#
# What a headline may and may not say is bounded by evidence, not taste:
#   - Name a lever the trainer controls: intensity, sets, landing height, reps,
#     surface, the gap before the next block, the straps.
#   - NEVER name a body part, tissue, or outcome -- m1/m2 are surrogates for
#     EXTERNAL impact loading and do not track internal tibial load
#     (Matijevich 2019, r = -0.29 +- 0.37; SPEC Section 2).
#   - NEVER prescribe a foot-strike pattern. The sensors are shank+thigh and the
#     model is movement-agnostic by mandate (SPEC Section 1.2, "no activity
#     classifier"), so foot contact is not observed. Advice about it would be
#     fabricated from data that does not exist.
#   - NEVER prescribe a stretch or any treatment: PRD Section 5 non-goal, "no
#     medical claims -- a training-load guidance tool, not a diagnostic device".
#   - Timings must be model arithmetic, not invented. The only defensible one
#     available is m3's decay half-life (DOSE_HALFLIFE, biomech SPEC Section 5.3).
#
# `tip` is a STATIC coaching cue per action. It is the same text every time the
# action fires and is NOT derived from this athlete's data -- the UI renders it
# under an explicit "General cue - not measured" label so it can never read as a
# finding. Keep tips to technique/setup generalities that hold regardless of what
# the sensors saw.

@dataclass(frozen=True)
class Action:
    action_id: str
    text: str    # short imperative, rendered large
    rank: int    # tie-break within equal severity; lower = surfaced first
    tip: str = ""   # static coaching cue; never data-derived (see above)


ACTIONS: dict[str, Action] = {
    a.action_id: a for a in (
        # Ordered by how directly each changes what happens in the next minute.
        Action(
            "ease_off", "Drop the next block down one level", 1,
            "Keep the next effort conversational — if they cannot talk through"
            " it, it is still too hard.",
        ),
        Action(
            "cap_session", "No more hard sets — easy work only", 2,
            "Finish with low-intensity movement rather than stopping dead; keep"
            " the remaining work continuous and light.",
        ),
        Action(
            "plan_recovery", "Leave a longer gap before the next hard block", 3,
            # Model arithmetic, not an invented duration: dose is filed to two
            # pools at the moment it is earned (biomech.py DOSE_HALFLIFE_S /
            # DOSE_HALFLIFE_SLOW_S). Hard work is filed to the slow pool, so it
            # is the 90-minute figure that governs recovery from a hard block.
            "Load earned in hard work falls by about half every 90 minutes at"
            " rest — easy work clears far faster, in around 15.",
        ),
        # check_mechanics was split 2026-08-04. It grouped impact_deviation
        # (m1/m2) with movement_quality (m4/m5) under one headline despite very
        # different evidence licence: SPEC Section 11.1 forbids presenting m4/m5
        # to a trainer as a finding at all, while m1/m2 support concrete advice.
        Action(
            "lower_landings", "Lower the landing height or cut the reps", 4,
            "Peak shock scales with drop height far more than with effort —"
            " lowering the box or the jump does more than cueing harder.",
        ),
        Action(
            "soften_landings", "Soften the landings — check the surface", 4,
            "Cue quieter, more absorbed contacts, and check what they are"
            " landing on: a harder surface raises loading rate on its own.",
        ),
        Action(
            "flag_review", "Worth a look at the next check-in", 5,
            "Movement Control and L/R Balance have no real-world validation yet"
            " — treat this as something to watch, not to act on today.",
        ),
        Action(
            "check_sensors", "Re-seat the straps and check the battery", 6,
            "A strap that has worked loose mid-session is the most common cause;"
            " re-seat it snugly over the muscle belly, not the joint.",
        ),
    )
}


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
    # Migration 003. `action_id` keys into ACTIONS and is what /api/insights/
    # current groups on — several rules share one on purpose. `reason` is ONE
    # short sentence carrying the numbers, sized to render as a bullet with no
    # expander; `rationale` stays as the long form for the audit feed.
    #
    # A CALLABLE `action_id` lets one rule pick its action from the evidence
    # (2026-08-04). `impact_deviation` already knows whether m1 or m2 moved and
    # was discarding it to emit one generic headline; peak shock and loading
    # rate call for different levers, so it now routes to `lower_landings` or
    # `soften_landings`. Resolved once, at insert time.
    action_id: str | Callable[[Ctx, Evidence], str] | None = None
    reason: Callable[[Ctx, Evidence], str] | None = None

    def resolve_action_id(self, ctx: Ctx, evidence: Evidence) -> str | None:
        if callable(self.action_id):
            return self.action_id(ctx, evidence)
        return self.action_id


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
    short, long_ = ctx.now_window or {}, ctx.longest or {}
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


def _deviation_reason(ctx: Ctx, ev: Evidence) -> str:
    """One-sentence bullet: what moved, versus what, by how much.

    Same facts as `_deviation_rationale` minus the citations and caveats. The
    panel renders these as bullets with NO expander, so every reason has to be
    complete and short at the same time — one clause of measurement, one of
    comparison. The unvalidated marker is the only qualifier kept inline,
    because SPEC §11.1 forbids presenting m4/m5 as a finding anywhere.
    """
    text = (
        f"{ev['metric_name'].capitalize()} is {ev['value']:.0f} over the last"
        f" {ev['window']} against a {ev['baseline_window']} baseline of"
        f" {ev['baseline']:.0f} ({ev['z']:.1f}× their usual spread)."
    )
    if ev.get("unvalidated"):
        text += " Unvalidated metric — a prompt to look, not a finding."
    return text


def _composite_high(ctx: Ctx) -> Evidence | None:
    w = ctx.now_window
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
            "window": (ctx.now_window or {}).get("window")}


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
    w, base = ctx.now_window, ctx.longest
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
        action=lambda ctx, ev: "Drop the next block down one level",
        rationale=lambda ctx, ev: (
            _deviation_rationale(ctx, ev)
            + " Doing too much in a single session relative to recent history is"
              " the load pattern most consistently linked to injury."
        ),
        action_id="ease_off",
        reason=lambda ctx, ev: _deviation_reason(ctx, ev),
    ),
    Rule(
        rule_id="accumulated_load",
        severity="warning",
        evaluate=_accumulated_load,
        message=lambda ctx, ev: (
            f"{ctx.display_name}: accumulated load is high and still climbing"
            f" ({ev['value']:.0f} over {ev['window']})."
        ),
        action=lambda ctx, ev: "No more hard sets — easy work only",
        rationale=lambda ctx, ev: (
            _deviation_rationale(ctx, ev)
            + " Accumulated load is still trending up, so each additional bout is"
              " landing on tissue that has less capacity than it started with."
        ),
        action_id="cap_session",
        reason=lambda ctx, ev: (
            _deviation_reason(ctx, ev) + " It is still trending up."
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
        action=lambda ctx, ev: "Leave a longer gap before the next hard block",
        rationale=lambda ctx, ev: (
            f"Load already accumulated decays slowly: even with no further"
            f" training, projected risk only falls to about"
            f" {ev['settles_at']:.0f} over the next {ev['horizon']}, still above"
            f" the {ev['threshold']:.0f} review threshold. This is decay of load"
            f" already taken, not a prediction of what they will do next."
        ),
        action_id="plan_recovery",
        reason=lambda ctx, ev: (
            f"Even if they stop now, risk only falls to about"
            f" {ev['settles_at']:.0f} over the next {ev['horizon']} — still above"
            f" the {ev['threshold']:.0f} review threshold."
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
        action=lambda ctx, ev: (
            "Lower the landing height or cut the reps"
            if ev.get("metric") == "m1" else
            "Soften the landings — check the surface"
        ),
        rationale=lambda ctx, ev: (
            _deviation_rationale(ctx, ev)
            + " These measure external impact loading at the shank, not tissue"
              " stress — worth a look at technique and surface rather than a"
              " conclusion about the athlete."
        ),
        # m1 = how HARD the peak is (height/volume); m2 = how FAST load arrives
        # (technique/surface). Different levers, so different actions.
        action_id=lambda ctx, ev: (
            "lower_landings" if ev.get("metric") == "m1" else "soften_landings"
        ),
        reason=lambda ctx, ev: (
            _deviation_reason(ctx, ev)
            + " This is external impact loading, not tissue stress."
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
        action=lambda ctx, ev: "Worth a look at the next check-in",
        rationale=lambda ctx, ev: (
            _deviation_rationale(ctx, ev)
            + " Reported as magnitude only — which side leads does not agree"
              " between sessions, and greater asymmetry has not been shown to"
              " predict injury."
        ),
        # NOT check_mechanics: m4/m5 may not be presented as a finding at all
        # (SPEC Section 11.1), so this advice stays soft and separate from the
        # concrete m1/m2 landing advice it used to share a headline with.
        action_id="flag_review",
        reason=lambda ctx, ev: (
            _deviation_reason(ctx, ev) + " Magnitude only, never a side."
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
        action=lambda ctx, ev: "Drop the next block down one level",
        rationale=lambda ctx, ev: (
            f"Injury-risk load averaged {ev['composite_avg']:.0f} over the last"
            f" {ev['window']}, above the {ev['threshold']:.0f} threshold. Sustained"
            f" levels this high reflect accumulated dose, not just a momentary effort."
        ),
        action_id="ease_off",
        reason=lambda ctx, ev: (
            f"Injury-risk load averaged {ev['composite_avg']:.0f} over the last"
            f" {ev['window']}, above the {ev['threshold']:.0f} threshold."
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
        action=lambda ctx, ev: "Leave a longer gap before the next hard block",
        rationale=lambda ctx, ev: (
            f"Risk has been trending up over the last {ev['window']} and is projected"
            f" to reach {ev['pred']:.0f} within {ev['horizon']}"
            f" (alert threshold {ev['threshold']:.0f})."
        ),
        action_id="plan_recovery",
        reason=lambda ctx, ev: (
            f"Risk is trending up and projected to reach {ev['pred']:.0f} within"
            f" {ev['horizon']} (alert threshold {ev['threshold']:.0f})."
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
        action=lambda ctx, ev: "Re-seat the straps and check the battery",
        rationale=lambda ctx, ev: (
            f"Data quality over the last {ev['window']} is {ev['quality']:.0%}, down"
            f" from {ev['baseline_quality']:.0%} over {ev['baseline_window']} — a"
            f" strap may have moved or a sensor may be failing."
        ),
        action_id="check_sensors",
        reason=lambda ctx, ev: (
            f"Data quality is {ev['quality']:.0%} over the last {ev['window']},"
            f" down from {ev['baseline_quality']:.0%} over"
            f" {ev['baseline_window']}."
        ),
    ),
]


# --- current actions ----------------------------------------------------------

def group_actions(rows: list[dict], max_actions: int) -> list[dict]:
    """Collapse recent insight rows into the CURRENT advice: <= max_actions
    imperatives, each with every reason that currently supports it.

    `rows` are insight records (newest first) already filtered to the hold
    window by the caller. Three things happen here, each load-bearing:

    1. GROUP on `action_id`, so rules that mean the same instruction merge.
    2. KEEP ONE ROW PER RULE — the newest. INSIGHT_HOLD_S deliberately exceeds
       INSIGHT_COOLDOWN_S so an action never blinks out between re-firings, and
       the direct consequence is that a still-true rule has TWO rows inside the
       window. Without this the same sentence would appear twice under one
       heading, which reads as two separate findings.
    3. RANK by severity, then by the catalogue's own ordering, and cut to
       `max_actions`.

    An action is marked `unvalidated` only when EVERY reason behind it comes
    from m4/m5. If even one validated metric supports it the advice does not
    rest on unvalidated ground, and flagging it would understate the finding —
    while flagging nothing when all of them are unvalidated would overstate it
    (SPEC §11.1).
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        # Rows written before migration 003 have no action_id; fall back to the
        # action text so they still group with themselves rather than vanishing.
        key = row.get("action_id") or row.get("action") or row.get("rule_id")
        if key is None:
            continue
        groups.setdefault(key, []).append(row)

    out = []
    for key, members in groups.items():
        newest_per_rule: dict[str, dict] = {}
        for row in members:                       # rows arrive newest-first
            newest_per_rule.setdefault(row["rule_id"], row)
        kept = sorted(
            newest_per_rule.values(),
            key=lambda r: (-SEVERITY_RANK.get(r["severity"], 0), r["rule_id"]),
        )
        action = ACTIONS.get(key)
        top = kept[0]
        reasons = [
            {
                "rule_id": r["rule_id"],
                "severity": r["severity"],
                # `reason` is null for pre-003 rows; the long rationale is the
                # only other complete sentence available, and `message` after
                # that.
                "text": r.get("reason") or r.get("rationale") or r["message"],
                "unvalidated": bool((r.get("context") or {}).get("unvalidated")),
                "created_at": r["created_at"],
            }
            for r in kept
        ]
        out.append({
            "action_id": key,
            "action": action.text if action else (top.get("action") or key),
            # Static coaching cue, never data-derived; the UI must label it as
            # such. None for pre-catalogue keys so the UI simply omits it.
            "tip": (action.tip or None) if action else None,
            "severity": top["severity"],
            "updated_at": max(r["created_at"] for r in kept),
            "unvalidated": all(x["unvalidated"] for x in reasons),
            "reasons": reasons,
            "_rank": action.rank if action else 99,
        })

    out.sort(key=lambda a: (-SEVERITY_RANK.get(a["severity"], 0), a["_rank"]))
    for a in out:
        del a["_rank"]
    return out[:max_actions]


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
                    live=await queries.live_window(
                        self._pool, self._settings, device_id),
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
                                                 message, context, action,
                                                 rationale, action_id, reason)
                           VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)""",
                        device_id,
                        severity,
                        rule.rule_id,
                        rule.message(ctx, evidence),
                        json.dumps(evidence),
                        rule.action(ctx, evidence) if rule.action else None,
                        rule.rationale(ctx, evidence) if rule.rationale else None,
                        rule.resolve_action_id(ctx, evidence),
                        rule.reason(ctx, evidence) if rule.reason else None,
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
