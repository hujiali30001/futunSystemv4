# 用户路由 HTTP 管理接口设计

## 1. 文档目标

本文档定义一个仅用于“用户到执行节点路由管理”的轻量 HTTP 接口设计。

目标是解决当前运行时的一个具体问题：

- `dispatcher` 读取 Redis 中的 `route:user_node:{user_id}` 决定任务去向
- 现有 `USER_NODE_ROUTES` 只适合作为启动时配置
- 一旦需要在线调整某个用户改走哪台执行节点，目前仍需要改配置或手工执行 `redis-cli set`

本文档要求在不破坏当前已跑通的 `主服务器 scanner + dispatcher` 与 `专用节点 executor` 链路前提下，增加一个正式、可维护、可审计的路由管理入口。

## 2. 范围

本次只做以下能力：

- 查看全部用户路由
- 查看单个用户路由
- 设置或更新单个用户路由
- 删除单个用户路由
- 为路由变更输出结构化运行事件

本次不做以下能力：

- 不做完整后台页面
- 不做数据库持久化
- 不做多管理员权限系统
- 不做批量导入导出接口
- 不做跨服务总控平台

## 3. 背景与现状

当前项目已经具备以下条件：

- [worker_config.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_config.py) 已支持 `USER_NODE_ROUTES`
- [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py) 已有 `UserNodeRouteStore`
- [worker_service.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_service.py) 会在 `dispatcher` 启动时把 `USER_NODE_ROUTES` 同步到 Redis
- 运行时真正消费的是 Redis 中的 `route:user_node:{user_id}`

当前缺口有两个：

1. 路由变更没有正式入口，只能改环境变量或手工写 Redis
2. `dispatcher` 启动时的同步语义目前偏“覆盖”，会和未来动态写入的路由冲突

因此，动态路由入口不能只是“再包一层 Redis 写接口”，还必须重新定义：

- 哪份数据是运行时真值
- `USER_NODE_ROUTES` 和 Redis 之间的优先级关系

## 4. 设计目标

本次设计满足以下目标：

1. 运行中可动态查看、设置、删除用户路由
2. 当前 `dispatcher` / `executor` 主链路不需要停机重构
3. 动态路由不会因 `dispatcher` 重启而被环境变量覆盖
4. 新入口只开放最小管理能力，避免演化成新的后台系统
5. 默认仅本机或内网访问，降低暴露面

## 5. 方案比较

### 5.1 方案 A：把 HTTP 接口挂到 `worker_service` 进程里

做法：

- 在现有 `worker_service.py` 中附带启动一个 HTTP 管理端口
- 与 `scanner` 或 `dispatcher` 共进程运行

优点：

- 进程数量最少
- 启动方式最简单

缺点：

- 运行角色与管理入口耦合
- 排障时难区分“扫描/分发问题”还是“管理接口问题”
- 不利于后续单独收敛权限和监听范围

### 5.2 方案 B：单独的轻量 `route-admin` 服务

做法：

- 新增一个只负责用户路由管理的 HTTP 服务
- 只运行在主服务器
- 直接读写 Redis 路由键

优点：

- 职责边界最清晰
- 风险隔离最好
- 最适合后续给后台或 SSH 隧道复用

缺点：

- 多一个独立服务与 systemd unit

### 5.3 方案 C：继续配置文件 + 定时热同步

做法：

- 仍以 `USER_NODE_ROUTES` 为主
- 增加轮询或重载逻辑把配置重新同步到 Redis

优点：

- 不新增 HTTP 接口

缺点：

- 动态性差
- 依然不适合做人工实时切换
- 容易出现“配置文件、Redis、运行时判断”三份状态漂移

## 6. 推荐方案

推荐采用 `方案 B：单独的轻量 route-admin 服务`。

选择原因：

- 用户已经明确希望优先做 HTTP 管理接口
- 当前运行链路已经拆成 `scanner / dispatcher / executor`，新增管理入口时应继续维持单一职责
- 单独服务可以只部署在主服务器，并只监听 `127.0.0.1` 或内网地址，不会把管理端口混入交易执行节点

## 7. 数据真值与同步语义

这是本次设计的核心约束。

### 7.1 运行时真值

运行时的唯一真值定义为 Redis 路由：

- `route:user_node:{user_id}`

`dispatcher` 在分发时只读取 Redis，不读取环境变量。

### 7.2 `USER_NODE_ROUTES` 的新语义

`USER_NODE_ROUTES` 不再是“每次启动都强制覆盖 Redis”的配置。

改为：

- 只作为冷启动默认值
- 只在 Redis 中该用户路由不存在时写入

也就是说，`dispatcher` 或 `route-admin` 重启后：

- 若 Redis 已有运行时路由，则保留 Redis 值
- 若 Redis 没有该用户路由，才使用 `USER_NODE_ROUTES` 补默认值

### 7.3 动态变更优先级

优先级定义为：

1. HTTP 接口动态写入的 Redis 路由
2. `USER_NODE_ROUTES` 冷启动默认值

这样可以保证：

- 在线路由变更立即生效
- 服务重启不会把动态路由冲掉

## 8. Redis 结构设计

### 8.1 保留的单用户路由键

继续保留：

- `route:user_node:{user_id}`

原因：

- 当前 `dispatcher` 已经依赖这个键
- 保持兼容能减少改动面

### 8.2 新增路由索引集合

新增：

- `route:user_node:index`

内容：

- 保存所有已配置路由的 `user_id`

用途：

- 支持 HTTP 接口高效列出所有路由
- 避免使用 `KEYS route:user_node:*`

### 8.3 写入规则

设置路由时：

1. 写 `route:user_node:{user_id} = node_id`
2. 把 `user_id` 加入 `route:user_node:index`

删除路由时：

1. 删除 `route:user_node:{user_id}`
2. 从 `route:user_node:index` 中移除 `user_id`

## 9. HTTP 服务设计

### 9.1 服务形态

新增一个小型 HTTP 服务，例如：

- `app/runtime/route_admin_service.py`

建议使用 `aiohttp.web` 实现，并在 `requirements.txt` 中显式声明 `aiohttp` 依赖。

原因：

- 当前项目本身就是 `asyncio` 运行模型
- `aiohttp.web` 足够轻量
- 比自己维护原始 HTTP 解析更稳妥

### 9.2 systemd 形态

新增主服务器专用 unit：

- `deploy/systemd/furun-route-admin.service`

只部署在主服务器，不部署到专用执行节点。

### 9.3 启动配置

建议新增配置项：

- `ROUTE_ADMIN_ENABLED`
- `ROUTE_ADMIN_BIND_HOST`
- `ROUTE_ADMIN_PORT`
- `ROUTE_ADMIN_TOKEN`

默认建议：

- `ROUTE_ADMIN_ENABLED=0`
- `ROUTE_ADMIN_BIND_HOST=127.0.0.1`
- `ROUTE_ADMIN_PORT=8787`

说明：

- 首版默认只监听 `127.0.0.1`
- 如确需远程访问，建议通过 SSH 隧道或反向代理暴露，而不是直接公网监听

## 10. HTTP 接口定义

### 10.1 `GET /healthz`

用途：

- 健康检查

返回示例：

```json
{
  "ok": true
}
```

### 10.2 `GET /routes`

用途：

- 列出全部当前路由

返回示例：

```json
{
  "routes": {
    "42": "node-a",
    "99": "main"
  }
}
```

### 10.3 `GET /routes/{user_id}`

用途：

- 查看单个用户当前路由

成功返回示例：

```json
{
  "user_id": "42",
  "node_id": "node-a"
}
```

未命中返回：

- `404 Not Found`

### 10.4 `PUT /routes/{user_id}`

用途：

- 设置或更新某个用户路由

请求体：

```json
{
  "node_id": "node-a"
}
```

成功返回示例：

```json
{
  "ok": true,
  "user_id": "42",
  "node_id": "node-a"
}
```

### 10.5 `DELETE /routes/{user_id}`

用途：

- 删除某个用户路由

成功返回示例：

```json
{
  "ok": true,
  "user_id": "42"
}
```

## 11. 鉴权与访问控制

### 11.1 首版鉴权

首版使用简单 Bearer Token：

- 请求头：`Authorization: Bearer <token>`

若 token 缺失或不匹配：

- 返回 `401 Unauthorized`

### 11.2 首版网络边界

首版默认要求：

- 仅监听 `127.0.0.1`
- 生产或远端联调优先通过 SSH 隧道访问

不建议首版直接公网开放。

### 11.3 非目标

首版不做：

- 用户名密码登录
- 多角色权限
- IP 白名单后台配置

## 12. 运行时事件与审计

路由变更必须输出结构化事件，至少包括：

- `route.admin.updated`
- `route.admin.deleted`
- `route.admin.sync_default_applied`
- `route.admin.unauthorized`

事件 payload 至少包含：

- `user_id`
- `node_id`（删除时可为空）
- `source`，例如 `http_api` 或 `startup_default`

这样可以在 `journalctl` 中追踪：

- 路由是谁改的
- 是启动默认值写入，还是 HTTP 动态修改

## 13. 错误处理

### 13.1 输入校验

以下情况返回 `400 Bad Request`：

- `user_id` 为空
- `node_id` 为空
- 请求体不是合法 JSON

### 13.2 鉴权失败

- 返回 `401 Unauthorized`

### 13.3 Redis 不可用

- 返回 `503 Service Unavailable`

### 13.4 未命中路由

- `GET /routes/{user_id}` 返回 `404`
- `DELETE /routes/{user_id}` 对不存在的路由也返回成功或 `404` 二选一

本次设计明确采用：

- `DELETE` 对不存在路由仍返回 `200 OK`

原因：

- 便于幂等删除

## 14. 测试要求

至少覆盖以下测试：

1. `USER_NODE_ROUTES` 只在缺失时补默认值，不覆盖 Redis 已存在路由
2. `GET /routes` 可返回索引集合中的全部路由
3. `GET /routes/{user_id}` 在存在与不存在时返回正确状态码
4. `PUT /routes/{user_id}` 能写入单用户键与索引集合
5. `DELETE /routes/{user_id}` 能删除单用户键并更新索引集合
6. 缺失或错误 token 返回 `401`
7. Redis 异常时返回 `503`

## 15. 验收标准

完成后至少满足：

1. 主服务器可通过 HTTP 接口在线修改用户到节点路由
2. `dispatcher` 无需重启即可读取新路由
3. `dispatcher` 重启不会覆盖已经动态写入的 Redis 路由
4. `GET /routes` 无需使用 Redis `KEYS`
5. 默认部署不直接暴露公网管理端口
6. 关键变更可从结构化日志中追踪

## 16. 结论

当前系统已经具备“主服务器分发、专用节点执行”的基础架构，下一步不应再通过手工写 Redis 的方式维护用户路由。最合适的演进方式，是新增一个只负责路由管理的轻量 HTTP 服务，并把 `USER_NODE_ROUTES` 降级为冷启动默认值，而把 Redis 明确为运行时真值。

这样既能满足在线调整用户归属节点的需求，又不会破坏当前已经跑通的 `scanner -> dispatcher -> executor` 主链路。
