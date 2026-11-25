from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, Field

from app.db import db
from app.services.common import audit, current_shift, now_str, require_unsealed
from app.services.handoff import (
    divert_batch_views,
    get_scan_view,
    require_current_writable_shift,
)

router = APIRouter(tags=["错分回流"])


class DivertIn(BaseModel):
    扫描编号: int = Field(validation_alias=AliasChoices("扫描编号", "scan_id"))
    备注: str = Field(default="", validation_alias=AliasChoices("备注", "note", "remark"))


@router.get("/回流批次")
@router.get("/divert-batches")
def list_batches():
    with db() as conn:
        return divert_batch_views(conn)


@router.post("/回流批次")
@router.post("/divert-batches")
def create_divert(body: DivertIn):
    with db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        require_current_writable_shift(conn, scan["shift_id"])
        if scan["shift_id"] != shift["id"]:
            raise HTTPException(400, "回流批次班次与扫描班次不一致")
        if scan["status"] not in ("已路由", "已入袋", "集包中"):
            raise HTTPException(400, "仅已路由、已入袋或集包中可标错分回流")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        code = f"回流-{shift['id']}-{stamp}-{uuid4().hex[:8]}"
        cur = conn.execute(
            "INSERT INTO divert_batches(shift_id, code, status, created_at, note) VALUES (?, ?, '回流中', ?, ?)",
            (shift["id"], code, now_str(), body.备注),
        )
        bid = int(cur.lastrowid)
        conn.execute(
            "UPDATE scans SET status='错分回流', divert_batch_id=?, bag_id=NULL WHERE id=?",
            (bid, body.扫描编号),
        )
        if scan["chute_id"]:
            conn.execute(
                """
                INSERT INTO route_events(scan_id, chute_id, action, created_at, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (body.扫描编号, scan["chute_id"], "错分回流", now_str(), code),
            )
        audit(conn, "回流", bid, "建回流批次", str(body.扫描编号))
        return {
            "批次": dict(conn.execute("SELECT * FROM divert_batches WHERE id=?", (bid,)).fetchone()),
            "扫描": get_scan_view(conn, body.扫描编号),
        }


@router.post("/回流批次/{batch_id}/收口")
@router.post("/divert-batches/{batch_id}/close")
def close_batch(batch_id: int):
    with db() as conn:
        batch = conn.execute("SELECT * FROM divert_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(404, "回流批次不存在")
        require_current_writable_shift(conn, batch["shift_id"])
        unresolved = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE divert_batch_id=? AND status='错分回流'",
            (batch_id,),
        ).fetchone()["c"]
        if unresolved:
            raise HTTPException(400, f"仍有 {unresolved} 件错分回流扫描未再路由")
        conn.execute(
            "UPDATE divert_batches SET status='已收口', closed_at=?, close_note=? WHERE id=?",
            (now_str(), "人工确认收口", batch_id),
        )
        audit(conn, "回流", batch_id, "批次收口", "人工确认")
        return dict(conn.execute("SELECT * FROM divert_batches WHERE id=?", (batch_id,)).fetchone())

