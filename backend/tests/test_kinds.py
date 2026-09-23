"""common.kinds: sync bytes, sleeve unit ids, rig kinds and UnitConfig.

The wire facts are the sleeve firmware's (NYKnicksDataLogger app_config.h:
UDP_SYNC_BYTE 0xA6, SENSOR_ID_IMU0 1 = thigh, SENSOR_ID_IMU1 2 = shin,
IMU_ACCEL_FS_G 32, IMU_GYRO_FS_DPS 4000, imu_fs_valid() allowed sets).
"""

from __future__ import annotations

import pytest

from common import kinds


def test_sync_lut_maps_exactly_the_two_syncs() -> None:
    assert int(kinds.SYNC_LUT[0xA5]) == kinds.KIND_BILATERAL
    assert int(kinds.SYNC_LUT[0xA6]) == kinds.KIND_UNILATERAL
    others = [b for b in range(256) if b not in (0xA5, 0xA6)]
    assert all(int(kinds.SYNC_LUT[b]) == kinds.KIND_INVALID for b in others)
    assert kinds.SYNC_BY_KIND[kinds.KIND_UNILATERAL] == 0xA6


def test_full_scale_conversions_match_the_firmware_formula() -> None:
    # g = raw * fs_g / 32768  ->  counts per g = 32768 / fs_g
    assert kinds.lsb_per_g(16) == 2048.0          # the bilateral constant
    assert kinds.lsb_per_g(32) == 1024.0          # sleeve default
    assert kinds.lsb_per_dps(2000) == 16.384      # the bilateral constant
    assert kinds.lsb_per_dps(4000) == 8.192       # sleeve default
    assert kinds.ACCEL_FS_ALLOWED == (2, 4, 8, 16, 32)
    assert kinds.GYRO_FS_ALLOWED == (125, 250, 500, 1000, 2000, 4000)


@pytest.mark.parametrize(("dev", "src", "expected"), [(30, 0, "u30-0"), (1, 1, "u1-1"), (255, 0, "u255-0")])
def test_unit_id_round_trip(dev: int, src: int, expected: str) -> None:
    assert kinds.unit_id(dev, src) == expected
    assert kinds.parse_unit_id(expected) == (dev, src)
    assert kinds.is_unit_id(expected)


@pytest.mark.parametrize("junk", ["30", "u30", "u30-2", "u-0", "u300-0", "u30-0-1", "U30-0", "u30:0", ""])
def test_unit_id_rejects_junk(junk: str) -> None:
    assert kinds.parse_unit_id(junk) is None
    assert not kinds.is_unit_id(junk)


def test_rig_kind_is_derived_from_the_id_prefix() -> None:
    assert kinds.rig_kind("30") == "bilateral"
    assert kinds.rig_kind("u30-0") == "unilateral"


def test_side_for_source_is_left_then_right() -> None:
    # PLAN_msd_management decision H: the sleeve's own source_id names its leg
    assert kinds.side_for_source(0) == "left"
    assert kinds.side_for_source(1) == "right"
    for bad in (2, -1, 255):
        with pytest.raises(ValueError):
            kinds.side_for_source(bad)


def test_unit_config_default_is_unpaired_with_the_wire_side() -> None:
    # decision H (2026-09-23) amends decision G: the default side comes from
    # the unit id's source part instead of being unset
    cfg = kinds.UnitConfig.default("u30-0", 32, 4000)
    assert cfg.rig_id == "u30-0" and not cfg.paired
    assert cfg.side == "left" and cfg.vsrc == 0
    assert cfg.limb_map({1: "thigh", 2: "shin"}) == {(0, 1): "left_thigh", (0, 2): "left_shin"}
    assert cfg.limb_scale() == (1024.0, 8.192)

    right = kinds.UnitConfig.default("u30-1", 32, 4000)
    assert right.side == "right" and right.vsrc == 1
    assert right.limb_map({1: "thigh", 2: "shin"}) == {(1, 1): "right_thigh", (1, 2): "right_shin"}

    # an id that does not parse gets no side rather than a guess
    assert kinds.UnitConfig.default("nonsense", 32, 4000).side is None


def test_unit_config_cleared_side_streams_bare_segments() -> None:
    cfg = kinds.UnitConfig("u30-0", "u30-0", None, 32, 4000)
    assert cfg.vsrc == 0
    assert cfg.limb_map({1: "thigh", 2: "shin"}) == {(0, 1): "thigh", (0, 2): "shin"}


def test_unit_config_sides_drive_names_and_virtual_source() -> None:
    left = kinds.UnitConfig("u30-0", "u30-0", "left", 32, 4000)
    right = kinds.UnitConfig("u31-0", "u30-0", "right", 16, 2000)
    assert left.limb_map({1: "thigh", 2: "shin"}) == {(0, 1): "left_thigh", (0, 2): "left_shin"}
    assert right.limb_map({1: "thigh", 2: "shin"}) == {(1, 1): "right_thigh", (1, 2): "right_shin"}
    assert right.paired and right.vsrc == 1
    assert right.limb_scale() == (2048.0, 16.384)


def test_unit_config_json_round_trip() -> None:
    cfg = kinds.UnitConfig("u31-0", "u30-0", "right", 8, 500)
    data = cfg.to_json()
    assert data == {"unit": "u31-0", "rig": "u30-0", "side": "right",
                    "accel_fs_g": 8, "gyro_fs_dps": 500, "v": kinds.UNIT_CONFIG_VERSION}
    assert kinds.UnitConfig.from_json(data) == cfg


@pytest.mark.parametrize("bad", [
    {"unit": "u30-0", "rig": "u30-0", "side": None, "accel_fs_g": 12, "gyro_fs_dps": 4000},
    {"unit": "u30-0", "rig": "u30-0", "side": None, "accel_fs_g": 32, "gyro_fs_dps": 3000},
    {"unit": "u30-0", "rig": "u30-0", "side": "up", "accel_fs_g": 32, "gyro_fs_dps": 4000},
    {"unit": "30", "rig": "u30-0", "side": None, "accel_fs_g": 32, "gyro_fs_dps": 4000},
    {"unit": "u30-0", "rig": "u30-0", "side": None, "accel_fs_g": 32},
    {"unit": "u30-0", "rig": "u30-0", "side": None, "accel_fs_g": "lots", "gyro_fs_dps": 4000},
])
def test_unit_config_rejects_values_the_pipeline_cannot_use(bad: dict) -> None:
    with pytest.raises(ValueError):
        kinds.UnitConfig.from_json(bad)
