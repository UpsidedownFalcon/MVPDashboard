# agent-docs: context and plans written by or for AI coding agents

Read in this order. Numbers give the chronology of the work; each plan file carries its
own dates, decisions table and "As built" section (code wins over any plan).

| # | File | What it is | Dates |
|---|---|---|---|
| 00 | [00_PROJECT_CONTEXT.md](00_PROJECT_CONTEXT.md) | The project brief every session needs: what the system is, current state, stack and layout, build/test/run commands, config tiers, frozen interfaces, hard rules, the rig model, the sleeve on-card storage facts, known doc drift. Imported into context by the root `CLAUDE.md` via `AGENTS.md`. | discovered 2026-09-23, kept current |
| 01 | [01_PLAN_unilateral_devices.md](01_PLAN_unilateral_devices.md) | Plan of record for the unilateral knee-sleeve change-set: second wearable kind (0xA6), units and rigs, dashboard-driven pairing, side and full scale, migration 005, `/api/units`. Decisions A-N, work packages, as-built. | approved and shipped 2026-09-23 |
| 02 | [02_PLAN_msd_management.md](02_PLAN_msd_management.md) | Plan of record for sleeve storage change-set 1: managing the sleeve's HIPPOSDATA USB drive from the dashboard (CONFIG.TXT editor, verified log transfer). Decisions A-N, verified firmware and browser facts, engine invariants, as-built, manual checklist. Its section 5 is the first sketch of change-set 2, superseded by 03. | approved and CS1 built 2026-09-23 |
| 03 | [03_PLAN_csv_summary.md](03_PLAN_csv_summary.md) | Plan of record for sleeve storage change-set 2: each transferred LOG_NNNN.BIN becomes, in a Web Worker beside raw/, a CSV byte-exact with bin2csv.py, a meta.json and a plain-text summary ported from sensor_stats.py, shown in the "CSV and summary" card with Convert missing and Retry. Decisions O-V, assumptions A1-A7, design 4.1-4.8, goldens, as-built. | approved and built 2026-09-25 |

Earlier planning, before this folder existed, is part of the documentation suite and
stays there because everything cross-references it: `docs/PLAN.md` (the anchor
snapshot, 2026-08-02), `docs/IMPLEMENTATION_PLAN.md` (build order and change-set
index) and `docs/tasks/STAGE1.md` to `STAGE4.md` (stages 1-3 shipped 2026-08-03, stage 4
demo frontend 2026-09-12).

Conventions:

- A new change-set gets the next number here (`03_PLAN_<feature>.md`), a bullet in
  `docs/PLAN.md`'s status block and a row in `docs/IMPLEMENTATION_PLAN.md`.
- Decisions are never rewritten; a reopened decision gets a dated amendment.
  Deviations found while building go in that plan's "As built" section.
- `00_PROJECT_CONTEXT.md` is refreshed at the end of every change-set and must stay
  under about 150 lines; it is loaded into every session.
- Root `CLAUDE.md` and `AGENTS.md` are pointers only; do not put content there.
