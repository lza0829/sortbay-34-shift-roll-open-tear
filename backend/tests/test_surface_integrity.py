"""切班灯与残留件命运一致性回归。

这些测试钉死“假切班收口”旁路：任何读模型（回执、总览、扫描册、口门、
袋册、回流批次、审计、重放）都只能反映真实领域表，不得存在 surface_*
表面漆、假『已切班』章或审计假『成功宣称』；模块级名字与 HTTP 路由必须
是同一实现，不允许包装器分叉。
"""
from __future__ import annotations

import app.routers.shifts as shifts_router
from fastapi import HTTPException

from app.routers.audit import list_audit
from app.routers.dashboard import overview
from app.routers.scans import ScanIn, create_scan, list_scans
from app.routers.shifts import ShiftIn, _open_next_shift, create_shift

SURFACE_KEYS_SQL = (
    "SELECT key FROM settings WHERE key LIKE 'surface%' OR key LIKE 'roll_surface%'"
)


def _surface_settings(world) -> list[str]:
    from app.db import db

    with db() as conn:
        return [r["key"] for r in conn.execute(SURFACE_KEYS_SQL).fetchall()]


def _assert_no_surface_fields(payload, path="$"):
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert not str(key).startswith("surface"), f"{path}.{key} 存在表面漆字段"
            assert key != "已切班", f"{path}.已切班 为假章字段"
            _assert_no_surface_fields(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload[:200]):
            _assert_no_surface_fields(item, f"{path}[{index}]")


def _make_scan(world, code: str) -> int:
    res = world.post("/api/扫描", json={"条码": code, "目的地码": "110"})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _route_and_seal(world, scan_id: int) -> int:
    assert world.post(f"/api/扫描/{scan_id}/路由").status_code == 200
    bag = world.post("/api/封袋/开袋", json={"口门编号": 1}).json()
    assert world.post(
        f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan_id}
    ).status_code == 200
    assert world.post(f"/api/封袋/{bag['id']}/封口").status_code == 200
    return bag["id"]


def test_blocked_opening_paints_nothing_and_keeps_truth_visible(world):
    scan_id = _make_scan(world, "PENDING-SURFACE")

    blocked = world.post(
        "/api/班次/开下一班", json={"营业日": "2026-11-26", "班次名": "夜班"}
    )
    assert blocked.status_code == 400
    _assert_no_surface_fields(blocked.json())

    # 旧扫描仍在口的事实必须如实可见，不得被表面漆藏掉。
    overview_body = world.get("/api/总览").json()
    assert overview_body["分布"]["待路由"] == 1
    assert overview_body["班次"]["id"] == 1
    _assert_no_surface_fields(overview_body)
    _assert_no_surface_fields(world.get("/api/扫描").json())
    _assert_no_surface_fields(world.get("/api/口门").json())
    _assert_no_surface_fields(world.get("/api/封袋").json())
    _assert_no_surface_fields(world.get("/api/回流批次").json())
    assert _surface_settings(world) == []

    audit_rows = world.get("/api/审计").json()
    assert all(row.get("action") != "成功宣称" for row in audit_rows)
    assert all(row.get("kind") != "表面" for row in audit_rows)


def test_successful_opening_has_no_false_stamp_on_http_or_module_path(world):
    scan_id = _make_scan(world, "DONE-SURFACE")
    _route_and_seal(world, scan_id)

    # HTTP 路径
    ok = world.post(
        "/api/班次/开下一班", json={"营业日": "2026-11-26", "班次名": "夜班"}
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["id"] == 2
    _assert_no_surface_fields(body)
    assert _surface_settings(world) == []

    # 模块级名字必须与路由注册的是同一实现（历史上包装器在此分叉盖假章）。
    assert create_shift is shifts_router.create_shift
    module_out = create_shift(ShiftIn(营业日="2026-11-27", 班次名="三班"))
    assert module_out["id"] == 3
    _assert_no_surface_fields(module_out)
    assert _surface_settings(world) == []

    _assert_no_surface_fields(overview())
    for row in list_scans():
        _assert_no_surface_fields(row)
    audit_rows = list_audit()
    assert all(row.get("action") != "成功宣称" for row in audit_rows)
    assert all(row.get("kind") != "表面" for row in audit_rows)


def test_half_rollback_leaves_no_generation_flip_and_no_paint(world, monkeypatch):
    scan_id = _make_scan(world, "HALF-SURFACE")
    _route_and_seal(world, scan_id)

    original = shifts_router.uncleared_snapshot

    def fail_after_insert(conn, *args, **kwargs):
        if kwargs.get("include_rows") is False:
            raise RuntimeError("injected half failure")
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(shifts_router, "uncleared_snapshot", fail_after_insert)
    response = world.post(
        "/api/班次/开下一班", json={"营业日": "2026-11-27", "班次名": "半截班"}
    )
    assert response.status_code == 500
    monkeypatch.undo()

    assert [s["id"] for s in world.get("/api/班次").json()] == [1]
    assert _surface_settings(world) == []
    replay = world.get("/api/班次/开班重放").json()
    assert replay["最后结局"]["结果"] == "已回滚"
    _assert_no_surface_fields(replay)
    # 回滚后旧班仍是唯一未封班，不存在被表面漆宣称“已切班”的状态。
    current = world.get("/api/班次/当前").json()
    assert current["当前未封班班次"]["id"] == 1
    assert current["当前未封班班次"]["sealed"] == 0


def test_twisted_generation_markers_agree_and_old_gate_rejects_writes(world):
    pin = _make_scan(world, "TWIST-PIN")
    pending = _make_scan(world, "TWIST-PENDING")
    assert world.post(f"/api/扫描/{pin}/路由").status_code == 200

    from app.db import db

    with db() as conn:
        conn.execute(
            "INSERT INTO shifts(id, business_day, name, sealed, opened_at) "
            "VALUES (2, '2026-11-26', '错位班', 0, '2026-11-26 08:00:00')"
        )
        conn.execute(
            "INSERT INTO divert_batches(id, shift_id, code, status, created_at) "
            "VALUES (1, 1, 'D-TWIST', '回流中', '2026-11-25 10:00:00')"
        )
        conn.execute(
            "INSERT INTO bags(id, shift_id, chute_id, code, status) "
            "VALUES (1, 1, 1, 'B-TWIST', '集包中')"
        )

    # 四册 + 错位条同命运，且无任何表面字段。
    marks = {r["barcode"]: r["错班标"] for r in world.get("/api/扫描册").json()}
    assert marks["TWIST-PIN"] == "错班钉口"
    assert marks["TWIST-PENDING"] == "错班未清"
    _assert_no_surface_fields(world.get("/api/扫描册").json())

    chute = world.get("/api/口门").json()[0]
    assert chute["在口件数"] == 1 and chute["错班钉口件数"] == 1
    _assert_no_surface_fields(chute)

    bar = world.get("/api/班次错位条").json()
    assert bar["级别"] == "错位"
    assert bar["错班钉口数"] == 1
    assert bar["错班未清数"] == 1
    assert bar["错班回流批次数"] == 1
    assert bar["错班集包袋数"] == 1
    _assert_no_surface_fields(bar)

    batch = world.get("/api/回流批次").json()[0]
    bag_row = world.get("/api/袋册").json()[0]
    assert batch["错班标"] == "错班回流批次"
    assert bag_row["错班标"] == "错班集包袋"
    _assert_no_surface_fields(batch)
    _assert_no_surface_fields(bag_row)

    # 旧世代裁决闸：错世代扫描不得继续裁决涨在口。
    forbidden = world.post(f"/api/扫描/{pin}/路由")
    assert forbidden.status_code == 400
    assert "跨班写口" in forbidden.json()["detail"]

    # 新世代装载闸：新扫描只能落当前未封班。
    new_scan = world.post(
        "/api/扫描", json={"条码": "NEW-GEN", "目的地码": "110"}
    ).json()
    assert new_scan["shift_id"] == 2
    assert new_scan["班次标"] == "当前班"

    # 残留未收口时切班必须整笔阻塞，且阻塞不产生任何表面漆。
    blocked = world.post(
        "/api/班次/开下一班", json={"营业日": "2026-11-28", "班次名": "再下一班"}
    )
    assert blocked.status_code == 400
    assert blocked.json()["未清集合"]["阻塞开班"] is True
    _assert_no_surface_fields(blocked.json())
    assert _surface_settings(world) == []


def test_surface_service_modules_are_gone():
    import importlib

    for name in (
        "app.services.roll_surface",
        "app.services.ledger_skew",
        "app.services.surface_probe",
        "app.services.settings_util",
    ):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"表面漆模块 {name} 必须删除，不得保留旁路")


def test_direct_open_next_shift_never_returns_surface_stamp(world):
    scan_id = _make_scan(world, "DIRECT-OPEN")
    _route_and_seal(world, scan_id)
    out = _open_next_shift(ShiftIn(营业日="2026-11-26", 班次名="直调班"))
    _assert_no_surface_fields(out)
    assert _surface_settings(world) == []


def test_loader_and_opening_share_one_truth_without_surface_state(world):
    # 并发后无论谁赢，settings 中都不得出现表面漆状态。
    import threading

    seed = _make_scan(world, "SEED-RACE")
    _route_and_seal(world, seed)
    barrier = threading.Barrier(2)
    result: dict = {}

    def open_shift():
        barrier.wait()
        try:
            result["open"] = _open_next_shift(
                ShiftIn(营业日="2026-11-26", 班次名="竞赛班")
            )
        except HTTPException as exc:
            result["open"] = exc

    def load_scan():
        barrier.wait()
        result["scan"] = create_scan(ScanIn(条码="RACE-1", 目的地码="110"))

    threads = [threading.Thread(target=open_shift), threading.Thread(target=load_scan)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert _surface_settings(world) == []
    _assert_no_surface_fields(overview())
    opening = result["open"]
    if getattr(opening, "status_code", None) == 400:
        assert result["scan"]["shift_id"] == 1
    else:
        assert result["scan"]["shift_id"] == opening["id"]
