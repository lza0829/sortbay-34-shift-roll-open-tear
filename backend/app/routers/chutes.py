from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import db
from app.services.common import audit
from app.services.handoff import chute_snapshot, mismatch_bar

router = APIRouter(tags=["口门"])


class ChuteIn(BaseModel):
    分拣线编号: int
    口门码: str = Field(min_length=1)
    标题: str = ""
    软容量: int = 120


@router.get("/口门")
@router.get("/chutes")
def list_chutes():
    with db() as conn:
        return chute_snapshot(conn)


@router.get("/口门/在口")
@router.get("/口门/在口列表")
@router.get("/口门在口")
@router.get("/在口")
@router.get("/chutes/on-mouth")
def chute_on_mouth_list():
    with db() as conn:
        return chute_snapshot(conn)


@router.get("/口门/在口对账")
@router.get("/口门/在口快照")
@router.get("/在口对账")
def chute_reconciliation():
    with db() as conn:
        return {"口门": chute_snapshot(conn), "口门在口": chute_snapshot(conn), "班次错位条": mismatch_bar(conn)}


@router.post("/口门")
def create_chute(body: ChuteIn):
    with db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO chutes(line_id, code, title, status, soft_cap) VALUES (?, ?, ?, '口门开', ?)",
                (body.分拣线编号, body.口门码, body.标题, body.软容量),
            )
        except Exception as e:
            raise HTTPException(400, f"创建失败: {e}")
        chute_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM chutes WHERE id=?", (chute_id,)).fetchone()
        audit(conn, "口门", chute_id, "新建", body.口门码)
        return dict(row)


@router.post("/口门/{chute_id}/开闭")
def set_chute(chute_id: int, 状态: str = Query(...)):
    if 状态 not in ("口门开", "口门闭"):
        raise HTTPException(400, "状态仅允许口门开或口门闭")
    with db() as conn:
        cur = conn.execute("UPDATE chutes SET status=? WHERE id=?", (状态, chute_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "口门不存在")
        audit(conn, "口门", chute_id, "开闭", 状态)
        return dict(conn.execute("SELECT * FROM chutes WHERE id=?", (chute_id,)).fetchone())

