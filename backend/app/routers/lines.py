from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from app.db import db, rows_to_list, row_to_dict
from app.services.common import audit

router = APIRouter(tags=["分拣线"])


class LineIn(BaseModel):
    名称: str = Field(min_length=1)
    启用: bool = True


@router.get("/分拣线")
def list_lines():
    with db() as conn:
        return rows_to_list(conn.execute("SELECT * FROM lines ORDER BY id").fetchall())


@router.post("/分拣线")
def create_line(body: LineIn):
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO lines(name, active) VALUES (?, ?)",
                (body.名称, 1 if body.启用 else 0),
            )
        except Exception as e:
            raise HTTPException(400, f"创建失败: {e}")
        row = conn.execute("SELECT * FROM lines WHERE name=?", (body.名称,)).fetchone()
        audit(conn, "分拣线", row["id"], "新建", body.名称)
        return row_to_dict(row)


@router.post("/分拣线/{line_id}/启停")
def toggle_line(line_id: int, 启用: bool = Query(...)):
    with db() as conn:
        cur = conn.execute(
            "UPDATE lines SET active=? WHERE id=?",
            (1 if 启用 else 0, line_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "分拣线不存在")
        audit(conn, "分拣线", line_id, "启停", "启用" if 启用 else "停用")
        return row_to_dict(conn.execute("SELECT * FROM lines WHERE id=?", (line_id,)).fetchone())

