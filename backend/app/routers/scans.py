from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, Field

from app.db import db
from app.services.common import audit, now_str, require_unsealed
from app.services.handoff import (
    get_scan_view,
    list_scan_views,
    require_current_writable_shift,
    uncleared_snapshot,
)

router = APIRouter(tags=["过机扫描"])


class ScanIn(BaseModel):
    条码: str = Field(
        min_length=1,
        validation_alias=AliasChoices("条码", "barcode", "code"),
    )
    目的地码: str = Field(
        default="",
        validation_alias=AliasChoices("目的地码", "dest_code", "destination_code", "destination"),
    )
    分拣线编号: int | None = Field(
        default=None,
        validation_alias=AliasChoices("分拣线编号", "line_id"),
    )
    备注: str = Field(default="", validation_alias=AliasChoices("备注", "note", "remark"))


@router.get("/扫描")
@router.get("/扫描册")
@router.get("/scans")
def list_scans(
    状态: str | None = None,
    status: str | None = None,
    班次编号: int | None = None,
    shift_id: int | None = None,
    未清: bool = False,
    unclear: bool = False,
):
    effective_status = 状态 or status
    effective_shift = 班次编号 if 班次编号 is not None else shift_id
    effective_unclear = 未清 or unclear
    with db() as conn:
        return list_scan_views(
            conn,
            status=effective_status,
            shift_id=effective_shift,
            only_unclear=effective_unclear,
        )


@router.get("/扫描/未清集合")
@router.get("/未清集合")
@router.get("/未清扫描")
def list_uncleared_scans(班次编号: int | None = None, shift_id: int | None = None):
    effective_shift = 班次编号 if 班次编号 is not None else shift_id
    with db() as conn:
        return uncleared_snapshot(conn, effective_shift)


@router.get("/扫描/未清列表")
@router.get("/未清扫描列表")
def list_uncleared_scan_rows(班次编号: int | None = None, shift_id: int | None = None):
    effective_shift = 班次编号 if 班次编号 is not None else shift_id
    with db() as conn:
        return uncleared_snapshot(conn, effective_shift)["扫描"]


@router.post("/扫描")
@router.post("/scans")
def create_scan(body: ScanIn):
    with db() as conn:
        require_unsealed(conn)
        from app.services.common import current_shift

        shift = current_shift(conn)
        line = None
        if body.分拣线编号:
            line = conn.execute("SELECT * FROM lines WHERE id=?", (body.分拣线编号,)).fetchone()
        else:
            line = conn.execute("SELECT * FROM lines WHERE active=1 ORDER BY id LIMIT 1").fetchone()
        if not line:
            raise HTTPException(400, "无可用分拣线")
        if not line["active"]:
            raise HTTPException(400, "分拣线已停用")
        dup = conn.execute(
            "SELECT id FROM scans WHERE barcode=? AND shift_id=?",
            (body.条码, shift["id"]),
        ).fetchone()
        if dup:
            raise HTTPException(400, "本班次条码已过机")
        cur = conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, scanned_at, note)
            VALUES (?, ?, ?, ?, '待路由', ?, ?)
            """,
            (body.条码, body.目的地码, shift["id"], line["id"], now_str(), body.备注),
        )
        scan_id = int(cur.lastrowid)
        audit(conn, "扫描", scan_id, "过机装载", body.条码)
        return get_scan_view(conn, scan_id)


@router.post("/扫描/{scan_id}/路由")
@router.post("/scans/{scan_id}/route")
def route_scan(scan_id: int):
    with db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if not scan:
            raise HTTPException(404, "扫描不存在")
        # Opening under the blocking architecture seals the old shift only
        # after every old scan has been cleared.  This gate also makes a lost
        # race or twisted fixture fail loudly instead of growing an old mouth.
        require_current_writable_shift(conn, scan["shift_id"])
        if scan["status"] not in ("待路由", "错分回流"):
            raise HTTPException(400, f"当前状态不可路由: {scan['status']}")
        if not scan["dest_code"]:
            raise HTTPException(400, "缺少目的地码")
        rule = conn.execute(
            "SELECT * FROM route_rules WHERE dest_code=?", (scan["dest_code"],)
        ).fetchone()
        if not rule:
            raise HTTPException(400, "无匹配路由")
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (rule["chute_id"],)).fetchone()
        if not chute:
            raise HTTPException(400, "口门不存在")
        if chute["status"] != "口门开":
            raise HTTPException(400, "口门已闭，禁止落入")
        conn.execute(
            "UPDATE scans SET status='已路由', chute_id=? WHERE id=?",
            (chute["id"], scan_id),
        )
        conn.execute(
            """
            INSERT INTO route_events(scan_id, chute_id, action, created_at, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (scan_id, chute["id"], "路由落入", now_str(), chute["code"]),
        )
        audit(conn, "扫描", scan_id, "路由裁决", chute["code"])
        if scan["divert_batch_id"]:
            from app.services.handoff import reconcile_divert_batch

            reconcile_divert_batch(conn, scan["divert_batch_id"])
        return get_scan_view(conn, scan_id)

