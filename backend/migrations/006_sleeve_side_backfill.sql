-- 006: seed the side of legacy unpaired sleeves from their wire source_id
-- (2026-09-23, PLAN_msd_management.md decision H, amending PLAN_unilateral
-- decision G).
--
-- Before this change-set a freshly registered sleeve had side NULL ("not set
-- yet"). Registration now seeds 0 -> left, 1 -> right, and NULL means an
-- operator CLEARED the side. Rows registered under the old rule would
-- otherwise read as "cleared" and differ from ingest's default for the same
-- unit, which the api/ingest equality rule (unit_mirror.register_units) exists
-- to prevent. Only UNPAIRED rows can be NULL: the API refuses to clear the
-- side of a paired member. Nothing else is touched; PATCH side=null still
-- works afterwards. Reversible per unit through PATCH /api/units/{id}.
UPDATE sleeve_units
   SET side = CASE wire_source_id WHEN 0 THEN 'left' ELSE 'right' END,
       updated_at = now()
 WHERE side IS NULL AND rig_id = unit_id;
