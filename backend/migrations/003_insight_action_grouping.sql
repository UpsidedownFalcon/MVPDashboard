-- 003: group insights into a small set of CURRENT actions (BACKEND_SCHEMA §1).
--
-- Migration 002 gave every insight its own `action` string, which made the feed
-- a list of one-action-per-rule cards. The product needs the inverse: at most a
-- handful of distinct actions on screen, each justified by EVERY rule that
-- currently supports it. Two columns carry that:
--
--   action_id  stable key from the ACTIONS catalogue (api/jobs/insights.py).
--              Several rules deliberately share one -- `load_spike` and
--              `composite_high` both mean "ease off" -- and grouping on this is
--              what turns N insights into <=3 actions with N reasons.
--   reason     ONE short sentence with the numbers, sized to render as a bullet
--              with no expander. `rationale` stays as the long form (citations,
--              caveats) for the audit feed and tooltips.
--
-- Both nullable: rows written before this migration keep rendering via
-- `action`/`rationale`, and /api/insights/current falls back to `action` as its
-- own group key when `action_id` is null.
ALTER TABLE insights ADD COLUMN IF NOT EXISTS action_id TEXT;
ALTER TABLE insights ADD COLUMN IF NOT EXISTS reason    TEXT;

-- /api/insights/current filters on (device_id, created_at) then groups by
-- action_id; the existing insights_device_id_created_at_idx already serves the
-- filter, so no new index is needed for the hold-window scan.
