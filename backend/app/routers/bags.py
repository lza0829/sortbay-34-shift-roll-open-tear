from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, Field

from app.db import db
from app.services.common import audit, current_shift, require_unsealed
from app.services.handoff import get_scan_view, require_current_writable_shift

router = APIRouter(tags=["封袋"])


class BagOpenIn(BaseModel):
    口门编号: int = Field(validation_alias=AliasChoices("口门编号", "chute_id"))
    备注: str = Field(default="", validation_alias=AliasChoices("备注", "note", "remark"))


class BagAddIn(BaseModel):
    扫描编号: int = Field(validation_alias=AliasChoices("扫描编号", "scan_id"))


def _bag_views(conn):
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT b.*, c.code AS 口门码, sh.business_day, sh.name AS 班次名,
                   (SELECT COUNT(*) FROM scans s WHERE s.bag_id=b.id) AS 件数,
                   (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1) AS current_shift_id,
                   CASE
                     WHEN (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1) IS NOT NULL
                      AND b.shift_id <> (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1)
                      AND b.status='集包中'
                       THEN '错班集包袋'
                     ELSE ''
                   END AS 错班标
            FROM bags b
            LEFT JOIN chutes c ON c.id=b.chute_id
            LEFT JOIN shifts sh ON sh.id=b.shift_id
            ORDER BY b.id DESC LIMIT 10000
            """
        ).fetchall()
    ]
    for row in rows:
        row["错班"] = bool(row.get("错班标"))
        row["shift_mismatch"] = bool(row.get("错班标"))
    return rows


@router.get("/封袋")
@router.get("/袋册")
@router.get("/bags")
def list_bags():
    with db() as conn:
        return _bag_views(conn)


@router.post("/封袋/开袋")
@router.post("/bags/open")
def open_bag(body: BagOpenIn):
    with db() as conn:
        require_unsealed(conn)
        shift = current_shift(conn)
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (body.口门编号,)).fetchone()
        if not chute:
            raise HTTPException(404, "口门不存在")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        code = f"袋-{chute['code']}-{shift['id']}-{stamp}-{uuid4().hex[:8]}"
        cur = conn.execute(
            "INSERT INTO bags(shift_id, chute_id, code, status, note) VALUES (?, ?, ?, '集包中', ?)",
            (shift["id"], body.口门编号, code, body.备注),
        )
        bag_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        audit(conn, "封袋", bag_id, "开袋", code)
        return dict(row)


@router.post("/封袋/{bag_id}/入袋")
@router.post("/bags/{bag_id}/add")
def add_to_bag(bag_id: int, body: BagAddIn):
    with db() as conn:
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        if not bag:
            raise HTTPException(404, "袋不存在")
        require_current_writable_shift(conn, bag["shift_id"])
        if bag["status"] != "集包中":
            raise HTTPException(400, "袋已封或已撤销，禁止再入")
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (body.扫描编号,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        if scan["shift_id"] != bag["shift_id"]:
            raise HTTPException(400, "扫描班次与袋册班次不一致，禁止跨班入袋")
        if scan["status"] != "已路由":
            raise HTTPException(400, "仅已路由件可入袋")
        if scan["chute_id"] != bag["chute_id"]:
            raise HTTPException(400, "扫描口门与袋口门不一致")
        cur = conn.execute(
            """
            UPDATE scans SET status='已入袋', bag_id=?
            WHERE id=? AND status='已路由' AND chute_id=? AND shift_id=?
            """,
            (bag_id, body.扫描编号, bag["chute_id"], bag["shift_id"]),
        )
        if cur.rowcount != 1:
            raise HTTPException(409, "入袋并发冲突，请刷新扫描册后重试")
        audit(conn, "封袋", bag_id, "入袋", str(body.扫描编号))
        return get_scan_view(conn, body.扫描编号)


@router.post("/封袋/{bag_id}/封口")
@router.post("/bags/{bag_id}/seal")
def seal_bag(bag_id: int):
    with db() as conn:
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        if not bag:
            raise HTTPException(404, "袋不存在")
        require_current_writable_shift(conn, bag["shift_id"])
        if bag["status"] != "集包中":
            raise HTTPException(400, "袋状态不可封口")
        total_n = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE bag_id=?", (bag_id,)
        ).fetchone()["c"]
        if total_n == 0:
            raise HTTPException(400, "空袋不可封口；如为误开请调用撤销空袋")
        n = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE bag_id=? AND status IN ('已入袋', '集包中')",
            (bag_id,),
        ).fetchone()["c"]
        conn.execute(
            "UPDATE bags SET status='已封袋', sealed_at=? WHERE id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), bag_id),
        )
        conn.execute(
            "UPDATE scans SET status='已封袋' WHERE bag_id=? AND status IN ('已入袋', '集包中')",
            (bag_id,),
        )
        audit(conn, "封袋", bag_id, "封口", f"件数{total_n}")
        return dict(conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone())


@router.post("/封袋/{bag_id}/撤销空袋")
@router.post("/封袋/{bag_id}/撤销")
@router.post("/封袋/{bag_id}/取消")
@router.post("/封袋/{bag_id}/作废空袋")
@router.post("/bags/{bag_id}/cancel-empty")
@router.post("/bags/{bag_id}/cancel")
def cancel_empty_bag(bag_id: int):
    with db() as conn:
        bag = conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
        if not bag:
            raise HTTPException(404, "袋不存在")
        require_current_writable_shift(conn, bag["shift_id"])
        if bag["status"] != "集包中":
            raise HTTPException(400, "仅集包中袋可撤销")
        n = conn.execute("SELECT COUNT(*) c FROM scans WHERE bag_id=?", (bag_id,)).fetchone()["c"]
        if n:
            raise HTTPException(400, "袋内有扫描，必须先封袋或回流，不能撤销")
        conn.execute(
            "UPDATE bags SET status='已撤销', sealed_at=? WHERE id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), bag_id),
        )
        audit(conn, "封袋", bag_id, "撤销空袋", "")
        return dict(conn.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone())

