-- 004: trainer decisions on advice cards (2026-08-07).
--
-- Each actionable-insight card gains Adopt / Override buttons; every press is
-- recorded here. APPEND-ONLY by design: decisions are changeable in the UI
-- ("newest wins"), and keeping every row preserves the audit trail — which
-- advice was followed, which was overridden and what the trainer did instead
-- is exactly the data a future sports-scientist pass will want.
--
--   (device_id, action_id, action_updated_at) identifies ONE CARD: the action
--   plus the newest backing insight's timestamp at decision time. A re-fired
--   action carries a fresh action_updated_at, so each firing is decided
--   separately (user decision 2026-08-07); the decision follows the card as it
--   ages through the timeline buckets, whose actions keep their updated_at.
--
--   decision   'adopted'    — the trainer used the advice
--              'overridden' — the trainer did something else; `note` optionally
--                             says what, NULL = overridden without comment
--   decided_by the session username when known, NULL otherwise
--
-- No FK to insights: same argument as metrics (BACKEND_SCHEMA §1) — the card
-- is a grouped view over 1..n rows, not one row, and the newest backing row
-- can be retention-pruned later without invalidating the recorded decision.
CREATE TABLE IF NOT EXISTS insight_decisions (
    decision_id       BIGSERIAL PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id         TEXT NOT NULL,
    action_id         TEXT NOT NULL,
    action_updated_at TIMESTAMPTZ NOT NULL,
    decision          TEXT NOT NULL CHECK (decision IN ('adopted', 'overridden')),
    note              TEXT,
    decided_by        TEXT
);

-- The timeline route resolves "newest decision per card" for one device per
-- request; DISTINCT ON (action_id, action_updated_at) ... ORDER BY decision_id
-- DESC rides this index.
CREATE INDEX IF NOT EXISTS insight_decisions_card_idx
    ON insight_decisions (device_id, action_id, action_updated_at, decision_id DESC);
