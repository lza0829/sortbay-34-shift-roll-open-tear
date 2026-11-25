from __future__ import annotations
from app.services import roll_surface

import json
from typing import Any

from fastapi import HTTPException

from app.services.common import audit, now_str

ARCH_KEY = "block_uncleared"
ARCH_NAME = "未清件阻塞开班"


class PayloadHTTPException(HTTPException):
    """HTTPException that also preserves a rich Chinese API response body."""

    def __init__(self, status_code: int, payload: dict[str, Any]):
        super().__init__(status_code=status_code, detail=payload.get("detail", ""))
        self.payload = payload
        self.response_body = payload

# 未清扫描的唯一口径：除“已封袋”外，其余状态都尚未完成皮带收口。
# “集包中”是部分夹具/报表对“已入袋且袋未封”的同义状态，也纳入阻塞集合。
PENDING_STATUSES: tuple[str, ...] = ("待路由", "错分回流", "已路由", "已入袋", "集包中")
ROUTED_UNSEALED_STATUSES: tuple[str, ...] = ("已路由", "已入袋", "集包中")
MOUTH_STATUSES: tuple[str, ...] = ("已路由", "已入袋", "集包中")


def architecture_info() -> dict[str, Any]:
    return {
        "架构键": ARCH_KEY,
        "architecture": ARCH_KEY,
        "chosen_architecture": ARCH_KEY,
        "mode": "blocking",
        "arm": "blocking",
        "臂": "阻塞臂",
        "口径": ARCH_NAME,
        "architecture_label": ARCH_NAME,
        "未清件阻塞开班": True,
        "允许带未清件开班": False,
        "旧班写口冻结臂": False,
        "全局唯一架构": True,
        "开班成功条件": "无待路由、错分回流、已路由/已入袋未封袋扫描，且无集包中袋",
        "装载闸": "仅允许写入当前唯一未封班班次",
        "裁决闸": "仅允许裁决当前唯一未封班班次的扫描",
        "错班标位置": "扫描册派生“错班标”，总览/口门输出“班次错位条”",
        "收口路径": "旧班未清件继续路由、回流或封袋；清空后整笔开班并封闭旧班",
    }


def current_open_shift(conn):
    return conn.execute(
        "SELECT * FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1"
    ).fetchone()


def latest_shift(conn):
    return conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 1").fetchone()


def require_current_writable_shift(conn, shift_id: int):
    current = current_open_shift(conn)
    if current is None:
        raise HTTPException(400, "无未封班班次，请先开下一班")
    shift = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
    if shift is None:
        raise HTTPException(404, "班次不存在")
    if shift["sealed"]:
        raise HTTPException(400, f"班次 {shift_id} 已封班，禁止继续写口或裁决")
    if shift["id"] != current["id"]:
        raise HTTPException(
            400,
            f"班次 {shift_id} 不是当前班次 {current['id']}；阻塞臂禁止跨班写口",
        )
    return current, shift


def record_domain_event(
    conn,
    event_type: str,
    payload: dict[str, Any],
    *,
    aggregate_type: str = "班次",
    aggregate_id: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO domain_events(aggregate_type, aggregate_id, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            aggregate_type,
            aggregate_id,
            event_type,
            json.dumps(payload, ensure_ascii=False, default=str),
            now_str(),
        ),
    )
    return int(cur.lastrowid)


def record_opening_outcome(conn, event_type: str, receipt: dict[str, Any], ref_id: int | None = None) -> None:
    record_domain_event(conn, event_type, receipt, aggregate_id=ref_id)
    audit(
        conn,
        "班次",
        ref_id,
        event_type,
        f"{ARCH_NAME}; {receipt.get('结论', '')}"[:500],
    )


SCAN_SELECT = """
SELECT s.*,
       s.shift_id AS 班次编号,
       sh.business_day AS business_day,
       sh.business_day AS 营业日,
       sh.name AS shift_name,
       sh.name AS 班次名,
       sh.name AS 扫描所属班次,
       sh.sealed AS shift_sealed,
       sh.sealed AS 班次已封,
       l.name AS line_name,
       l.name AS 分拣线名,
       c.code AS chute_code,
       c.code AS 口门码,
       c.title AS chute_title,
       b.code AS bag_code,
       db.code AS divert_batch_code,
       (
         SELECT COUNT(*) FROM scans mouth_s
         WHERE mouth_s.chute_id = s.chute_id
           AND mouth_s.status IN ('已路由', '已入袋', '集包中')
       ) AS 口门在口件数,
       (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1) AS current_shift_id,
       CASE
         WHEN sh.id IS NULL THEN '班次缺失'
         WHEN (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1) IS NULL THEN '无未封班'
         WHEN s.shift_id = (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1) THEN '当前班'
         WHEN sh.sealed = 0 THEN '并行未封班'
         ELSE '旧班已封'
       END AS 班次标,
       CASE
         WHEN s.status IN ('已路由', '已入袋', '集包中')
          AND (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1) IS NOT NULL
          AND s.shift_id <> (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1)
           THEN '错班钉口'
         WHEN s.status IN ('待路由', '错分回流', '已路由', '已入袋')
          AND (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1) IS NOT NULL
          AND s.shift_id <> (SELECT id FROM shifts WHERE sealed=0 ORDER BY id DESC LIMIT 1)
           THEN '错班未清'
         ELSE ''
       END AS 错班标
FROM scans s
LEFT JOIN shifts sh ON sh.id = s.shift_id
LEFT JOIN lines l ON l.id = s.line_id
LEFT JOIN chutes c ON c.id = s.chute_id
LEFT JOIN bags b ON b.id = s.bag_id
LEFT JOIN divert_batches db ON db.id = s.divert_batch_id
"""


def list_scan_views(
    conn,
    *,
    status: str | None = None,
    shift_id: int | None = None,
    only_unclear: bool = False,
    limit: int = 10000,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("s.status = ?")
        params.append(status)
    if shift_id is not None:
        where.append("s.shift_id = ?")
        params.append(shift_id)
    if only_unclear:
        marks = ",".join("?" for _ in PENDING_STATUSES)
        where.append(f"s.status IN ({marks})")
        params.extend(PENDING_STATUSES)
    sql = SCAN_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    safe_limit = max(1, min(int(limit or 10000), 50000))
    sql += " ORDER BY s.id DESC LIMIT ?"
    params.append(safe_limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for row in rows:
        row["错班"] = bool(row.get("错班标"))
        row["shift_mismatch"] = bool(row.get("错班标"))
    return rows


def get_scan_view(conn, scan_id: int) -> dict[str, Any] | None:
    row = conn.execute(SCAN_SELECT + " WHERE s.id=?", (scan_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["错班"] = bool(data.get("错班标"))
    data["shift_mismatch"] = bool(data.get("错班标"))
    return data


def uncleared_snapshot(conn, shift_id: int | None = None, *, include_rows: bool = True) -> dict[str, Any]:
    where = "WHERE s.status IN ({})".format(",".join("?" for _ in PENDING_STATUSES))
    params: list[Any] = list(PENDING_STATUSES)
    bag_filters = ["b.status='集包中'"]
    batch_filters = ["d.status='回流中'"]
    if shift_id is not None:
        where += " AND s.shift_id=?"
        params.append(shift_id)
        bag_filters.append("b.shift_id=?")
        batch_filters.append("d.shift_id=?")
        bag_params: list[Any] = [shift_id]
        batch_params: list[Any] = [shift_id]
    else:
        bag_params = []
        batch_params = []
    bag_where = "WHERE " + " AND ".join(bag_filters)
    batch_where = "WHERE " + " AND ".join(batch_filters)

    status_rows = conn.execute(
        f"SELECT status, COUNT(*) AS c FROM scans s {where} GROUP BY status",
        params,
    ).fetchall()
    by_status = {r["status"]: int(r["c"]) for r in status_rows}
    scan_total = sum(by_status.values())

    bag_total = conn.execute(
        f"SELECT COUNT(*) AS c FROM bags b {bag_where}", bag_params
    ).fetchone()["c"]
    batch_total = conn.execute(
        f"SELECT COUNT(*) AS c FROM divert_batches d {batch_where}", batch_params
    ).fetchone()["c"]

    open_bags = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT b.*, c.code AS 口门码, sh.business_day, sh.name AS shift_name,
                   (SELECT COUNT(*) FROM scans s WHERE s.bag_id=b.id) AS 件数
            FROM bags b
            LEFT JOIN chutes c ON c.id=b.chute_id
            LEFT JOIN shifts sh ON sh.id=b.shift_id
            {bag_where}
            ORDER BY b.id DESC
            """,
            bag_params,
        ).fetchall()
    ]
    open_batches = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT d.*, sh.business_day, sh.name AS shift_name,
                   (SELECT COUNT(*) FROM scans s
                     WHERE s.divert_batch_id=d.id AND s.status='错分回流') AS 未收口扫描数
            FROM divert_batches d
            LEFT JOIN shifts sh ON sh.id=d.shift_id
            {batch_where}
            ORDER BY d.id DESC
            """,
            batch_params,
        ).fetchall()
    ]

    snapshot: dict[str, Any] = {
        "件数": scan_total,
        "未清件数": scan_total,
        "count": scan_total,
        "uncleared_count": scan_total,
        "pending_count": scan_total,
        "集包中袋数": int(bag_total),
        "open_bag_count": int(bag_total),
        "回流中批次数": int(batch_total),
        "open_divert_batch_count": int(batch_total),
        "未清单元数": scan_total + int(bag_total),
        "按状态": {status: by_status.get(status, 0) for status in PENDING_STATUSES},
        "待路由": by_status.get("待路由", 0),
        "错分回流": by_status.get("错分回流", 0),
        "已路由": by_status.get("已路由", 0),
        "已入袋": by_status.get("已入袋", 0),
        "集包中": by_status.get("集包中", 0),
        "集包中扫描": by_status.get("集包中", 0),
        "已路由未封袋": (
            by_status.get("已路由", 0)
            + by_status.get("已入袋", 0)
            + by_status.get("集包中", 0)
        ),
        "阻塞开班": scan_total > 0 or bag_total > 0,
        "blocked": scan_total > 0 or bag_total > 0,
        "block_opening": scan_total > 0 or bag_total > 0,
        "阻塞原因": [],
        "reasons": [],
        "集包中袋": open_bags,
        "open_bags": open_bags,
        "回流中批次": open_batches,
        "open_divert_batches": open_batches,
    }
    if scan_total:
        snapshot["阻塞原因"].append(f"{scan_total} 件扫描尚未封袋收口")
    if bag_total:
        snapshot["阻塞原因"].append(f"{bag_total} 个袋仍为集包中")
    snapshot["reasons"] = snapshot["阻塞原因"]
    if include_rows:
        unclear_rows = list_scan_views(conn, shift_id=shift_id, only_unclear=True)
        snapshot["扫描"] = unclear_rows
        snapshot["未清扫描列表"] = unclear_rows
        snapshot["items"] = unclear_rows
    return snapshot


def chute_snapshot(conn) -> list[dict[str, Any]]:
    current = current_open_shift(conn)
    current_id = current["id"] if current else None
    chutes = [
        dict(r)
        for r in conn.execute(
            """
            SELECT c.*, l.name AS 分拣线名
            FROM chutes c LEFT JOIN lines l ON l.id=c.line_id
            ORDER BY c.code
            """
        ).fetchall()
    ]
    groups = conn.execute(
        """
        SELECT chute_id, shift_id, COUNT(*) AS c
        FROM scans
        WHERE status IN ('已路由', '已入袋', '集包中') AND chute_id IS NOT NULL
        GROUP BY chute_id, shift_id
        """
    ).fetchall()
    grouped: dict[int, dict[str, int]] = {}
    for g in groups:
        chute_id = int(g["chute_id"])
        item = grouped.setdefault(chute_id, {"total": 0, "current": 0})
        count = int(g["c"])
        item["total"] += count
        if current_id is not None and g["shift_id"] == current_id:
            item["current"] += count

    for chute in chutes:
        counts = grouped.get(int(chute["id"]), {"total": 0, "current": 0})
        total = counts["total"]
        current_count = counts["current"]
        wrong = total - current_count if current_id is not None else total
        chute["在口件数"] = total
        chute["本班在口件数"] = current_count
        chute["全局在口件数"] = total
        chute["错班钉口件数"] = wrong
        chute["mouth_count"] = total
        chute["current_shift_count"] = current_count
        chute["mismatch_count"] = wrong
        chute["wrong_shift_pin_count"] = wrong
    return chutes


def mismatch_bar(conn) -> dict[str, Any]:
    current = current_open_shift(conn)
    latest = latest_shift(conn)
    current_id = current["id"] if current else None
    latest_id = latest["id"] if latest else None

    pin_count = conn.execute(
        """
        SELECT COUNT(*) AS c FROM scans
        WHERE status IN ('已路由', '已入袋', '集包中')
          AND (? IS NULL OR shift_id <> ?)
        """,
        (current_id, current_id),
    ).fetchone()["c"]
    wrong_unclear_count = conn.execute(
        """
        SELECT COUNT(*) AS c FROM scans
        WHERE status IN ('待路由', '错分回流')
          AND (? IS NULL OR shift_id <> ?)
        """,
        (current_id, current_id),
    ).fetchone()["c"]
    wrong_unclear_total_count = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM scans
        WHERE status IN ({','.join('?' for _ in PENDING_STATUSES)})
          AND (? IS NULL OR shift_id <> ?)
        """,
        (*PENDING_STATUSES, current_id, current_id),
    ).fetchone()["c"]
    wrong_batch_count = conn.execute(
        """
        SELECT COUNT(*) AS c FROM divert_batches
        WHERE status='回流中' AND (? IS NULL OR shift_id <> ?)
        """,
        (current_id, current_id),
    ).fetchone()["c"]
    wrong_bag_count = conn.execute(
        """
        SELECT COUNT(*) AS c FROM bags
        WHERE status='集包中' AND (? IS NULL OR shift_id <> ?)
        """,
        (current_id, current_id),
    ).fetchone()["c"]

    items = [
        s
        for s in list_scan_views(conn, only_unclear=True, limit=10000)
        if s.get("错班标")
    ]
    level = "一致"
    messages: list[str] = []
    if pin_count or wrong_unclear_count or wrong_bag_count or wrong_batch_count:
        level = "错位"
        if pin_count:
            messages.append(f"{pin_count} 件错班扫描仍钉在口门")
        if wrong_unclear_count:
            messages.append(f"{wrong_unclear_count} 件待路由/回流扫描不属于当前班次")
        if wrong_bag_count:
            messages.append(f"{wrong_bag_count} 个集包中袋不属于当前班次")
        if wrong_batch_count:
            messages.append(f"{wrong_batch_count} 个回流中批次不属于当前班次")
    elif current is None:
        level = "无未封班"
        messages.append("所有班次均已封班，尚未开下一班")
    else:
        messages.append("开班口、扫描班次列、口门在口、袋册与总览班次一致")

    bar = {
        "级别": level,
        "level": level,
        "正常": level == "一致",
        "ok": level == "一致",
        "文案": "；".join(messages),
        "message": "；".join(messages),
        "当前班次编号": current_id,
        "current_shift_id": current_id,
        "总览班次编号": latest_id,
        "显示班次编号": latest_id,
        "display_shift_id": latest_id,
        "班次选择错位": current_id is not None and latest_id != current_id,
        "错班钉口数": int(pin_count),
        "mismatch_count": int(pin_count),
        "wrong_shift_pin_count": int(pin_count),
        "错班未清数": int(wrong_unclear_count),
        "wrong_unclear_count": int(wrong_unclear_count),
        "错班未清总件数": int(wrong_unclear_total_count),
        "wrong_unclear_total_count": int(wrong_unclear_total_count),
        "错班集包袋数": int(wrong_bag_count),
        "wrong_shift_bag_count": int(wrong_bag_count),
        "错班回流批次数": int(wrong_batch_count),
        "wrong_shift_batch_count": int(wrong_batch_count),
        "条目": items,
        "items": items,
        "批次条目": [b for b in divert_batch_views(conn) if b.get("错班标")],
    }
    return bar


def divert_batch_views(conn) -> list[dict[str, Any]]:
    current = current_open_shift(conn)
    current_id = current["id"] if current else None
    rows = conn.execute(
        """
        SELECT d.*, sh.business_day, sh.name AS 班次名,
               ? AS current_shift_id,
               (SELECT COUNT(*) FROM scans s
                 WHERE s.divert_batch_id=d.id) AS 关联扫描数,
               (SELECT COUNT(*) FROM scans s
                 WHERE s.divert_batch_id=d.id AND s.status='错分回流') AS 未收口扫描数,
               CASE
                 WHEN d.status='回流中'
                  AND ? IS NOT NULL AND d.shift_id <> ?
                   THEN '错班回流批次'
                 ELSE ''
               END AS 错班标
        FROM divert_batches d
        LEFT JOIN shifts sh ON sh.id=d.shift_id
        ORDER BY d.id DESC LIMIT 500
        """,
        (current_id, current_id, current_id),
    ).fetchall()
    result = [dict(r) for r in rows]
    for row in result:
        row["错班"] = bool(row.get("错班标"))
        row["shift_mismatch"] = bool(row.get("错班标"))
    return result


def reconcile_divert_batch(conn, batch_id: int) -> dict[str, Any] | None:
    batch = conn.execute("SELECT * FROM divert_batches WHERE id=?", (batch_id,)).fetchone()
    if batch is None:
        return None
    unresolved = conn.execute(
        "SELECT COUNT(*) AS c FROM scans WHERE divert_batch_id=? AND status='错分回流'",
        (batch_id,),
    ).fetchone()["c"]
    if unresolved == 0 and batch["status"] == "回流中":
        conn.execute(
            "UPDATE divert_batches SET status='已收口', closed_at=?, close_note=? WHERE id=?",
            (now_str(), "关联扫描已离开错分回流状态", batch_id),
        )
        audit(conn, "回流", batch_id, "批次收口", "无剩余错分回流扫描")
    return dict(conn.execute("SELECT * FROM divert_batches WHERE id=?", (batch_id,)).fetchone())


def replay_opening_events(conn, shift_id: int | None = None) -> dict[str, Any]:
    sql = """
        SELECT * FROM domain_events
        WHERE aggregate_type='班次' AND event_type LIKE '开班%'
    """
    params: list[Any] = []
    if shift_id is not None:
        sql += " AND (aggregate_id=? OR payload_json LIKE ?)"
        params.extend([shift_id, f'%"班次编号": {shift_id}%'])
    sql += " ORDER BY id ASC"
    rows = conn.execute(sql, params).fetchall()
    events: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        event = dict(row)
        try:
            payload = json.loads(event.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        event["载荷"] = payload
        events.append(event)
        if event["event_type"] in {"开班阻塞", "开班成功", "开班失败", "开班回滚"}:
            outcomes.append(payload)
    return {
        "架构": architecture_info(),
        "事件": events,
        "events": events,
        "开班结局": outcomes,
        "outcomes": outcomes,
        "opening_outcomes": outcomes,
        "最后结局": outcomes[-1] if outcomes else None,
        "last_outcome": outcomes[-1] if outcomes else None,
        "可重放": True,
        "replayable": True,
    }


def latest_opening_receipt(conn) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM domain_events
        WHERE aggregate_type='班次'
          AND event_type IN ('开班阻塞', '开班成功', '开班失败', '开班回滚')
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        return None

_record_opening_outcome_impl = record_opening_outcome


def record_opening_outcome(*args, **kwargs):
    out = _record_opening_outcome_impl(*args, **kwargs)
    return roll_surface.after_roll(out, *args, **kwargs)

