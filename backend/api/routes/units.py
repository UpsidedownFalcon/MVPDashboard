"""Sleeve unit endpoints (PLAN_unilateral_devices.md section 6, schema §3).

    GET    /api/units                 the pair picker's list
    PATCH  /api/units/{unit_id}       side and IMU full-scale
    POST   /api/units/{host}/pair     fold a second sleeve into a host rig
    POST   /api/units/{unit_id}/unpair

Every write is ONE database transaction, followed by `mirror_unit` for each
affected unit: the transaction is the truth, Redis is how ingest hears about it
(api/unit_mirror.py). Nothing here touches the pipeline directly -- ingest
reacts to the mirrored config by recreating the affected rigs, which is what
hard-resets their biomech sessions (decision N).
"""

from __future__ import annotations

from typing import Literal

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from api import queries
from api.unit_mirror import mirror_unit
from common.kinds import ACCEL_FS_ALLOWED, GYRO_FS_ALLOWED

router = APIRouter()

Side = Literal["left", "right"]

# Columns mirror_unit() needs, so every UPDATE can RETURN a mirrorable row.
UNIT_ROW = "unit_id, rig_id, side, accel_fs_g, gyro_fs_dps"


class UnitPatchBody(BaseModel):
    """Absent field = leave alone; `side: null` = clear the side (decision G)."""

    side: Side | None = None
    accel_fs_g: int | None = None
    gyro_fs_dps: int | None = None

    @field_validator("accel_fs_g")
    @classmethod
    def _accel_allowed(cls, value: int | None) -> int | None:
        if value is not None and value not in ACCEL_FS_ALLOWED:
            raise ValueError(f"accel_fs_g must be one of {ACCEL_FS_ALLOWED}")
        return value

    @field_validator("gyro_fs_dps")
    @classmethod
    def _gyro_allowed(cls, value: int | None) -> int | None:
        if value is not None and value not in GYRO_FS_ALLOWED:
            raise ValueError(f"gyro_fs_dps must be one of {GYRO_FS_ALLOWED}")
        return value


class PairBody(BaseModel):
    unit_id: str            # the JOINER; the host is the path parameter
    side: Side              # the joiner's side
    # Required only when the host has no side yet; ignored if it matches.
    host_side: Side | None = None


# --- helpers ------------------------------------------------------------------

async def _locked_unit(conn: asyncpg.Connection, unit_id: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        f"SELECT {UNIT_ROW} FROM sleeve_units WHERE unit_id = $1 FOR UPDATE",
        unit_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown unit {unit_id}")
    return row


async def _rig_members(conn: asyncpg.Connection, rig_id: str) -> list[asyncpg.Record]:
    """Every unit in one rig, locked, host first by id order."""
    return list(await conn.fetch(
        f"SELECT {UNIT_ROW} FROM sleeve_units WHERE rig_id = $1 "
        f"ORDER BY unit_id FOR UPDATE",
        rig_id,
    ))


def _side_conflict(members: list[asyncpg.Record], unit_id: str,
                   side: str | None) -> None:
    """409 if another member of the rig already holds `side`.

    Two units on one side would share a virtual source and therefore a limb
    name, and duplicate limb names do not fail loudly: the ticker's `frames`
    dict is keyed by limb, so one sensor overwrites the other and biomech
    rebuilds its session every tick -- zeroing dose, baselines and calibration
    60 times a second (the hazard common/config.py:_limb_names_must_be_unique
    rejects for LIMB_MAP).
    """
    if side is None:
        return
    for other in members:
        if other["unit_id"] != unit_id and other["side"] == side:
            raise HTTPException(
                status_code=409,
                detail=f"side {side} is already taken in rig {other['rig_id']} "
                       f"by {other['unit_id']}",
            )


# --- routes -------------------------------------------------------------------

@router.get("/api/units")
async def list_units(request: Request) -> list[dict]:
    state = request.app.state
    return await queries.units(state.pool, state.redis, state.settings)


@router.patch("/api/units/{unit_id}")
async def patch_unit(unit_id: str, body: UnitPatchBody, request: Request) -> dict:
    """Set a sleeve's side and/or IMU full-scale (decisions B, F, G)."""
    state = request.app.state
    sent = body.model_fields_set
    async with state.pool.acquire() as conn:
        async with conn.transaction():
            row = await _locked_unit(conn, unit_id)
            members = await _rig_members(conn, row["rig_id"])
            side = body.side if "side" in sent else row["side"]
            accel = body.accel_fs_g if "accel_fs_g" in sent else row["accel_fs_g"]
            gyro = body.gyro_fs_dps if "gyro_fs_dps" in sent else row["gyro_fs_dps"]

            # Clearing the side of a unit that shares its rig would leave TWO
            # side-less members, and a side-less unit streams the bare segments
            # "thigh"/"shin" -- so both members would map to the same two limb
            # names. Unpair first; a lone sleeve may be side-less all it likes.
            if side is None and len(members) > 1:
                raise HTTPException(
                    status_code=409,
                    detail="cannot clear the side of a paired unit: both members "
                           "would stream the same limb names (unpair first)",
                )
            _side_conflict(members, unit_id, side)

            if (side, accel, gyro) == (row["side"], row["accel_fs_g"],
                                       row["gyro_fs_dps"]):
                updated = row          # nothing to write, nothing to reset
            else:
                updated = await conn.fetchrow(
                    f"""UPDATE sleeve_units
                        SET side = $2, accel_fs_g = $3, gyro_fs_dps = $4,
                            updated_at = now()
                        WHERE unit_id = $1
                        RETURNING {UNIT_ROW}""",
                    unit_id, side, accel, gyro,
                )
    await mirror_unit(state.redis, updated)
    unit = await queries.unit_one(state.pool, state.redis, state.settings, unit_id)
    assert unit is not None
    return unit


@router.post("/api/units/{host}/pair")
async def pair_unit(host: str, body: PairBody, request: Request) -> dict:
    """Fold `body.unit_id` into `host`'s rig; the host keeps its id and history.

    The joiner's own rig disappears from `/api/devices` for as long as the
    pairing lasts (decision H, queries.visible_rig_predicate) and returns with
    its history on unpair, so nothing is lost and nothing is duplicated.
    """
    state = request.app.state
    joiner_id = body.unit_id
    if joiner_id == host:
        raise HTTPException(status_code=409, detail="a unit cannot be paired with itself")

    async with state.pool.acquire() as conn:
        async with conn.transaction():
            # Lock both rows in id order: two operators pairing the same two
            # sleeves from opposite ends must not deadlock.
            locked = {uid: await _locked_unit(conn, uid)
                      for uid in sorted((host, joiner_id))}
            host_row, joiner = locked[host], locked[joiner_id]

            if host_row["rig_id"] != host:
                raise HTTPException(
                    status_code=409,
                    detail=f"{host} is already paired into rig {host_row['rig_id']}")
            if joiner["rig_id"] != joiner_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"{joiner_id} is already paired into rig {joiner['rig_id']}")

            host_members = await _rig_members(conn, host)
            if any(m["unit_id"] != host for m in host_members):
                raise HTTPException(status_code=409, detail=f"rig {host} is full")
            # The joiner hosting its own pair is "already paired" too: folding
            # it in would orphan its members onto a rig that is no longer theirs.
            if any(m["unit_id"] != joiner_id
                   for m in await _rig_members(conn, joiner_id)):
                raise HTTPException(
                    status_code=409, detail=f"{joiner_id} already hosts a pair")

            host_side = body.host_side if body.host_side is not None else host_row["side"]
            if host_side is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"host {host} has no side yet: pass host_side")
            if host_side == body.side:
                raise HTTPException(
                    status_code=409,
                    detail=f"both units would be {body.side}: a rig needs one per side")

            changed = [await conn.fetchrow(
                f"""UPDATE sleeve_units
                    SET rig_id = $2, side = $3, updated_at = now()
                    WHERE unit_id = $1
                    RETURNING {UNIT_ROW}""",
                joiner_id, host, body.side,
            )]
            if host_side != host_row["side"]:
                changed.append(await conn.fetchrow(
                    f"""UPDATE sleeve_units SET side = $2, updated_at = now()
                        WHERE unit_id = $1 RETURNING {UNIT_ROW}""",
                    host, host_side,
                ))
            # The rig row normally auto-registers from the first tick (writer.py).
            # Pairing is an operator action that names a rig before any tick has
            # to arrive, so make sure the row the response describes exists.
            await conn.execute(
                "INSERT INTO devices (device_id, display_name) VALUES ($1, $1) "
                "ON CONFLICT DO NOTHING",
                host,
            )

    for row in changed:
        await mirror_unit(state.redis, row)
    device = await queries.device_one(state.pool, state.redis, state.settings, host)
    assert device is not None
    return device


@router.post("/api/units/{unit_id}/unpair")
async def unpair_unit(unit_id: str, request: Request) -> dict:
    """Release one member, or every member when called on the host.

    Sides are KEPT: a released sleeve is a one-leg soldier on the side it was
    worn on, which is exactly what the operator set. The response lists the
    whole former rig so the caller can invalidate both rigs' queries.
    """
    state = request.app.state
    async with state.pool.acquire() as conn:
        async with conn.transaction():
            row = await _locked_unit(conn, unit_id)
            rig_id = row["rig_id"]
            rig_rows = await _rig_members(conn, rig_id)
            if rig_id != unit_id:
                released = [row]                      # a member leaves alone
            else:
                released = [r for r in rig_rows if r["unit_id"] != unit_id]
                if not released:
                    raise HTTPException(
                        status_code=409, detail=f"{unit_id} is not paired")
            released_ids = [r["unit_id"] for r in released]
            changed = await conn.fetch(
                f"""UPDATE sleeve_units SET rig_id = unit_id, updated_at = now()
                    WHERE unit_id = ANY($1::text[])
                    RETURNING {UNIT_ROW}""",
                released_ids,
            )
            affected = sorted({r["unit_id"] for r in rig_rows} | {unit_id})

    for changed_row in changed:
        await mirror_unit(state.redis, changed_row)
    return {"units": await queries.units(
        state.pool, state.redis, state.settings, affected)}
