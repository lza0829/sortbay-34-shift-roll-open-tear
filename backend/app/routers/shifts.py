from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, Field

from app.db import db, row_to_dict, rows_to_list
from app.services.common import audit, now_str
from app.services.handoff import (
    ARCH_NAME,
    PENDING_STATUSES,
    PayloadHTTPException,
    architecture_info,
    chute_snapshot,
    current_open_shift,
    divert_batch_views,
    latest_opening_receipt,
    list_scan_views,
    mismatch_bar,
    record_opening_outcome,
    reconcile_divert_batch,
    replay_opening_events,
    uncleared_snapshot,
)
from app.routers.bags import _bag_views

router = APIRouter(tags=["班次"])


class ShiftIn(BaseModel):
    # The canonical UI/API call supplies both fields.  Defaults and English
    # aliases keep the semantic "/开下一班" entry usable by compact fixtures.
    营业日: str = Field(
        default="",
        validation_alias=AliasChoices("营业日", "business_day", "day"),
    )
    班次名: str = Field(
        default="",
        validation_alias=AliasChoices("班次名", "shift_name", "name"),
    )


def _shift_rows(conn):
    rows = rows_to_list(
        conn.execute(
            """
            SELECT s.*,
                   (SELECT COUNT(*) FROM scans sc
                     WHERE sc.shift_id=s.id
                       AND sc.status IN ('待路由','错分回流','已路由','已入袋','集包中')) AS 未清件数,
                   (SELECT COUNT(*) FROM bags b
                     WHERE b.shift_id=s.id AND b.status='集包中') AS 集包中袋数
            FROM shifts s
            ORDER BY s.id DESC
            """
        ).fetchall()
    )
    for row in rows:
        row["uncleared_count"] = row.get("未清件数", 0)
        row["collecting_bag_count"] = row.get("集包中袋数", 0)
    return rows


def _divert_rows(conn):
    return divert_batch_views(conn)


def _board_books(conn):
    """All board ledgers read through the opening transaction's connection."""
    return {
        "扫描册": list_scan_views(conn),
        "袋册": _bag_views(conn),
        "回流批次": _divert_rows(conn),
    }


def _open_next_shift(body: ShiftIn | None = None):
    body = body or ShiftIn()
    day = (body.营业日 or "").strip() or datetime.now().strftime("%Y-%m-%d")
    name = (body.班次名 or "").strip() or f"下一班-{datetime.now().strftime('%H%M%S%f')}"
    requested = {**body.model_dump(), "营业日": day, "班次名": name}
    try:
        with db() as conn:
            before_shifts = _shift_rows(conn)
            current = current_open_shift(conn)
            snapshot = uncleared_snapshot(conn)
            mouths = chute_snapshot(conn)
            bar = mismatch_bar(conn)
            now = now_str()
            current_id = current["id"] if current else None
            books = _board_books(conn)

            receipt: dict = {
                "结果": "处理中",
                "结果码": "processing",
                "result": "processing",
                "status": "processing",
                "结论": "",
                "架构": architecture_info(),
                "架构口径": ARCH_NAME,
                "开班口": "POST /api/班次（等价 POST /api/班次/开下一班）",
                "请求": requested,
                "时间": now,
                "当前班次编号": current_id,
                "班次编号": current_id,
                "开班前班次": before_shifts,
                "未清集合": snapshot,
                "未清扫描列表": snapshot.get("扫描", []),
                "口门在口": mouths,
                "班次错位条": bar,
                "扫描册": books["扫描册"],
                "袋册": books["袋册"],
                "回流批次": books["回流批次"],
            }

            if snapshot["阻塞开班"]:
                message = "；".join(snapshot["阻塞原因"]) or "存在未清件"
                detail = f"未清件阻塞开班，整笔开班失败：{message}。班次列与口门在口保持开班前快照。"
                receipt.update(
                    {
                        "结果": "阻塞",
                        "结果码": "blocked",
                        "result": "blocked",
                        "status": "blocked",
                        "结论": detail,
                        "开班后班次编号": current_id,
                    }
                )
                record_opening_outcome(conn, "开班阻塞", receipt, current_id)
                # Commit before raising: db() would otherwise roll back the
                # replayable blocking outcome when the exception propagates.
                conn.commit()
                payload = {
                    "detail": detail,
                    "架构": architecture_info(),
                    "开班回执": receipt,
                    "未清集合": snapshot,
                    "未清扫描列表": snapshot.get("扫描", []),
                    "扫描册": books["扫描册"],
                    "袋册": books["袋册"],
                    "回流批次": books["回流批次"],
                    "班次": row_to_dict(current),
                    "班次列": before_shifts,
                    "口门在口": mouths,
                    "班次错位条": bar,
                    "班次错位": bar,
                    "错位条": bar,
                }
                raise PayloadHTTPException(400, payload)

            exists = conn.execute(
                "SELECT * FROM shifts WHERE business_day=? AND name=?",
                (day, name),
            ).fetchone()
            if exists:
                detail = f"开班失败：营业日 {day} 班次 {name} 已存在"
                receipt.update({
                    "结果": "失败",
                    "结果码": "failed",
                    "result": "failed",
                    "status": "failed",
                    "结论": detail,
                    "已存在班次": dict(exists),
                })
                record_opening_outcome(conn, "开班失败", receipt, exists["id"])
                conn.commit()
                payload = {
                    "detail": detail,
                    "开班回执": receipt,
                    "未清集合": snapshot,
                    "未清扫描列表": snapshot.get("扫描", []),
                    "扫描册": books["扫描册"],
                    "袋册": books["袋册"],
                    "回流批次": books["回流批次"],
                    "班次列": before_shifts,
                    "口门在口": mouths,
                    "班次错位条": bar,
                    "班次错位": bar,
                    "错位条": bar,
                }
                raise PayloadHTTPException(400, payload)

            # 没有未清扫描/集包中袋时，遗留的“回流中”批次只可能是已完成状态翻转未收口。
            for batch in snapshot["回流中批次"]:
                reconcile_divert_batch(conn, batch["id"])

            old_open_shifts = [
                dict(r) for r in conn.execute("SELECT * FROM shifts WHERE sealed=0 ORDER BY id").fetchall()
            ]
            sealed_time = now_str()
            conn.execute(
                "UPDATE shifts SET sealed=1, sealed_at=? WHERE sealed=0",
                (sealed_time,),
            )
            cur = conn.execute(
                """
                INSERT INTO shifts(business_day, name, sealed, sealed_at, opened_at, handoff_arch)
                VALUES (?, ?, 0, NULL, ?, ?)
                """,
                (day, name, now, "block_uncleared"),
            )
            new_id = int(cur.lastrowid)
            new_row = row_to_dict(conn.execute("SELECT * FROM shifts WHERE id=?", (new_id,)).fetchone())

            after_snapshot = uncleared_snapshot(conn, include_rows=False)
            after_mouths = chute_snapshot(conn)
            after_bar = mismatch_bar(conn)
            after_books = _board_books(conn)
            success_detail = f"开班成功：新班次 {new_id}；旧班已清空并封班"
            receipt.update(
                {
                    "结果": "成功",
                    "结果码": "success",
                    "result": "success",
                    "status": "success",
                    "success": True,
                    "结论": success_detail,
                    "班次编号": new_id,
                    "新班次编号": new_id,
                    "新班次": new_row,
                    "旧班已封": old_open_shifts,
                    "开班后未清集合": after_snapshot,
                    "开班后口门在口": after_mouths,
                    "开班后班次错位条": after_bar,
                    "开班后扫描册": after_books["扫描册"],
                    "开班后袋册": after_books["袋册"],
                    "开班后回流批次": after_books["回流批次"],
                    "扫描册": after_books["扫描册"],
                    "袋册": after_books["袋册"],
                    "回流批次": after_books["回流批次"],
                }
            )
            record_opening_outcome(conn, "开班成功", receipt, new_id)

            result = dict(new_row)
            result["架构"] = ARCH_NAME
            result["开班回执"] = receipt
            result["未清集合"] = after_snapshot
            result["班次错位条"] = after_bar
            result["班次错位"] = after_bar
            result["错位条"] = after_bar
            result["扫描册"] = after_books["扫描册"]
            result["袋册"] = after_books["袋册"]
            result["回流批次"] = after_books["回流批次"]
            return result
    except HTTPException:
        raise
    except Exception as exc:
        # The opening transaction has already been rolled back by db(); persist
        # the replayable rollback outcome in a separate committed transaction.
        rollback_receipt = {
            "结果": "已回滚",
            "结果码": "rolled_back",
            "result": "rolled_back",
            "status": "rolled_back",
            "success": False,
            "结论": f"开班半截失败，已整笔回滚：{type(exc).__name__}: {exc}",
            "架构": architecture_info(),
            "请求": requested,
            "时间": now_str(),
        }
        try:
            with db() as fail_conn:
                record_opening_outcome(fail_conn, "开班回滚", rollback_receipt)
        except Exception:
            pass
        raise HTTPException(500, rollback_receipt["结论"]) from exc


@router.get("/班次/架构")
@router.get("/shifts/architecture")
def shift_architecture():
    return architecture_info()


@router.get("/班次/开班重放")
@router.post("/班次/开班重放")
@router.get("/班次/事件重放")
@router.post("/班次/事件重放")
@router.get("/班次/开班事件")
@router.get("/开班重放")
@router.post("/开班重放")
@router.get("/事件重放")
@router.post("/事件重放")
def opening_replay(shift_id: int | None = None, 班次编号: int | None = None):
    effective_shift = shift_id if shift_id is not None else 班次编号
    with db() as conn:
        return replay_opening_events(conn, effective_shift)


@router.get("/班次/最后回执")
@router.get("/班次/开班回执")
@router.get("/开班回执")
@router.get("/最后回执")
@router.get("/shifts/last-receipt")
def last_receipt():
    with db() as conn:
        receipt = latest_opening_receipt(conn)
        if receipt is None:
            return {"结果": "无开班事件", "开班回执": None}
        return {**receipt, "开班回执": receipt}


@router.get("/班次/未清集合")
@router.get("/班次/未清件")
@router.get("/shifts/uncleared")
def shift_uncleared(shift_id: int | None = None, 班次编号: int | None = None):
    effective_shift = shift_id if shift_id is not None else 班次编号
    with db() as conn:
        return uncleared_snapshot(conn, effective_shift)


@router.get("/班次")
@router.get("/班次列")
@router.get("/班次列表")
@router.get("/shifts")
def list_shifts():
    with db() as conn:
        return _shift_rows(conn)


@router.get("/班次/当前")
@router.get("/班次/current")
@router.get("/shifts/current")
def get_current_shift():
    with db() as conn:
        current = current_open_shift(conn)
        if current is None:
            return {
                "当前未封班班次": None,
                "current_shift": None,
                "未清集合": uncleared_snapshot(conn, include_rows=False),
                "班次错位条": mismatch_bar(conn),
            }
        row = row_to_dict(current)
        return {
            **row,
            "当前未封班班次": row,
            "current_shift": row,
            "未清集合": uncleared_snapshot(conn, row["id"], include_rows=False),
            "班次错位条": mismatch_bar(conn),
        }


@router.get("/班次/对账")
def reconcile(shift_id: int | None = None, 班次编号: int | None = None):
    shift_id = shift_id if shift_id is not None else 班次编号
    with db() as conn:
        if shift_id is None:
            shift = conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 1").fetchone()
        else:
            shift = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not shift:
            raise HTTPException(404, "班次不存在")
        sid = shift["id"]
        scan_n = conn.execute("SELECT COUNT(*) c FROM scans WHERE shift_id=?", (sid,)).fetchone()["c"]
        routed = conn.execute(
            "SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status IN ('已路由','已入袋','已封袋')",
            (sid,),
        ).fetchone()["c"]
        bag_n = conn.execute("SELECT COUNT(*) c FROM bags WHERE shift_id=?", (sid,)).fetchone()["c"]
        sealed_bag = conn.execute(
            "SELECT COUNT(*) c FROM bags WHERE shift_id=? AND status='已封袋'", (sid,)
        ).fetchone()["c"]
        divert_n = conn.execute(
            "SELECT COUNT(*) c FROM divert_batches WHERE shift_id=?", (sid,)
        ).fetchone()["c"]
        pending_marks = ",".join("?" for _ in PENDING_STATUSES)
        pending = conn.execute(
            f"SELECT COUNT(*) c FROM scans WHERE shift_id=? AND status IN ({pending_marks})",
            (sid, *PENDING_STATUSES),
        ).fetchone()["c"]
        uncleared = uncleared_snapshot(conn, sid)
        return {
            "班次": row_to_dict(shift),
            "扫描总数": scan_n,
            "已路由及以上": routed,
            "袋数": bag_n,
            "已封袋数": sealed_bag,
            "回流批次数": divert_n,
            "未清件数": pending,
            "待路由或错分回流数": uncleared["待路由"] + uncleared["错分回流"],
            "集包中袋数": uncleared["集包中袋数"],
            "未清集合": uncleared,
            "班次错位条": mismatch_bar(conn),
        }


@router.post("/班次")
@router.post("/班次/开班")
@router.post("/班次/开下一班")
@router.post("/开下一班")
@router.post("/开班")
@router.post("/开班入口")
@router.post("/shifts")
@router.post("/shifts/open-next")
def create_shift(body: ShiftIn | None = None):
    return _open_next_shift(body)


@router.get("/班次/{shift_id}")
def get_shift(shift_id: int):
    with db() as conn:
        shift = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not shift:
            raise HTTPException(404, "班次不存在")
        result = row_to_dict(shift)
        result["未清集合"] = uncleared_snapshot(conn, shift_id, include_rows=False)
        result["班次错位条"] = mismatch_bar(conn)
        return result


@router.post("/班次/{shift_id}/封班")
@router.post("/shifts/{shift_id}/seal")
def seal_shift(shift_id: int):
    with db() as conn:
        shift = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not shift:
            raise HTTPException(404, "班次不存在")
        if shift["sealed"]:
            raise HTTPException(400, "已封班")

        stale = uncleared_snapshot(conn, shift_id)
        for batch in stale["回流中批次"]:
            reconcile_divert_batch(conn, batch["id"])

        uncleared = uncleared_snapshot(conn, shift_id)
        if uncleared["件数"] or uncleared["集包中袋数"]:
            raise HTTPException(
                400,
                (
                    f"仍有未清状态：扫描 {uncleared['件数']} 件"
                    f"（待路由 {uncleared['待路由']}、错分回流 {uncleared['错分回流']}、"
                    f"已路由未封袋 {uncleared['已路由未封袋']}），"
                    f"集包中袋 {uncleared['集包中袋数']} 个，禁止封班"
                ),
            )
        conn.execute(
            "UPDATE shifts SET sealed=1, sealed_at=? WHERE id=?",
            (now_str(), shift_id),
        )
        audit(conn, "班次", shift_id, "封班", "")
        return row_to_dict(conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone())

