# 历史路由索引回填命令设计

## 1. 文档目标

本文档定义一个可重复执行的 CLI 命令，用于把 Redis 中历史遗留的单用户路由键补录进路由索引集合。

当前系统中：

- 单用户路由真值保存在 `route:user_node:{user_id}`
- 路由列表接口依赖索引集合 `route:user_node:index`

因此，如果历史上存在“只有单用户键、没有进入索引集合”的老数据，则：

- `dispatcher` 仍可正常读取该用户路由
- 但 `route-admin` 的 `GET /routes` 无法列出该用户

本设计的目标，是用一个最小、可重复执行、不会改动路由值本身的命令来修复这类历史数据。

## 2. 范围

本次只做以下能力：

- 扫描 Redis 中的历史 `route:user_node:{user_id}` 键
- 识别哪些用户尚未进入 `route:user_node:index`
- 将缺失用户补入索引集合
- 输出简要统计结果

本次不做以下能力：

- 不修改任何已有 `node_id`
- 不新增 HTTP 接口
- 不引入数据库持久化
- 不做自动定时任务
- 不重构现有 `route-admin` API

## 3. 背景与问题

当前代码已经具备：

- [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py) 中的 `UserNodeRouteStore`
- 路由索引集合 `route:user_node:index`
- `route-admin` 服务通过索引集合列出全部路由

但当前还存在一个历史兼容问题：

- 旧链路或手工维护可能只写了 `route:user_node:{user_id}`
- 这些老键没有同步进入 `route:user_node:index`
- 因此 `GET /routes` 只能看到新写入或新同步过的数据

这个问题不影响分发正确性，但影响运维可见性和管理体验。

## 4. 设计目标

本次设计满足以下目标：

1. 不改动现有运行时真值结构
2. 不影响 `dispatcher`、`executor`、`route-admin` 主链路
3. 支持安全重复执行
4. 回填只补索引，不覆盖现有路由值
5. 便于本地和远端运维执行

## 5. 方案比较

### 5.1 方案 A：一次性脚本

做法：

- 新建一个临时脚本，手动运行一次后即结束

优点：

- 改动最少

缺点：

- 后续换机、迁移、Redis 恢复后不好复用
- 很容易变成“临时文件留在仓库外”

### 5.2 方案 B：长期 CLI 命令

做法：

- 新增一个正式 CLI 入口
- 需要时可重复执行

优点：

- 最适合当前阶段
- 边界清晰
- 不扩大 HTTP 管理接口范围
- 后续迁移和恢复时可重复使用

缺点：

- 需要补一个命令入口与少量测试

### 5.3 方案 C：挂到 `route-admin` HTTP 接口

做法：

- 新增例如 `POST /routes/backfill-index`

优点：

- 远端触发方便

缺点：

- 扩大 `route-admin` 接口范围
- 增加鉴权与运维暴露面
- 不符合当前“小步收尾”的目标

## 6. 推荐方案

推荐采用 `方案 B：长期 CLI 命令`。

选择原因：

- 用户已经同意按最稳、最小影响的方式一步步推进
- CLI 更适合这种“历史数据修复”类工作
- 相比新增 HTTP 接口，CLI 对现有系统边界影响最小

## 7. 命令形态

建议新增一个独立入口，例如：

- `app/runtime/route_admin_cli.py`

建议命令形式：

```bash
python -m app.runtime.route_admin_cli backfill-index
```

可选扩展参数：

- `--dry-run`

首版可以支持：

```bash
python -m app.runtime.route_admin_cli backfill-index --dry-run
python -m app.runtime.route_admin_cli backfill-index
```

其中：

- `--dry-run` 只统计，不写 Redis
- 默认执行模式会真正补写索引集合

## 8. Redis 扫描策略

### 8.1 扫描对象

命令需要扫描：

- `route:user_node:*`

但必须排除：

- `route:user_node:index`

### 8.2 扫描方式

必须使用 Redis `SCAN`，而不是 `KEYS`。

原因：

- 线上 Redis 中不应使用阻塞式全量匹配
- `SCAN` 更适合运维级回填命令

### 8.3 回填逻辑

对每个扫描到的路由键：

1. 解析出 `user_id`
2. 读取其当前 `node_id`
3. 若 `node_id` 非空，则把 `user_id` 加入 `route:user_node:index`

注意：

- 不修改 `route:user_node:{user_id}` 的值
- 不尝试校验 `node_id` 是否健康
- 不删除任何旧数据

## 9. `UserNodeRouteStore` 扩展

建议在 [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py) 中新增最小能力：

- `iter_route_user_ids()` 或等价扫描方法
- `backfill_route_index(dry_run: bool = False) -> dict`

返回值建议包含：

- `scanned`
- `indexed`
- `skipped`
- `dry_run`

示例：

```python
{
    "scanned": 12,
    "indexed": 3,
    "skipped": 9,
    "dry_run": False,
}
```

## 10. 输出与退出语义

### 10.1 标准输出

命令执行完成后输出 JSON 或易读文本摘要。

建议输出 JSON：

```json
{
  "ok": true,
  "scanned": 12,
  "indexed": 3,
  "skipped": 9,
  "dry_run": false
}
```

### 10.2 退出码

- 成功返回 `0`
- Redis 连接失败或异常返回非 `0`

## 11. 安全边界

首版要求：

- 命令只运行在受信任的运维环境
- 使用已有 `.env.worker` 中的 `REDIS_URL`
- 不要求额外管理 token

因为这不是对外接口，而是运维命令。

## 12. 错误处理

### 12.1 Redis 不可用

- 输出错误信息
- 返回非 `0`

### 12.2 扫描到空值路由

如果某个 `route:user_node:{user_id}` 存在，但 value 为空：

- 不写入索引
- 计入 `skipped`

### 12.3 非法键格式

如果扫描到不符合 `route:user_node:{user_id}` 结构的键：

- 跳过
- 不中断整个命令

## 13. 测试要求

至少覆盖以下测试：

1. `SCAN` 结果中包含 `route:user_node:index` 时会被跳过
2. 历史单用户路由可被补入索引集合
3. `dry_run` 不会真正写 Redis
4. 空值路由不会写入索引
5. CLI 成功时输出统计结果

## 14. 验收标准

完成后至少满足：

1. 运行回填命令不会修改已有 `route:user_node:{user_id}` 的值
2. 历史路由能出现在 `GET /routes`
3. 命令可重复执行，不产生错误副作用
4. 线上执行时不使用 Redis `KEYS`

## 15. 结论

历史路由索引缺失是一个数据可见性问题，而不是运行时分发错误。最合适的修复方式，不是继续扩展 `route-admin` HTTP 接口，而是补一个长期可复用的 CLI 命令，专门用于把历史单用户路由补进索引集合。

这样可以在不扩大系统管理面、不影响当前主链路的前提下，修复 `GET /routes` 对历史老路由不可见的问题。
