from __future__ import annotations
from datetime import date, datetime

from app.db import db, init_db


def seed_if_empty() -> None:
    init_db()
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM lines").fetchone()["c"]
        if n:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        day = date.today().isoformat()
        conn.execute("INSERT INTO lines(name, active) VALUES (?, 1)", ("一号皮带",))
        line_id = conn.execute("SELECT id FROM lines WHERE name=?", ("一号皮带",)).fetchone()["id"]
        conn.execute(
            "INSERT INTO shifts(business_day, name, sealed) VALUES (?, ?, 0)",
            (day, "早班",),
        )
        shift_id = conn.execute(
            "SELECT id FROM shifts WHERE business_day=? AND name=?", (day, "早班")
        ).fetchone()["id"]
        chutes = [
            ("A01", "华北快运格"),
            ("A02", "华东快运格"),
            ("B01", "西南快运格"),
            ("C01", "异常暂存格"),
        ]
        chute_ids = {}
        for code, title in chutes:
            conn.execute(
                "INSERT INTO chutes(line_id, code, title, status, soft_cap) VALUES (?, ?, ?, '口门开', 120)",
                (line_id, code, title),
            )
            chute_ids[code] = conn.execute(
                "SELECT id FROM chutes WHERE line_id=? AND code=?", (line_id, code)
            ).fetchone()["id"]
        routes = [
            ("110", "A01"),
            ("210", "A02"),
            ("510", "B01"),
            ("999", "C01"),
        ]
        for dest, chute in routes:
            conn.execute(
                "INSERT INTO route_rules(dest_code, chute_id, note) VALUES (?, ?, ?)",
                (dest, chute_ids[chute], "种子路由"),
            )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?), (?, ?)",
            ("口门软容量提示", "120", "回流自动再路由", "否"),
        )
        # 预置一笔待路由扫描，方便冒烟看见分布
        conn.execute(
            """
            INSERT INTO scans(barcode, dest_code, shift_id, line_id, status, scanned_at, note)
            VALUES (?, ?, ?, ?, '待路由', ?, ?)
            """,
            ("YB202611240001", "110", shift_id, line_id, now, "种子待路由"),
        )
        conn.execute(
            "INSERT INTO audit_events(kind, ref_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
            ("系统", None, "种子初始化", now, "分拣过机台"),
        )
