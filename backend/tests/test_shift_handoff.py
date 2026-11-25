from __future__ import annotations

import threading

import app.routers.shifts as shifts_router
from fastapi import HTTPException

from app.routers.scans import ScanIn, create_scan
from app.routers.shifts import ShiftIn, _open_next_shift


def make_scan(client, barcode: str = "PKG1", dest: str = "110") -> int:
    res = client.post("/api/扫描", json={"条码": barcode, "目的地码": dest})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def route_and_seal(client, scan_id: int, chute_id: int = 1) -> int:
    res = client.post(f"/api/扫描/{scan_id}/路由")
    assert res.status_code == 200, res.text
    bag = client.post("/api/封袋/开袋", json={"口门编号": chute_id})
    assert bag.status_code == 200, bag.text
    bag_id = bag.json()["id"]
    add = client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": scan_id})
    assert add.status_code == 200, add.text
    seal = client.post(f"/api/封袋/{bag_id}/封口")
    assert seal.status_code == 200, seal.text
    return bag_id


def test_architecture_is_single_blocking_arm(world):
    data = world.get("/api/班次/架构").json()
    assert data["架构键"] == "block_uncleared"
    assert data["未清件阻塞开班"] is True
    assert data["允许带未清件开班"] is False
    assert data["旧班写口冻结臂"] is False


def test_pending_scan_blocks_opening_and_keeps_snapshot(world):
    scan_id = make_scan(world, "PENDING-1")

    before_shifts = world.get("/api/班次").json()
    before_chutes = world.get("/api/口门").json()
    response = world.post("/api/班次/开下一班", json={"营业日": "2026-11-26", "班次名": "夜班"})

    assert response.status_code == 400
    body = response.json()
    assert "未清件阻塞开班" in body["detail"]
    assert body["未清集合"]["件数"] == 1
    assert body["未清集合"]["扫描"][0]["id"] == scan_id
    assert body["开班回执"]["结果"] == "阻塞"
    assert body["班次"]["id"] == 1

    assert world.get("/api/班次").json() == before_shifts
    assert world.get("/api/口门").json() == before_chutes
    overview = world.get("/api/总览").json()
    assert overview["班次"]["id"] == 1
    replay = world.get("/api/班次/开班重放").json()
    assert replay["事件"][-1]["event_type"] == "开班阻塞"


def test_routed_bagged_reflow_and_collecting_bag_all_block(world):
    pending = make_scan(world, "PENDING")
    routed = make_scan(world, "ROUTED")
    bagged = make_scan(world, "BAGGED")
    reflow = make_scan(world, "REFLOW")

    for scan_id in (routed, bagged, reflow):
        assert world.post(f"/api/扫描/{scan_id}/路由").status_code == 200
    bag = world.post("/api/封袋/开袋", json={"口门编号": 1}).json()
    assert world.post(f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": bagged}).status_code == 200
    divert = world.post("/api/回流批次", json={"扫描编号": reflow})
    assert divert.status_code == 200

    empty_bag = world.post("/api/封袋/开袋", json={"口门编号": 1})
    assert empty_bag.status_code == 200

    body = world.post("/api/班次", json={"营业日": "2026-11-26", "班次名": "夜班"}).json()
    assert body["未清集合"]["件数"] == 4
    assert body["未清集合"]["待路由"] == 1
    assert body["未清集合"]["已路由"] == 1
    assert body["未清集合"]["已入袋"] == 1
    assert body["未清集合"]["错分回流"] == 1
    assert body["未清集合"]["集包中袋数"] == 2  # one occupied collecting bag + one empty bag

    # The empty collecting bag has an explicit closure path.
    cancel = world.post(f"/api/封袋/{empty_bag.json()['id']}/撤销空袋")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "已撤销"


def test_clear_route_bag_seal_then_opening_succeeds_and_replays(world):
    scan_id = make_scan(world, "DONE-1")
    route_and_seal(world, scan_id)

    response = world.post("/api/班次/开下一班", json={"营业日": "2026-11-26", "班次名": "夜班"})
    assert response.status_code == 200, response.text
    new_shift = response.json()
    assert new_shift["id"] == 2
    assert new_shift["sealed"] == 0
    receipt = new_shift["开班回执"]
    assert receipt["结果"] == "成功"
    assert receipt["未清集合"]["件数"] == 0

    shifts = world.get("/api/班次").json()
    old = next(s for s in shifts if s["id"] == 1)
    assert old["sealed"] == 1

    overview = world.get("/api/总览").json()
    assert overview["班次"]["id"] == 2
    assert overview["班次错位条"]["级别"] == "一致"
    assert overview["口门"][0]["在口件数"] == 0

    replay = world.get("/api/班次/开班重放").json()
    assert [e["event_type"] for e in replay["事件"]] == ["开班成功"]
    audit = world.get("/api/审计").json()
    assert any(a["action"] == "开班成功" for a in audit)

    old_route = world.post(f"/api/扫描/{scan_id}/路由")
    assert old_route.status_code == 400
    assert "已封班" in old_route.json()["detail"]

    new_scan = world.post("/api/扫描", json={"条码": "NEW-SHIFT", "目的地码": "110"})
    assert new_scan.status_code == 200
    assert new_scan.json()["shift_id"] == 2
    assert new_scan.json()["班次标"] == "当前班"


def test_direct_twisted_shift_is_visible_in_scan_chute_and_overview(world):
    scan_id = make_scan(world, "TWIST")
    assert world.post(f"/api/扫描/{scan_id}/路由").status_code == 200
    with __import__("app.db", fromlist=["db"]).db() as conn:
        conn.execute(
            "INSERT INTO shifts(id, business_day, name, sealed, opened_at) "
            "VALUES (2, '2026-11-26', '错位班', 0, '2026-11-26 08:00:00')"
        )

    scan = world.get("/api/扫描").json()[0]
    assert scan["错班标"] == "错班钉口"
    chute = world.get("/api/口门").json()[0]
    assert chute["在口件数"] == 1
    assert chute["错班钉口件数"] == 1
    bar = world.get("/api/总览/班次错位条").json()
    assert bar["级别"] == "错位"
    assert bar["错班钉口数"] == 1


def test_mismatch_bar_agrees_with_scan_bag_and_batch_markers(world):
    pin = make_scan(world, "MARK-PIN")
    make_scan(world, "MARK-PENDING")
    assert world.post(f"/api/扫描/{pin}/路由").status_code == 200

    with __import__("app.db", fromlist=["db"]).db() as conn:
        conn.execute(
            "INSERT INTO shifts(id, business_day, name, sealed, opened_at) "
            "VALUES (2, '2026-11-26', '当前错位班', 0, '2026-11-26 08:00:00')"
        )
        conn.execute(
            "INSERT INTO divert_batches(id, shift_id, code, status, created_at) "
            "VALUES (1, 1, 'D-TWIST', '回流中', '2026-11-25 10:00:00')"
        )
        conn.execute(
            "INSERT INTO bags(id, shift_id, chute_id, code, status) "
            "VALUES (1, 1, 1, 'B-TWIST', '集包中')"
        )

    rows = world.get("/api/扫描册").json()
    marks = {row["barcode"]: row["错班标"] for row in rows}
    assert marks["MARK-PIN"] == "错班钉口"
    assert marks["MARK-PENDING"] == "错班未清"

    bar = world.get("/api/班次错位条").json()
    assert bar["错班钉口数"] == 1
    assert bar["错班未清数"] == 1
    assert bar["错班回流批次数"] == 1
    assert bar["错班集包袋数"] == 1

    batch = world.get("/api/回流批次").json()[0]
    assert batch["错班标"] == "错班回流批次"
    assert batch["shift_mismatch"] is True
    bag = world.get("/api/袋册").json()[0]
    assert bag["错班标"] == "错班集包袋"
    assert bag["shift_mismatch"] is True
    assert isinstance(world.get("/api/口门/在口").json(), list)
    assert "集包中" in world.get("/api/未清集合").json()

    try:
        _open_next_shift(ShiftIn(营业日="2026-11-27", 班次名="不会创建"))
        raise AssertionError("direct opening call should raise the blocking failure")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.payload["未清集合"]["阻塞开班"] is True


def test_half_opening_failure_rolls_back_shift_and_persists_replay(world, monkeypatch):
    scan_id = make_scan(world, "DONE")
    route_and_seal(world, scan_id)

    original = shifts_router.uncleared_snapshot

    def fail_after_insert(conn, *args, **kwargs):
        if kwargs.get("include_rows") is False:
            raise RuntimeError("injected half failure")
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(shifts_router, "uncleared_snapshot", fail_after_insert)
    response = world.post("/api/班次/开下一班", json={"营业日": "2026-11-27", "班次名": "半截班"})
    assert response.status_code == 500

    shifts = world.get("/api/班次").json()
    assert [s["id"] for s in shifts] == [1]
    assert all(s["name"] != "半截班" for s in shifts)
    replay = world.get("/api/班次/开班重放").json()
    assert replay["事件"][-1]["event_type"] == "开班回滚"
    assert replay["最后结局"]["结果"] == "已回滚"


def test_blocked_opening_keeps_old_shift_writable_and_then_opens(world):
    scan_id = make_scan(world, "BLOCK-THEN-WORK")
    blocked = world.post("/api/班次/开下一班", json={"营业日": "2026-11-26", "班次名": "夜班"})
    assert blocked.status_code == 400

    # The blocking arm does not freeze the current shift: closure work continues.
    routed = world.post(f"/api/扫描/{scan_id}/路由")
    assert routed.status_code == 200, routed.text
    route_and_seal_bag_only(world, scan_id)

    opened = world.post("/api/班次/开下一班", json={"营业日": "2026-11-26", "班次名": "夜班"})
    assert opened.status_code == 200, opened.text
    assert opened.json()["id"] == 2


def route_and_seal_bag_only(client, scan_id: int, chute_id: int = 1) -> int:
    bag = client.post("/api/封袋/开袋", json={"口门编号": chute_id})
    assert bag.status_code == 200, bag.text
    bag_id = bag.json()["id"]
    add = client.post(f"/api/封袋/{bag_id}/入袋", json={"扫描编号": scan_id})
    assert add.status_code == 200, add.text
    seal = client.post(f"/api/封袋/{bag_id}/封口")
    assert seal.status_code == 200, seal.text
    return bag_id


def test_direct_collecting_status_scan_has_seal_closure(world):
    scan_id = make_scan(world, "COLLECTING-STATUS")
    assert world.post(f"/api/扫描/{scan_id}/路由").status_code == 200
    bag = world.post("/api/封袋/开袋", json={"口门编号": 1}).json()
    assert world.post(
        f"/api/封袋/{bag['id']}/入袋", json={"扫描编号": scan_id}
    ).status_code == 200

    with __import__("app.db", fromlist=["db"]).db() as conn:
        conn.execute("UPDATE scans SET status='集包中' WHERE id=?", (scan_id,))

    blocked = world.post("/api/班次/开下一班", json={"营业日": "2026-11-26", "班次名": "夜班"})
    assert blocked.status_code == 400
    assert blocked.json()["未清集合"]["集包中扫描"] == 1

    seal = world.post(f"/api/封袋/{bag['id']}/封口")
    assert seal.status_code == 200, seal.text
    scan = world.get("/api/扫描").json()[0]
    assert scan["status"] == "已封袋"
    opened = world.post("/api/班次/开下一班")
    assert opened.status_code == 200, opened.text
    assert opened.json()["开班回执"]["结果"] == "成功"


def test_concurrent_open_and_load_never_creates_split_brain(world):
    # Start from a cleared old shift.
    seed = make_scan(world, "SEED")
    route_and_seal(world, seed)

    barrier = threading.Barrier(2)
    result: dict = {}

    def open_shift():
        barrier.wait()
        try:
            result["open"] = _open_next_shift(ShiftIn(营业日="2026-11-26", 班次名="竞赛班"))
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

    opening = result["open"]
    scan = result["scan"]
    current = world.get("/api/总览").json()["当前未封班班次"]

    if getattr(opening, "status_code", None) == 400:
        # Loader won the lock: opening must fail and scan remains on old/current.
        assert scan["shift_id"] == current["id"] == 1
        assert world.get("/api/扫描/未清集合").json()["件数"] == 1
    else:
        # Opening won the lock: loader must land on the brand-new shift.
        assert opening["id"] == current["id"]
        assert scan["shift_id"] == opening["id"]
        assert scan["班次标"] == "当前班"
