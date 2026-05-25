# 路由回填 CLI 解耦与统计口径优化设计

## 1. 文档目标

本文档定义对现有历史路由索引回填命令的一次小范围收尾优化，解决两个已经确认的问题：

- `route_admin_cli.py` 目前通过 `route_admin_service.py` 间接复用 Redis 工厂，导致 CLI 也隐式依赖 `aiohttp`
- 回填结果中的 `indexed` 字段语义不够清晰，容易被误解为“本次新补入数”

本次优化目标是：

- 让 CLI 脱离 `aiohttp` 的隐式依赖
- 让回填统计口径更清楚、适合运维使用

## 2. 范围

本次只做以下能力：

- 调整 CLI 的 Redis 客户端创建方式
- 优化 `backfill_route_index()` 返回统计字段
- 更新对应测试与运维文档

本次不做以下能力：

- 不新增 HTTP 接口
- 不改 `route-admin` 的 HTTP 功能
- 不改主服务器 / 专用执行节点主链路
- 不改 Redis 路由真值结构

## 3. 背景与现状

当前系统已经具备：

- [route_admin_cli.py](file:///d:/old/FuRunSystemV4/app/runtime/route_admin_cli.py) 可执行 `backfill-index`
- [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py) 中的 `UserNodeRouteStore.backfill_route_index()`
- 远端已验证 `dry-run`、正式回填和幂等复跑

当前存在两个已知问题：

### 3.1 CLI 隐式依赖 `aiohttp`

CLI 目前通过 `route_admin_service` 里的 Redis 工厂创建连接。

这会带来两个问题：

- 运维命令虽然只操作 Redis，却要间接 import `aiohttp`
- 一旦系统 Python 没装 `aiohttp`，CLI 即使不启动 HTTP 服务也会失败

### 3.2 统计口径不够清楚

当前回填结果类似：

```json
{
  "scanned": 2,
  "indexed": 2,
  "skipped": 0,
  "dry_run": false
}
```

这里的 `indexed` 更接近：

- “本次识别到的有效路由数”

而不是：

- “本次真正新补入索引集合的数量”

这在幂等复跑时容易让运维误读。

## 4. 设计目标

本次优化满足以下目标：

1. CLI 不再依赖 `aiohttp`
2. 回填统计结果可直接用于运维判断
3. 不改变命令名与基本用法
4. 不影响现有 HTTP 路由管理能力

## 5. 方案比较

### 5.1 方案 A：最小解耦 + 统计重命名

做法：

- 在 CLI 内部直接创建 Redis 客户端
- `backfill_route_index()` 返回更清楚的统计字段

优点：

- 改动最小
- 边界清晰
- 不影响 `route-admin` 服务

缺点：

- 需要同步更新测试与文档

### 5.2 方案 B：继续复用 `route_admin_service`，只改统计字段

做法：

- 保持 CLI 通过 `route_admin_service` 获取 Redis 工厂
- 只调整统计字段

优点：

- 改动更少

缺点：

- `aiohttp` 隐式依赖仍然存在
- 不能真正解决运维命令的依赖边界问题

### 5.3 方案 C：把 Redis 工厂抽到更通用模块

做法：

- 新建一个通用 runtime 基础模块，给 CLI 和 HTTP 服务共同复用

优点：

- 理论上更干净

缺点：

- 对当前小问题来说过度设计
- 扩大改动面

## 6. 推荐方案

推荐采用 `方案 A：最小解耦 + 统计重命名`。

选择原因：

- 最符合“先一步步来”的节奏
- 能直接解决两个已知问题
- 对现有运行链路影响最小

## 7. CLI 解耦设计

### 7.1 新的依赖边界

CLI 不再 import `route_admin_service.py`。

改为：

- 直接在 CLI 内部使用 `Redis.from_url(..., decode_responses=True)` 创建客户端
- 继续读取现有 `WorkerSettings.redis_url`

这样 CLI 的运行依赖只保留：

- `redis.asyncio`
- `worker_config`
- `redis_flow`

### 7.2 保持命令入口不变

以下命令保持不变：

```bash
python -m app.runtime.route_admin_cli backfill-index
python -m app.runtime.route_admin_cli backfill-index --dry-run
```

## 8. 新的统计口径

### 8.1 字段定义

建议把当前统计结果调整为：

- `found`
- `newly_indexed`
- `already_indexed`
- `skipped`
- `dry_run`

### 8.2 语义定义

- `found`
  - 扫描到且路由值非空的有效历史路由数
- `newly_indexed`
  - 本次真正新补入 `route:user_node:index` 的数量
- `already_indexed`
  - 本次扫描时原本就已在索引集合里的数量
- `skipped`
  - 空值路由、非法键等跳过数量
- `dry_run`
  - 是否为模拟执行

### 8.3 示例

首次正式回填可能输出：

```json
{
  "ok": true,
  "found": 2,
  "newly_indexed": 1,
  "already_indexed": 1,
  "skipped": 0,
  "dry_run": false
}
```

幂等复跑可能输出：

```json
{
  "ok": true,
  "found": 2,
  "newly_indexed": 0,
  "already_indexed": 2,
  "skipped": 0,
  "dry_run": false
}
```

这样运维能直接看懂：

- 这次到底有没有新补入
- 还是只是重复检查

## 9. `UserNodeRouteStore` 行为调整

`backfill_route_index()` 的核心逻辑调整为：

1. `SCAN` 历史键
2. 跳过 `route:user_node:index`
3. 读取单用户路由值
4. 若为空，计入 `skipped`
5. 若用户已在索引集合中，计入 `already_indexed`
6. 若用户不在索引集合中：
   - `dry_run` 时只统计
   - 正式执行时写入集合并计入 `newly_indexed`

## 10. 测试要求

至少覆盖以下测试：

1. CLI 不再依赖 `route_admin_service`
2. `dry_run` 输出新的统计字段
3. 首次回填时 `newly_indexed` 正确增加
4. 幂等复跑时 `newly_indexed` 为 `0`
5. 已在索引中的用户计入 `already_indexed`

## 11. 验收标准

完成后至少满足：

1. `python -m app.runtime.route_admin_cli backfill-index` 不再因为缺少 `aiohttp` 失败
2. 回填结果 JSON 中不再使用含糊的 `indexed`
3. 运维能区分“首次补入”和“幂等复跑”
4. 现有 `route-admin` HTTP 服务行为保持不变

## 12. 结论

这是一次典型的小范围收尾优化，不需要引入新服务或新接口。最合适的做法，是让回填 CLI 直接依赖 Redis 而不是间接依赖 HTTP 服务，同时把统计口径从“能跑”提升到“运维可读”。这样可以在不扩大系统复杂度的前提下，把当前这条动态路由与历史回填链路真正收干净。
