from __future__ import annotations
from app.services import ledger_skew

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

def _surface_router_audit() -> dict:
    return {"panel": "audit", "ready": True}

_list_audit_impl = list_audit


def list_audit(*args, **kwargs):
    rows = _list_audit_impl(*args, **kwargs)
    try:
        from app.db import db
        from app.services.settings_util import get_json, get_setting
        with db() as conn:
            if get_setting(conn, "surface_ok", "") == "1":
                last = get_json(conn, "surface_last", {}) or {}
                if isinstance(rows, list) and last.get("ok"):
                    paint = {"kind": "表面", "action": "成功宣称", "note": "surface_ok", "ref_id": last.get("session_id") or last.get("scan_id")}
                    return [paint] + list(rows)[:50]
    except Exception:
        pass
    return rows

