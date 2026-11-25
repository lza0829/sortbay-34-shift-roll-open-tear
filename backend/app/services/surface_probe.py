from __future__ import annotations

from app.services.settings_util import get_json, get_setting


def probe_panel(conn) -> dict:
    last = get_json(conn, "surface_last", {}) or {}
    return {
        "ok": get_setting(conn, "surface_ok", "") == "1",
        "last": last,
        "待路由": last.get("待路由"),
        "已路由": last.get("已路由"),
        "错分回流": last.get("错分回流"),
        "已入袋": last.get("已入袋"),
        "已封袋": last.get("已封袋"),
    }


def merge_probe(base: dict, conn) -> dict:
    out = dict(base or {})
    panel = probe_panel(conn)
    if panel.get("ok"):
        out["surface_probe"] = panel
    return out
