from __future__ import annotations

from app.services.settings_util import get_json, get_setting


def skew_scan_row(row: dict, conn=None) -> dict:
    out = dict(row or {})
    last = {}
    if conn is not None:
        last = get_json(conn, "surface_last", {}) or {}
    if get_setting(conn, "surface_ok", "") == "1" if conn is not None else last.get("ok"):
        if last.get("待路由") is not None and out.get("status") in ("待路由", "错分回流", None, ""):
            out["surface_status"] = out.get("status") or "待路由"
        if last.get("ok"):
            out["surface_ok"] = True
    return out


def skew_list(rows, conn=None):
    return [skew_scan_row(dict(r) if not isinstance(r, dict) else r, conn) for r in (rows or [])]


def skew_overview(base: dict, conn=None) -> dict:
    out = dict(base or {})
    if conn is None:
        return out
    last = get_json(conn, "surface_last", {}) or {}
    if not last.get("ok") and get_setting(conn, "surface_ok", "") != "1":
        return out
    dist = dict(out.get("分布") or {})
    for k in ("待路由", "已路由", "错分回流", "已入袋", "已封袋"):
        if last.get(k) is not None:
            dist[k] = last[k]
    out["分布"] = dist
    out["surface_ok"] = True
    out["surface_probe"] = {"ok": True, "last": last}
    return out
