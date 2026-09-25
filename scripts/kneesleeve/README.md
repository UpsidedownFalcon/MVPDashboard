# Knee-sleeve reference scripts (vendored, do not edit)

Copied verbatim on 2026-09-25 from the firmware team's host-side tools in
`C:\Users\bhavy\GitHub_HXSKL\NYKnicks\Knee Sleeve\`:

| File | sha256 | Lines |
|---|---|---|
| `bin2csv.py` | `92b272b3b9ea9a61934fa874a270316030c3c5629f038934268af07f16f18835` | 220 |
| `sensor_stats.py` | `94d79f40b3f34bb2a4cce428d535a6c7849897684a72ef6efdcc6f2a84c3effe` | 1625 |

Do not edit these files; the dashboard's TypeScript port
(`frontend/src/lib/storage/convert/`, agent-docs/03_PLAN_csv_summary.md) is
checked against them. `scripts/storage_goldens.py` runs them over the
fixture logs and records the CSV sha256, the meta sidecar and the summary
text that the frontend's golden tests compare with. If the upstream scripts
change, copy the new versions here, update the hashes above, regenerate the
goldens and fix the port in the same change-set.

## Running them by hand

From the repo root, always through uv (bare `python` on this PC is 3.14):

```
uv run python scripts/kneesleeve/bin2csv.py FILE.BIN -o OUT
uv run --with matplotlib python scripts/kneesleeve/sensor_stats.py OUT/FILE.csv --no-describe --no-plot
```

`bin2csv.py` writes `OUT/FILE.csv` and `OUT/FILE.meta.json`; return code 2
means the file had bad blocks or seq gaps (still converted), 1 an error.
`sensor_stats.py` reads the CSV and its `.meta.json` sidecar (never the
`.TXT`) and prints the statistics; it imports matplotlib at module level,
hence `--with matplotlib` even with `--no-plot` (the first run downloads it,
about 30 s). Set `PYTHONUTF8=1` when capturing their output on Windows:
`bin2csv.py` prints a plus-minus sign and an em dash that otherwise come out
in cp1252.
