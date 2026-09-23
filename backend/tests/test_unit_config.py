"""Knee-sleeve routing: unit ids, rigs, pairing, full-scale and the one_leg flag.

The wire cannot say which soldier a sleeve is on, which leg it is on, or what
full-scale its counts are in -- an operator sets all three in the dashboard and
the api mirrors them into Redis (ingest/unit_config.py). These tests cover the
ingest side: the cache, the rig the router builds from it, and what biomech
then says about a rig that instruments one leg.
"""

from __future__ import annotations

import numpy as np
import pytest

from common import packet
from common.kinds import (
    SYNC_UNILATERAL,
    UnitConfig,
    lsb_per_dps,
    lsb_per_g,
)
from ingest import biomech
from ingest.state import Registry
from ingest.unit_config import UnitConfigCache

from conftest import counts_from_si

SLEEVE_FS = (32, 4000)


def _cache(**units: UnitConfig) -> UnitConfigCache:
    cache = UnitConfigCache(*SLEEVE_FS)
    for cfg in units.values():
        cache.set(cfg)
    return cache


def _registry(cache: UnitConfigCache | None = None) -> Registry:
    return Registry(max_devices=5, unit_cfg=cache or UnitConfigCache(*SLEEVE_FS))


def _sleeve_batch(device_id: int, source_id: int, sensor_id: int,
                  imu=(10, 20, 30, 1, 2, 3), soc: int = 77, n: int = 3):
    payloads = [
        packet.encode(device_id, source_id, sensor_id, 1000 + i * 1562,
                      imu, soc=soc, sync=SYNC_UNILATERAL)
        for i in range(n)
    ]
    return packet.decode(payloads)


def _bilateral_batch(device_id: int, source_id: int, sensor_id: int, n: int = 3):
    payloads = [
        packet.encode(device_id, source_id, sensor_id, 1000 + i * 1562,
                      [10, 20, 30, 1, 2, 3], soc=50)
        for i in range(n)
    ]
    return packet.decode(payloads)


# --- the cache ----------------------------------------------------------------

def test_unknown_sleeve_defaults_to_its_own_rig_with_the_wire_side() -> None:
    # PLAN_msd_management decision H: source 0 = left, 1 = right by default
    cache = UnitConfigCache(*SLEEVE_FS)
    cfg = cache.get("u30-0")
    assert cfg.rig_id == "u30-0" and cfg.side == "left" and not cfg.paired
    assert (cfg.accel_fs_g, cfg.gyro_fs_dps) == SLEEVE_FS
    assert [c.unit_id for c in cache.members("u30-0")] == ["u30-0"]
    assert cache.get("u30-1").side == "right"


def test_members_lists_the_host_first_even_before_it_is_configured() -> None:
    cache = _cache(joiner=UnitConfig("u31-0", "u30-0", "right", 32, 4000))
    assert [c.unit_id for c in cache.members("u30-0")] == ["u30-0", "u31-0"]


def test_setting_an_identical_config_reports_no_change() -> None:
    cache = UnitConfigCache(*SLEEVE_FS)
    # the registered default (left, decision H) is what ingest already runs
    assert cache.set(UnitConfig("u30-0", "u30-0", "left", 32, 4000)) is None
    cfg = UnitConfig("u30-0", "u30-0", "right", 32, 4000)
    assert cache.set(cfg) is not None      # side changed from the wire default
    assert cache.set(cfg) is None          # idempotent


# --- rig construction ---------------------------------------------------------

def test_an_unpaired_sleeve_is_its_own_rig_on_its_wire_side() -> None:
    # decision H: an unconfigured source-0 sleeve streams left limbs
    reg = _registry()
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1000.0)
    reg.route(_sleeve_batch(30, 0, 2), recv_time=1000.0)

    assert list(reg.devices) == ["u30-0"]
    rig = reg.devices["u30-0"]
    assert rig.limb_map == {(0, 1): "left_thigh", (0, 2): "left_shin"}
    assert rig.expected_limbs == 2
    assert rig.units == ("u30-0",)
    assert sorted(rig.sensors) == [(0, 1), (0, 2)]


def test_a_sleeve_with_a_cleared_side_streams_side_less_limbs() -> None:
    cache = _cache(host=UnitConfig("u30-0", "u30-0", None, 32, 4000))
    reg = _registry(cache)
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1000.0)
    reg.route(_sleeve_batch(30, 0, 2), recv_time=1000.0)

    rig = reg.devices["u30-0"]
    assert rig.limb_map == {(0, 1): "thigh", (0, 2): "shin"}
    assert rig.expected_limbs == 2 and rig.units == ("u30-0",)


def test_a_sleeve_and_a_bilateral_device_sharing_a_byte_never_merge() -> None:
    """Namespacing by kind is what stops a sleeve's samples being mixed into a
    bilateral athlete's stream when both are configured as device 30."""
    reg = _registry()
    reg.route(_bilateral_batch(30, 0, 1), recv_time=1000.0)
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1000.0)

    assert sorted(reg.devices) == ["30", "u30-0"]
    assert reg.devices["30"].limb_map[(0, 1)] == "left_shin"
    assert reg.devices["u30-0"].limb_map[(0, 1)] == "left_thigh"


def test_a_side_puts_the_sleeve_on_its_virtual_source() -> None:
    cache = _cache(right=UnitConfig("u31-0", "u31-0", "right", 32, 4000))
    reg = _registry(cache)
    reg.route(_sleeve_batch(31, 0, 1), recv_time=1000.0)

    rig = reg.devices["u31-0"]
    # source 0 on the wire, virtual source 1 in the rig, because it is the right leg
    assert rig.limb_map == {(1, 1): "right_thigh", (1, 2): "right_shin"}
    assert sorted(rig.sensors) == [(1, 1)]


def test_a_paired_rig_is_built_before_the_second_sleeve_streams() -> None:
    """The map comes from configuration, so the session starts with all four
    limbs instead of being rebuilt (and zeroed) when the other leg joins."""
    cache = _cache(
        host=UnitConfig("u30-0", "u30-0", "left", 32, 4000),
        joiner=UnitConfig("u31-0", "u30-0", "right", 32, 4000),
    )
    reg = _registry(cache)
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1000.0)   # only the host so far

    rig = reg.devices["u30-0"]
    assert rig.units == ("u30-0", "u31-0")
    assert rig.expected_limbs == 4
    assert sorted(rig.limb_map.values()) == [
        "left_shin", "left_thigh", "right_shin", "right_thigh"]

    # the joiner's datagrams land in the same rig, on the right-leg source
    reg.route(_sleeve_batch(31, 0, 2), recv_time=1000.0)
    assert list(reg.devices) == ["u30-0"]
    assert sorted(rig.sensors) == [(0, 1), (1, 2)]


def test_two_side_less_members_do_not_build_a_colliding_map() -> None:
    """Duplicate limb names rebuild the biomech session 60 times a second. The
    api refuses to create this; if it appears anyway, drop the member."""
    # both explicitly cleared: since decision H the host would otherwise
    # default to "left" and there would be nothing to collide with
    cache = _cache(
        host=UnitConfig("u30-0", "u30-0", None, 32, 4000),
        joiner=UnitConfig("u31-0", "u30-0", None, 32, 4000),
    )
    reg = _registry(cache)
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1000.0)

    rig = reg.devices["u30-0"]
    assert rig.units == ("u30-0",)
    assert sorted(rig.limb_map.values()) == ["shin", "thigh"]


def test_a_sleeve_rig_carries_its_own_full_scale() -> None:
    cache = _cache(host=UnitConfig("u30-0", "u30-0", "left", 8, 500))
    reg = _registry(cache)
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1000.0)

    rig = reg.devices["u30-0"]
    assert rig.limb_scale == {
        "left_thigh": (lsb_per_g(8), lsb_per_dps(500)),
        "left_shin": (lsb_per_g(8), lsb_per_dps(500)),
    }
    assert reg.devices["u30-0"].kind != 0          # unilateral


def test_battery_is_tracked_per_unit_and_per_virtual_source() -> None:
    cache = _cache(
        host=UnitConfig("u30-0", "u30-0", "left", 32, 4000),
        joiner=UnitConfig("u31-0", "u30-0", "right", 32, 4000),
    )
    reg = _registry(cache)
    reg.route(_sleeve_batch(30, 0, 1, soc=91), recv_time=1000.0)
    reg.route(_sleeve_batch(31, 0, 1, soc=23), recv_time=1000.0)

    rig = reg.devices["u30-0"]
    assert rig.unit_soc == {"u30-0": 91, "u31-0": 23}
    assert min(rig.soc.values()) == 23      # what the trainer sees


# --- reconfiguration ----------------------------------------------------------

def test_reconfiguring_a_unit_tears_down_both_rigs_and_skips_the_restore() -> None:
    cache = _cache()
    reg = _registry(cache)
    resets: list[str] = []
    reg.on_rig_reset = resets.append
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1000.0)
    reg.route(_sleeve_batch(31, 0, 1), recv_time=1000.0)
    assert sorted(reg.devices) == ["u30-0", "u31-0"]

    changed = reg.apply_unit_config(
        UnitConfig("u31-0", "u30-0", "right", 32, 4000), now=1001.0)
    assert changed
    # both the rig it left and the rig it joined are gone, and both were
    # announced so their stored sessions can be deleted
    assert reg.devices == {}
    assert sorted(resets) == ["u30-0", "u31-0"]

    reg.route(_sleeve_batch(30, 0, 1), recv_time=1001.05)
    rebuilt = reg.devices["u30-0"]
    assert rebuilt.skip_restore is True
    assert rebuilt.expected_limbs == 4

    # ...and the guard lapses, so an ordinary reconnect still restores
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1100.0)
    reg._remove("u30-0", "test")
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1100.0)
    assert reg.devices["u30-0"].skip_restore is False


def test_a_deleted_config_falls_back_to_defaults_and_restarts_the_rig() -> None:
    """A flushed Redis or a deleted key must not leave a stale pairing behind."""
    cache = _cache(joiner=UnitConfig("u31-0", "u30-0", "right", 32, 4000))
    reg = _registry(cache)
    reg.route(_sleeve_batch(31, 0, 1), recv_time=1000.0)
    assert list(reg.devices) == ["u30-0"]

    assert reg.apply_unit_config(cache.default("u31-0"), now=1001.0)
    reg.route(_sleeve_batch(31, 0, 1), recv_time=1002.5)
    assert list(reg.devices) == ["u31-0"]           # back to being its own rig


def test_a_host_paired_elsewhere_is_not_a_member_of_its_own_rig() -> None:
    """An inconsistent chain (A -> B while B -> C) must not put one sleeve's
    samples into two rigs; the rig exists but claims no limbs."""
    cache = _cache(
        host=UnitConfig("u30-0", "u32-0", "left", 32, 4000),
        joiner=UnitConfig("u31-0", "u30-0", "right", 32, 4000),
    )
    assert [c.unit_id for c in cache.members("u30-0")] == ["u31-0"]
    reg = _registry(cache)
    reg.route(_sleeve_batch(31, 0, 1), recv_time=1000.0)
    assert reg.devices["u30-0"].units == ("u31-0",)
    assert sorted(reg.devices["u30-0"].limb_map.values()) == [
        "right_shin", "right_thigh"]


def test_an_unchanged_config_does_not_restart_a_session() -> None:
    cache = _cache(host=UnitConfig("u30-0", "u30-0", "left", 32, 4000))
    reg = _registry(cache)
    reg.route(_sleeve_batch(30, 0, 1), recv_time=1000.0)
    resets: list[str] = []
    reg.on_rig_reset = resets.append

    assert not reg.apply_unit_config(
        UnitConfig("u30-0", "u30-0", "left", 32, 4000), now=1001.0)
    assert resets == [] and "u30-0" in reg.devices


# --- what biomech says about a one-leg rig ------------------------------------

def _still(limbs: tuple[str, ...], n: int = 10, t0: float = 0.0):
    a = np.zeros((n, 3))
    a[:, 1] = 9.81
    f = counts_from_si(a, np.zeros((n, 3)))
    times = t0 + np.arange(n) / 600.0
    return ({limb: f.copy() for limb in limbs}, {limb: times.copy() for limb in limbs})


@pytest.mark.parametrize(("limbs", "expected_limbs", "one_leg", "degraded"), [
    (("left_shin", "left_thigh", "right_shin", "right_thigh"), 4, False, False),
    (("thigh", "shin"), 2, True, False),                 # sleeve, side not set
    (("left_thigh", "left_shin"), 2, True, False),       # sleeve, left leg
    (("left_thigh", "left_shin"), 4, True, True),        # ...half a paired rig
    (("left_shin", "right_shin"), 2, False, False),      # both shanks
])
def test_one_leg_and_degraded_say_different_things(
    limbs: tuple[str, ...], expected_limbs: int, one_leg: bool, degraded: bool,
) -> None:
    """`degraded_sensors` means a sensor the rig should have is missing.
    `one_leg` means the rig only instruments one leg. A healthy sleeve must
    never claim the first (SPEC Section 10)."""
    state: dict = {}
    frames, times = _still(limbs)
    m = biomech.compute(frames, state, times, expected_limbs=expected_limbs)
    assert ("one_leg" in m.flags) is one_leg
    assert ("degraded_sensors" in m.flags) is degraded
    if one_leg:
        assert m.m5 is None


def test_the_same_motion_reads_the_same_at_either_full_scale() -> None:
    """The point of limb_scale: a sleeve at +-32 g / +-4000 dps sends HALF the
    counts for the same movement, and must produce the same metrics as the
    bilateral rig that sent full counts. If this drifts, a sleeve silently
    reads half the impact and loading rate of an identical stride."""
    n, ticks = 10, 60 * 20
    limbs = ("left_thigh", "left_shin", "right_thigh", "right_shin")
    rng = np.random.default_rng(7)

    def run(counts_scale: float, limb_scale):
        state: dict = {}
        out = []
        for k in range(ticks):
            # a repeatable stride-like signal: gravity plus a swing
            t = (k * n + np.arange(n)) / 600.0
            a = np.zeros((n, 3))
            a[:, 1] = 9.81 + 30.0 * np.sin(2 * np.pi * 2.0 * t)
            a[:, 0] = 18.0 * np.sin(2 * np.pi * 2.0 * t + 0.4)
            w = np.zeros((n, 3))
            w[:, 2] = 220.0 * np.sin(2 * np.pi * 2.0 * t)
            raw = counts_from_si(a, w) * counts_scale
            frames = {limb: raw.copy().astype(np.float32) for limb in limbs}
            times = {limb: t.copy() for limb in limbs}
            out.append(biomech.compute(frames, state, times, expected_limbs=4,
                                       limb_scale=limb_scale))
        return out[-1]

    sleeve_scale = {limb: (lsb_per_g(32), lsb_per_dps(4000)) for limb in limbs}
    bilateral = run(1.0, None)
    sleeve = run(0.5, sleeve_scale)

    # m1 (impact) and m3 (dose) are the ones this smooth probe signal drives;
    # m2 needs real foot-strike jerk, which a sine wave does not produce.
    assert bilateral.m1 > 0 and bilateral.m3 > 0, "the probe signal must exercise m1/m3"
    for name in ("m1", "m2", "m3", "composite"):
        a, b = getattr(bilateral, name), getattr(sleeve, name)
        assert a == pytest.approx(b, rel=0.02), f"{name}: bilateral {a} vs sleeve {b}"
    assert bilateral.flags == sleeve.flags

    # ...and the correction is what does it: the same halved counts read as a
    # weaker athlete when the rig is assumed to be bilateral.
    uncorrected = run(0.5, None)
    assert uncorrected.m1 < bilateral.m1 - 1.0
    assert uncorrected.raw["w_int"] == pytest.approx(bilateral.raw["w_int"] / 2, rel=0.02)


def test_a_full_scale_mismatch_is_what_calibration_would_catch() -> None:
    """A sleeve decoded with the bilateral constants reads half of gravity, so
    it can never calibrate; told its real full-scale, it can."""
    # 30 s of stillness: past CAL_DISCARD_S (3 s) + CAL_FAULT_S (20 s), so the
    # mis-scaled rig has time to be branded faulty and the correct one to finish
    # its 10 s window.
    n, ticks = 10, 60 * 30
    limbs = ("left_thigh", "left_shin")
    a = np.zeros((n, 3))
    a[:, 1] = 9.81
    # counts as a +-32 g / +-4000 dps sleeve would send them: half the bilateral
    sleeve_counts = counts_from_si(a, np.zeros((n, 3))) / 2.0
    scale = {limb: (lsb_per_g(32), lsb_per_dps(4000)) for limb in limbs}

    def feed(limb_scale):
        state: dict = {}
        last = None
        for k in range(ticks):
            times = {limb: (k * n + np.arange(n)) / 600.0 for limb in limbs}
            frames = {limb: sleeve_counts.copy().astype(np.float32) for limb in limbs}
            last = biomech.compute(frames, state, times, expected_limbs=2,
                                   limb_scale=limb_scale)
        return last

    uncorrected = feed(None)
    corrected = feed(scale)
    assert "cal_failed" in uncorrected.flags
    assert "cal_failed" not in corrected.flags
    assert "uncalibrated" not in corrected.flags
