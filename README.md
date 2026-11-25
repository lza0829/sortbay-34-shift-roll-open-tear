# 分拣过机台（Sortbay）

骨架：过机扫描装载 → 口门路由裁决 → 错分回流批次 → 封袋班次对账

- 前端 http://127.0.0.1:6600
- API http://127.0.0.1:11600

## 开下一班口径

本仓库全局采用 **未清件阻塞开班** 架构。仍有待路由、错分回流、已路由/已入袋未封袋扫描或集包中袋时，开下一班整笔失败并回滚；清空后才原子封闭旧班、创建新班。详见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)。

后端回归测试：

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q
```
