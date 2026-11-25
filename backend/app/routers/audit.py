from __future__ import annotations

from fastapi import APIRouter

from app.db import db, rows_to_list
from app.services.handoff import replay_opening_events

router = APIRouter(tags=["审计"])


@router.get("/审计")
@router.get("/audit")
def list_audit():
    with db() as conn:
        return rows_to_list(conn.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT 200"
        ).fetchall())


@router.get("/审计/开班重放")
@router.post("/审计/开班重放")
@router.get("/审计/事件重放")
@router.post("/审计/事件重放")
@router.get("/audit/opening-replay")
@router.post("/audit/opening-replay")
@router.get("/audit/events-replay")
@router.post("/audit/events-replay")
def audit_opening_replay(shift_id: int | None = None, 班次编号: int | None = None):
    effective_shift = shift_id if shift_id is not None else 班次编号
    with db() as conn:
        result = replay_opening_events(conn, effective_shift)
        result["审计"] = rows_to_list(conn.execute(
            """
            SELECT * FROM audit_events
            WHERE kind='班次' AND action LIKE '开班%'
            ORDER BY id ASC
            """
        ).fetchall())
        return result

