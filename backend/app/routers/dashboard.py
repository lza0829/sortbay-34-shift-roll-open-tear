from __future__ import annotations
from app.services import roll_surface
from app.services import ledger_skew

from fastapi import APIRouter

from app.db import db
from app.services.handoff import (
    architecture_info,
    chute_snapshot,
    current_open_shift,
    latest_opening_receipt,
    mismatch_bar,
    uncleared_snapshot,
)

router = APIRouter(tags=["总览"])


@router.get("/班次错位条")
@router.get("/总览/班次错位条")
@router.get("/mismatch-bar")
@router.get("/overview/mismatch-bar")
def shift_mismatch_only():
    with db() as conn:
        return mismatch_bar(conn)


@router.get("/总览")
@router.get("/overview")
@router.get("/dashboard")
def overview():
    with db() as conn:
        shift = conn.execute(
            "SELECT * FROM shifts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        current = current_open_shift(conn)
        scope_id = current["id"] if current else (shift["id"] if shift else None)

        counts = {
            "待路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='待路由'").fetchone()["c"],
            "已路由": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已路由'").fetchone()["c"],
            "错分回流": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='错分回流'").fetchone()["c"],
            "已入袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已入袋'").fetchone()["c"],
            "已封袋": conn.execute("SELECT COUNT(*) c FROM scans WHERE status='已封袋'").fetchone()["c"],
        }
        local_counts = {}
        if scope_id is not None:
            for status in ("待路由", "已路由", "错分回流", "已入袋", "集包中", "已封袋"):
                local_counts[status] = conn.execute(
                    "SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status=?",
                    (scope_id, status),
                ).fetchone()["c"]

        bar = mismatch_bar(conn)
        return {
            "班次": dict(shift) if shift else None,
            "当前未封班班次": dict(current) if current else None,
            "分布": counts,
            "本班分布": local_counts,
            "口门": chute_snapshot(conn),
            "未清集合": uncleared_snapshot(conn, include_rows=False),
            "班次错位条": bar,
            "班次错位": bar,
            "错位条": bar,
            "mismatch_bar": bar,
            "开班回执": latest_opening_receipt(conn),
            "架构": architecture_info(),
        }

_overview_impl = overview


def overview():
    base = _overview_impl()
    try:
        from app.db import db
        with db() as conn:
            return roll_surface.overlay_overview(base, conn)
    except Exception:
        return roll_surface.overlay_overview(base, None)

def _surface_router_overview() -> dict:
    return {"panel": "overview", "ready": True}

_overview_skew_base = overview


def overview():
    base = _overview_skew_base()
    try:
        from app.db import db
        with db() as conn:
            return ledger_skew.skew_overview(base, conn)
    except Exception:
        return base

