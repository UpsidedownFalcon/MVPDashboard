"""Regenerate the sleeve-storage decoder goldens with the vendored scripts.

Run from the repo root:

    uv run --with matplotlib python scripts/storage_goldens.py
    uv run --with matplotlib python scripts/storage_goldens.py sync_mid kitchen

Why this exists
---------------
The dashboard converts every transferred LOG_NNNN.BIN to a CSV, a meta
sidecar and a summary in the browser (agent-docs/03_PLAN_csv_summary.md).
The CSV must be byte-exact with `scripts/kneesleeve/bin2csv.py` and the
summary must reproduce `scripts/kneesleeve/sensor_stats.py` (both vendored
verbatim, see scripts/kneesleeve/README.md). This script runs the two
reference scripts over the fixture logs and records what they produce; the
frontend's golden tests (frontend/src/lib/storage/convert/goldens.test.ts
and the summary tests) compare the TypeScript port against these files.

What it does
------------
For every fixture BIN, `frontend/src/lib/storage/fixtures/LOG_0010.head64.bin`
(the real firmware 1.1.0 head) plus `frontend/src/lib/storage/fixtures/convert/*.bin`
(synthetic, written by convert/fixtures.write.test.ts), sorted by name:

1. copies the file unchanged, under its own name, into a temp dir;
2. runs `bin2csv.py <file> -o <tmp>`; return code 2 is normal (bad blocks or
   seq gaps, still converted), 1 is an error and fails this script; a
   fixture with no IMU rows produces no CSV, gets no goldens and is reported;
3. runs `sensor_stats.py <tmp>/<name>.csv --no-describe --no-plot` with the
   same interpreter (so `uv run --with matplotlib` applies to it too);
4. writes into `frontend/src/lib/storage/fixtures/convert/`:
     <name>.csv.sha256   sha256 of the CSV bytes, lowercase hex plus LF
     <name>.meta.json    bin2csv's sidecar, CRLF -> LF, otherwise untouched
     <name>.summary.txt  sensor_stats' stdout decoded as UTF-8, CRLF -> LF,
                         the single line starting `File: ` replaced by
                         `File: <name>.csv`; nothing else changed (trailing
                         spaces and the final blank line included)

`<name>` is the file name without `.bin` (`LOG_0010.head64`, `sync_mid`).
Running it twice must leave `git status` unchanged.

Tunables
--------
sensor_stats.py's defaults, gap 1 ms (`--gap-ms`), noise above 20 Hz
(`--noise-above-hz`) and timestamp outlier 1 s (`--ts-outlier-ms`), mirror
STORAGE_GAP_US, STORAGE_NOISE_F_CUT_HZ and STORAGE_TS_OUTLIER_US in
frontend/src/lib/config.ts. They must change together: pass the new values
on the sensor_stats command line here AND change config.ts, then regenerate.

Output capture uses PYTHONUTF8=1: bin2csv prints a non-ASCII plus-minus sign
and an em dash that would otherwise come out in cp1252 on Windows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KNEESLEEVE = REPO_ROOT / "scripts" / "kneesleeve"
BIN2CSV = KNEESLEEVE / "bin2csv.py"
SENSOR_STATS = KNEESLEEVE / "sensor_stats.py"
FIXTURES = REPO_ROOT / "frontend" / "src" / "lib" / "storage" / "fixtures"
CONVERT_FIXTURES = FIXTURES / "convert"
REAL_HEAD = FIXTURES / "LOG_0010.head64.bin"

# sensor_stats.py options; its defaults mirror the STORAGE_* tunables in
# frontend/src/lib/config.ts (see the docstring).
SENSOR_STATS_ARGS = ["--no-describe", "--no-plot"]
BIN2CSV_OK_CODES = (0, 2)
FILE_LINE_PREFIX = "File: "


def run_py(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run([sys.executable, *args], cwd=str(cwd), env=env, capture_output=True)


def stderr_text(proc: subprocess.CompletedProcess[bytes]) -> str:
    return proc.stderr.decode("utf-8", "replace")


def fixture_name(bin_path: Path) -> str:
    """File name without `.bin`: LOG_0010.head64.bin -> LOG_0010.head64."""
    if bin_path.suffix.lower() != ".bin":
        raise SystemExit(f"{bin_path} is not a .bin fixture")
    return bin_path.name[: -len(bin_path.suffix)]


def rewrite_file_line(summary: str, name: str) -> str:
    lines = summary.split("\n")
    hits = [i for i, line in enumerate(lines) if line.startswith(FILE_LINE_PREFIX)]
    if len(hits) != 1:
        raise SystemExit(f"{name}: expected exactly one '{FILE_LINE_PREFIX}' line in the summary, found {len(hits)}")
    lines[hits[0]] = f"{FILE_LINE_PREFIX}{name}.csv"
    return "\n".join(lines)


def golden(bin_path: Path, tmp: Path) -> dict | None:
    name = fixture_name(bin_path)
    work = tmp / name
    work.mkdir()
    shutil.copyfile(bin_path, work / bin_path.name)

    conv = run_py([str(BIN2CSV), str(work / bin_path.name), "-o", str(work)], cwd=work)
    if conv.returncode not in BIN2CSV_OK_CODES:
        raise SystemExit(f"bin2csv.py failed on {bin_path.name} (rc {conv.returncode}):\n{stderr_text(conv)}")
    csv_path = work / f"{name}.csv"
    meta_path = work / f"{name}.meta.json"
    if not csv_path.exists():
        return None
    if not meta_path.exists():
        raise SystemExit(f"bin2csv.py wrote {csv_path.name} but no {meta_path.name}")

    sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    meta_text = meta_path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    meta = json.loads(meta_text)

    stats = run_py([str(SENSOR_STATS), str(csv_path), *SENSOR_STATS_ARGS], cwd=work)
    if stats.returncode != 0:
        raise SystemExit(f"sensor_stats.py failed on {csv_path.name} (rc {stats.returncode}):\n{stderr_text(stats)}")
    summary = rewrite_file_line(stats.stdout.decode("utf-8").replace("\r\n", "\n"), name)

    CONVERT_FIXTURES.mkdir(parents=True, exist_ok=True)
    (CONVERT_FIXTURES / f"{name}.csv.sha256").write_bytes(f"{sha}\n".encode("ascii"))
    (CONVERT_FIXTURES / f"{name}.meta.json").write_bytes(meta_text.encode("utf-8"))
    (CONVERT_FIXTURES / f"{name}.summary.txt").write_bytes(summary.encode("utf-8"))
    return dict(
        name=name,
        sha=sha,
        valid=meta["blocks_valid"],
        bad=meta["blocks_bad"],
        gaps=meta["seq_gaps"],
        rows=sum(meta["rows"].values()),
        rc=conv.returncode,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*",
                    help="fixture names to regenerate, e.g. sync_mid (default: all)")
    args = ap.parse_args()

    for script in (BIN2CSV, SENSOR_STATS):
        if not script.exists():
            raise SystemExit(f"missing {script}; see scripts/kneesleeve/README.md")
    bins = [REAL_HEAD] + sorted(CONVERT_FIXTURES.glob("*.bin"))
    if args.names:
        wanted = set(args.names)
        bins = [b for b in bins if fixture_name(b) in wanted]
        missing = wanted - {fixture_name(b) for b in bins}
        if missing:
            raise SystemExit(f"no fixture named: {', '.join(sorted(missing))}")
    if not bins:
        raise SystemExit("no fixture BIN files found")

    print(f"python {sys.version.split()[0]}  fixtures -> {CONVERT_FIXTURES.relative_to(REPO_ROOT)}")
    print(f"{'name':<18} {'csv sha256':<64} {'valid':>5} {'bad':>4} {'gaps':>4} {'rows':>7}  rc")
    with tempfile.TemporaryDirectory(prefix="storage_goldens_") as tmp:
        for bin_path in bins:
            row = golden(bin_path, Path(tmp))
            if row is None:
                print(f"{fixture_name(bin_path):<18} (no IMU rows: bin2csv wrote no CSV, no goldens written)")
                continue
            print(f"{row['name']:<18} {row['sha']:<64} {row['valid']:>5} {row['bad']:>4} "
                  f"{row['gaps']:>4} {row['rows']:>7}  {row['rc']}")


if __name__ == "__main__":
    main()
