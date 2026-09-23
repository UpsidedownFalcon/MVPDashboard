-- 005: unilateral knee-sleeve units and their dashboard-owned settings
-- (2026-09-23, PLAN_unilateral_devices.md §6).
--
-- A sleeve UNIT is one ESP32-S3 on one leg, identified on the wire by
-- (device_id, source_id) and named 'u<device_id>-<source_id>' (decision D).
-- A RIG is what everything downstream is keyed by (tickers, biomech sessions,
-- `devices` rows, ticks, cards):
--
--   rig_id = unit_id   the unit is UNPAIRED and is its own rig;
--   rig_id != unit_id  the unit is a MEMBER of the host sleeve's rig -- the
--                      host keeps its id and history, the joiner's own rig is
--                      hidden from the device list while paired (decision H).
--
--   side       NULL until an operator sets it (decision G): a side-less unit
--              streams the bare segments 'thigh'/'shin', which biomech treats
--              as neither left nor right. Two side-less members in ONE rig
--              would collide on those names, so the API refuses to clear the
--              side of a paired unit.
--   accel_fs_g / gyro_fs_dps
--              the unit's IMU full-scale, which the datagram does NOT carry
--              (decision B). Seeded per unit from UNILATERAL_ACCEL_FS_G /
--              UNILATERAL_GYRO_FS_DPS at registration and editable per sleeve,
--              so there is deliberately NO DDL default: the operational
--              default lives in .env only (config tier 1).
--
-- Rows are written here by the api and mirrored to Redis (`unit:cfg:{id}`, no
-- TTL) for ingest, which has no DB access. This table is the source of truth.
--
-- No FK to `devices`: same argument as metrics (BACKEND_SCHEMA §1) -- a unit is
-- registered the first time ingest reports it, which is independent of (and can
-- precede) the rig row that the tick writer auto-registers, and a `devices` row
-- pruned later must not delete a sleeve's pairing and full-scale settings.
CREATE TABLE IF NOT EXISTS sleeve_units (
    unit_id        TEXT PRIMARY KEY,          -- 'u30-0'
    wire_device_id SMALLINT NOT NULL,
    wire_source_id SMALLINT NOT NULL,
    rig_id         TEXT NOT NULL,             -- own unit_id when unpaired, host unit_id when paired
    side           TEXT CHECK (side IN ('left','right')),   -- NULL until set (decision G)
    accel_fs_g     SMALLINT NOT NULL,         -- seeded from Settings, no DDL default (rule 5)
    gyro_fs_dps    SMALLINT NOT NULL,
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The visible-rig predicate and the per-rig member lookup both filter on rig_id.
CREATE INDEX IF NOT EXISTS sleeve_units_rig_idx ON sleeve_units (rig_id);
