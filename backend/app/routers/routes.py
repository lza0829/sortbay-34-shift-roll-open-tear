from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list, row_to_dict
from app.services.common import audit

router = APIRouter(tags=["路由表"])


class RouteIn(BaseModel):
    目的地码: str = Field(min_length=1)
    口门编号: int
    备注: str = ""


@router.get("/路由表")
def list_routes():
    with db() as conn:
        return rows_to_list(conn.execute(
            """
            SELECT r.*, c.code AS 口门码, c.title AS 口门标题
            FROM route_rules r JOIN chutes c ON c.id=r.chute_id
            ORDER BY r.dest_code
            """
        ).fetchall())


@router.post("/路由表")
def upsert_route(body: RouteIn):
    with db() as conn:
        chute = conn.execute("SELECT * FROM chutes WHERE id=?", (body.口门编号,)).fetchone()
        if not chute:
            raise HTTPException(404, "口门不存在")
        exist = conn.execute(
            "SELECT * FROM route_rules WHERE dest_code=?", (body.目的地码,)
        ).fetchone()
        if exist:
            conn.execute(
                "UPDATE route_rules SET chute_id=?, note=? WHERE id=?",
                (body.口门编号, body.备注, exist["id"]),
            )
            rid = exist["id"]
            action = "改路由"
        else:
            conn.execute(
                "INSERT INTO route_rules(dest_code, chute_id, note) VALUES (?, ?, ?)",
                (body.目的地码, body.口门编号, body.备注),
            )
            rid = conn.execute(
                "SELECT id FROM route_rules WHERE dest_code=?", (body.目的地码,)
            ).fetchone()["id"]
            action = "新建路由"
        audit(conn, "路由表", rid, action, body.目的地码)
        return row_to_dict(conn.execute("SELECT * FROM route_rules WHERE id=?", (rid,)).fetchone())

def _surface_router_routes() -> dict:
    return {"panel": "routes", "ready": True}

