#!/usr/bin/env python3
"""S1-T13 stage-1 validation matrix — automates STAGE1.md scenarios 1-5.

Run from the repo root with services up and the redis debug port exposed:
    docker compose --profile debug up -d
    uv run python scripts/validate_stage1.py            # full: ~17-20 min total
    uv run python scripts/validate_stage1.py --quick    # shortened durations, ~4 min

NOT read-only: scenario 5 stops/starts the `api` container and restarts `redis`
to prove ingest survives both. Do not run it against a stack in use.

Scenario 6 (real wearables) needs hardware and is documented in the README.
Results print as a PASS/FAIL report; exit code 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import redis as sync_redis

REPO_ROOT = Path(__file__).resolve().parents[1]
SIM = REPO_ROOT / "simulator" / "simulate.py"
REDIS_URL = "redis://127.0.0.1:6379/0"

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout


def start_sim(*extra: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(SIM), *extra],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def rstats(r: sync_redis.Redis) -> dict[str, str]:
    return {k.decode(): v.decode() for k, v in r.hgetall("ingest:stats").items()}


def ingest_mem_mb() -> float:
    out = sh("docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}")
    for line in out.splitlines():
        if "ingest" in line:
            usage = line.split()[1]  # e.g. 123.4MiB
            val = float("".join(c for c in usage if (c.isdigit() or c == ".")))
            return val * (1024 if "GiB" in usage else 1)
    return -1.0


def ingest_container_id() -> str:
    return sh("docker", "compose", "ps", "-q", "ingest").strip()


def sample_loop(r, devices, duration_s, interval_s=10.0):
    """Collect per-device rate + quality + counters over the window.

    The simulator MUST still be running for the whole window (callers start it
    with extra margin and terminate it afterwards) — otherwise the tail samples
    catch the offline decay and poison the averages.
    Rates are window averages of ticks_out (instantaneous 1s counts quantize ±1).
    """
    quals, mems = {d: [] for d in devices}, []
    st0 = rstats(r)
    t0 = time.time()
    t_end = t0 + duration_s
    while time.time() < t_end:
        time.sleep(min(interval_s, max(t_end - time.time(), 0.1)))
        st = rstats(r)
        for d in devices:
            if f"dev:{d}:quality" in st:
                quals[d].append(float(st[f"dev:{d}:quality"]))
        mems.append(ingest_mem_mb())
    st1 = rstats(r)
    elapsed = time.time() - t0
    avg_rate = {
        d: (int(st1.get(f"dev:{d}:ticks_out", 0)) - int(st0.get(f"dev:{d}:ticks_out", 0)))
        / elapsed
        for d in devices
    }
    return avg_rate, quals, mems, st1


def end_sim(sim: subprocess.Popen) -> None:
    if sim.poll() is None:
        sim.terminate()
    try:
        sim.wait(timeout=10)
    except subprocess.TimeoutExpired:
        sim.kill()


def counters_sum(st: dict[str, str], suffix: str) -> int:
    return sum(int(v) for k, v in st.items() if k.endswith(suffix))


# --- scenarios -----------------------------------------------------------------

def scenario1_clean(r, duration_s: float) -> None:
    print(f"\n== S1: clean run, 5 devices, {duration_s:.0f}s ==", flush=True)
    devices = [str(30 + i) for i in range(5)]
    sim = start_sim("--devices", "5", "--seed", "101",
                    "--duration", str(duration_s + 40))
    try:
        time.sleep(8)  # warm-up
        rates, quals, mems, st = sample_loop(r, devices, duration_s)
    finally:
        end_sim(sim)

    ok = all(59.5 <= x <= 60.5 for x in rates.values())
    record("S1 tick rate 60±0.5 all devices", ok,
           " ".join(f"dev{d}={x:.2f}" for d, x in rates.items()))
    crc = int(st.get("global:crc_fail", "-1"))
    record("S1 crc_fail == 0", crc == 0, f"crc_fail={crc}")
    late = counters_sum(st, ":late_drop")
    recv = counters_sum(st, ":recv")
    record("S1 late_drop ~ 0", late <= max(recv * 0.001, 50),
           f"late={late} recv={recv} ({100 * late / max(recv, 1):.3f}%)")
    mems = [m for m in mems if m > 0]
    third = max(len(mems) // 3, 1)
    early, lately = statistics.mean(mems[:third]), statistics.mean(mems[-third:])
    record("S1 memory flat", lately < early * 1.2 + 10,
           f"early={early:.0f}MiB late={lately:.0f}MiB")


def scenario2_impairment(r, duration_s: float) -> None:
    print(f"\n== S2: loss 10% + reorder 5% + jitter 20ms, 5 devices, {duration_s:.0f}s ==",
          flush=True)
    devices = [str(30 + i) for i in range(5)]
    st_before = rstats(r)
    sim = start_sim("--devices", "5", "--seed", "102", "--loss", "10",
                    "--reorder", "5", "--jitter", "20",
                    "--duration", str(duration_s + 40))
    try:
        time.sleep(8)
        rates, quals, mems, st = sample_loop(r, devices, duration_s)
    finally:
        end_sim(sim)

    allq = [x for d in devices for x in quals[d]]
    qavg = statistics.mean(allq) if allq else 0.0
    record("S2 quality ≈ 0.90", 0.82 <= qavg <= 0.97, f"avg={qavg:.3f}")
    record("S2 tick rate steady", all(59.5 <= x <= 60.5 for x in rates.values()),
           " ".join(f"dev{d}={x:.2f}" for d, x in rates.items()))
    late = counters_sum(st, ":late_drop") - counters_sum(st_before, ":late_drop")
    recv = counters_sum(st, ":recv") - counters_sum(st_before, ":recv")
    record("S2 no counter runaway", late < recv * 0.05,
           f"late={late} recv={recv} ({100 * late / max(recv, 1):.2f}%)")


def scenario3_drift(r, duration_s: float) -> None:
    print(f"\n== S3: drift 500ppm, 2 devices, {duration_s:.0f}s ==", flush=True)
    devices = ["30", "31"]
    sim = start_sim("--devices", "2", "--seed", "103", "--drift", "500",
                    "--duration", str(duration_s + 40))
    try:
        time.sleep(8)
        # start counting resets only now: reusing device ids from a previous
        # scenario correctly fires the reboot detector once per source at t=0
        since = time.strftime("%Y-%m-%dT%H:%M:%S")
        rates, quals, mems, st = sample_loop(r, devices, duration_s)
    finally:
        end_sim(sim)

    logs = sh("docker", "compose", "logs", "ingest", "--since", since)
    resets = sum(1 for line in logs.splitlines() if "clock reset" in line)
    record("S3 no clock resets", resets == 0, f"resets={resets}")
    allq = [x for d in devices for x in quals[d]]
    qavg = statistics.mean(allq) if allq else 0.0
    record("S3 legs stay paired (quality ~1)", qavg >= 0.97, f"avg quality={qavg:.3f}")


def scenario4_device_churn(r) -> None:
    print("\n== S4: kill/restart a device repeatedly ==", flush=True)
    ps = r.pubsub()
    ps.subscribe("ticks")

    def flush() -> None:
        # discard everything buffered so a window only sees fresh messages
        while ps.get_message(timeout=0.05) is not None:
            pass

    def drain_devs(window_s: float, fresh: bool = False) -> set[str]:
        if fresh:
            flush()
        seen: set[str] = set()
        t_end = time.time() + window_s
        while time.time() < t_end:
            msg = ps.get_message(timeout=0.2)
            if msg and msg["type"] == "message":
                seen.add(json.loads(msg["data"])["dev"])
        return seen

    ok_offline, ok_online = True, True
    for cycle in range(3):
        sim = start_sim("--devices", "1", "--base-id", "40", "--seed",
                        str(110 + cycle), "--duration", "8")
        time.sleep(6)
        alive = drain_devs(2.0)
        ok_online = ok_online and ("40" in alive)
        sim.wait(timeout=20)          # device "killed"
        time.sleep(3.5)               # > OFFLINE_AFTER_S: ticker must be suspended
        silent = drain_devs(2.5, fresh=True)
        ok_offline = ok_offline and ("40" not in silent)
    record("S4 device online while streaming", ok_online, "3 cycles")
    record("S4 no stale ticks while offline", ok_offline, "3 cycles")


def scenario5_service_kill(r) -> None:
    print("\n== S5: kill/restart api and redis ==", flush=True)
    sim = start_sim("--devices", "2", "--seed", "120", "--duration", "75")
    try:
        time.sleep(6)
        container_before = ingest_container_id()

        # kill api: ingest must keep ticking
        sh("docker", "compose", "stop", "api")
        t0 = int(rstats(r).get("dev:30:ticks_out", "0"))
        time.sleep(6)
        t1 = int(rstats(r).get("dev:30:ticks_out", "0"))
        sh("docker", "compose", "start", "api")
        record("S5 ingest unaffected by api kill", t1 - t0 >= 300,
               f"ticks advanced {t1 - t0} in 6s while api down")

        # kill redis: ingest must survive and reconnect
        sh("docker", "compose", "restart", "redis")
        time.sleep(8)
        t2 = int(rstats(r).get("dev:30:ticks_out", "0"))
        time.sleep(6)
        t3 = int(rstats(r).get("dev:30:ticks_out", "0"))
        container_after = ingest_container_id()
        record("S5 ingest survives redis restart",
               container_after == container_before and t3 > t2,
               f"container unchanged={container_after == container_before}, "
               f"ticks resumed +{t3 - t2}")
    finally:
        sim.wait(timeout=90)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="short durations (smoke) instead of the full matrix")
    args = parser.parse_args()

    s1 = 90 if args.quick else 600
    s2 = 45 if args.quick else 90
    s3 = 45 if args.quick else 120

    r = sync_redis.from_url(REDIS_URL, socket_connect_timeout=2)
    try:
        r.ping()
    except Exception:
        print("ERROR: redis not reachable on 127.0.0.1:6379 — "
              "run: docker compose --profile debug up -d")
        return 2

    print(f"stage-1 validation matrix ({'quick' if args.quick else 'full'} mode)")
    scenario1_clean(r, s1)
    scenario2_impairment(r, s2)
    scenario3_drift(r, s3)
    scenario4_device_churn(r)
    scenario5_service_kill(r)

    print("\n==== SUMMARY ====")
    failed = 0
    for name, ok, detail in RESULTS:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        failed += 0 if ok else 1
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
