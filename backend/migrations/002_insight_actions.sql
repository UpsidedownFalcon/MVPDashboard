-- 002 (S3-T10): action-first insights for the product UI (BACKEND_SCHEMA §1).
-- `action` = short imperative ("Reduce landing volume"); `rationale` = the why,
-- grounded in the measured metrics. Both nullable: rows from pre-refinement
-- rules render `message` as the headline instead (UIUX §4).
ALTER TABLE insights ADD COLUMN IF NOT EXISTS action TEXT;
ALTER TABLE insights ADD COLUMN IF NOT EXISTS rationale TEXT;
