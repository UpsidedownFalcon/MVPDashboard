"""Battery state-of-charge: wire byte -> registry -> ingest:stats -> /api/devices.

The `soc` byte is per-datagram and each `source_id` is a separate leg MCU with
its own battery, so the device-level figure is the MINIMUM across MCUs — a
dying unit must not hide behind a healthy one.
"""

from __future__ import annotations

import numpy as np

from common import packet
from ingest.state import Registry


def _batch(device_id: int, source_id: int, sensor_id: int, soc: int, n: int = 3):
    payloads = [
        packet.encode(device_id, source_id, sensor_id, 1000 + i * 1562,
                      [10, 20, 30, 1, 2, 3], soc=soc)
        for i in range(n)
    ]
    return packet.decode(payloads)


def test_soc_is_tracked_per_source_mcu() -> None:
    reg = Registry()
    reg.route(_batch(30, 0, 1, soc=88), recv_time=1000.0)
    reg.route(_batch(30, 1, 1, soc=42), recv_time=1000.0)
    device = reg.devices[30]
    assert device.soc == {0: 88, 1: 42}


def test_soc_takes_the_newest_datagram_in_a_batch() -> None:
    """A batch holds several samples; the freshest reading wins."""
    reg = Registry()
    payloads = [
        packet.encode(30, 0, 1, 1000 + i * 1562, [1, 2, 3, 4, 5, 6], soc=s)
        for i, s in enumerate((90, 80, 70))
    ]
    reg.route(packet.decode(payloads), recv_time=1000.0)
    assert reg.devices[30].soc[0] == 70


def test_published_soc_is_the_lowest_mcu() -> None:
    """publish.py reports min() so a flat leg unit is what the trainer sees."""
    reg = Registry()
    reg.route(_batch(30, 0, 1, soc=95), recv_time=1000.0)
    reg.route(_batch(30, 1, 2, soc=17), recv_time=1000.0)
    device = reg.devices[30]
    assert min(device.soc.values()) == 17


def test_sd_log_records_carry_no_soc() -> None:
    """decode_log() reads 21-byte records; the byte is UDP-only (TRD §3), so it
    must not be mistaken for a real 0% battery."""
    rec = packet.encode(30, 0, 1, 1000, [1, 2, 3, 4, 5, 6], soc=55)
    batch = packet.decode_log([rec[: packet.LOG_REC_SIZE]])
    assert int(batch.soc[0]) == 0

    reg = Registry()
    reg.route(batch, recv_time=1000.0)
    # routed from a log batch the value is the synthesised 0 -- documented, and
    # why the UI must treat "no datagram seen yet" as unknown rather than empty
    assert reg.devices[30].soc == {0: 0}


def test_soc_survives_a_device_with_no_datagrams_yet() -> None:
    reg = Registry()
    reg.route(_batch(31, 0, 1, soc=64), recv_time=1000.0)
    assert reg.devices[31].soc == {0: 64}
    # a device that has never routed anything has no entry at all
    assert 99 not in reg.devices


def test_decoded_soc_is_a_plain_uint8() -> None:
    batch = _batch(30, 0, 1, soc=200, n=1)
    assert batch.soc.dtype == np.uint8
    assert int(batch.soc[0]) == 200
