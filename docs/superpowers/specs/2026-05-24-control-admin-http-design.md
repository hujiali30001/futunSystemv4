# 管理控制面 HTTP 接口设计

## 1. 文档目标

本文档定义一个仅用于“管理控制面最小闭环”的轻量 HTTP 服务设计。

本次目标是把总设计文档中的 `admin-control-plane` 先以最小可落地形态接入当前已经跑通的运行链路：

- 管理员可在线维护额度规则
- 管理员可在线维护平台开关
- 管理员可发布和查询公告
- `dispatcher` 与 `executor` 都能读取同一套运行时控制规则

本文档只覆盖当前项目已有 `scanner -> dispatcher -> executor` 主链路上的控制面补齐，不重写总套利系统，也不一次性引入完整后台、数据库审计和用户公告回执体系。

## 2. 范围

本次只做以下能力：

- 提供独立 `control-admin` HTTP 服务
- 提供额度规则的查询、写入、删除接口
- 提供平台开关的查询、写入、删除接口
- 提供公告的查询、创建、更新、删除接口
- 使用 Redis 作为运行时真值
- 让 `dispatcher` 与 `executor` 双层读取并执行同一套控制规则
- 对关键管理动作输出结构化运行事件

本次不做以下能力：

- 不做 Web 后台页面
- 不做数据库真值持久化
- 不做多管理员权限系统
- 不做公告已读、强制确认与回执统计
- 不做复杂审批流
- 不做跨区域多主同步
- 不做完整的强制平仓任务编排

## 3. 背景与现状

当前项目已经具备以下条件：

- [control_plane.py](file:///d:/old/FuRunSystemV4/app/admin/control_plane.py) 已有内存级 `ControlPlane`、`LimitRule`、`PlatformSwitch`
- [notifier.py](file:///d:/old/FuRunSystemV4/app/admin/notifier.py) 已有最小公告发布抽象
- [models.py](file:///d:/old/FuRunSystemV4/models.py) 已定义 `RiskLimitRule` 与 `Announcement` ORM 模型
- [route_admin_service.py](file:///d:/old/FuRunSystemV4/app/runtime/route_admin_service.py) 已验证“独立 HTTP 管理服务 + Bearer Token + Redis 真值”的实现路径可行
- 当前运行主链路已经切为 `scanner + dispatcher` 在主服务器、`executor` 在专用节点

当前缺口主要有四个：

1. 额度规则仍停留在内存对象，缺少正式运行时真值
2. 平台开关没有独立管理入口，无法在线切换
3. 公告只有最小通知抽象，没有正式管理接口
4. `dispatcher` 与 `executor` 还没有统一接入同一套控制面真值

因此，这一步不能只做“再包一个 HTTP 接口”，还必须同时定义：

- 控制面哪份数据是运行时真值
- 哪一层负责首次拦截，哪一层负责最终兜底
- 公告能力这一步做到什么边界

## 4. 问题定义

如果继续维持当前状态，会有以下问题：

1. 管理员无法在线调整额度与平台开关，只能改代码、改配置或临时脚本写 Redis
2. 只有单层校验会导致规则变更窗口内出现拦截空隙
3. 公告能力无法作为后续管理员后台的稳定后端基础
4. 总设计文档中的 `admin-control-plane` 会长期停留在概念层，而不是可部署服务

用户当前已明确：

- 继续沿现有 backend 主线一步步推进
- 本步控制面优先采用 `HTTP API`
- 额度规则与平台开关先同时接入 `dispatcher` 与 `executor`

因此，本次设计必须围绕“最小可部署、最小可验证、可向后扩展”来收敛。

## 5. 设计目标

本次设计满足以下目标：

1. 管理员可在运行中在线维护额度规则、平台开关与公告
2. `dispatcher` 与 `executor` 使用同一套 Redis 真值进行双层校验
3. 规则变更后无需重启主链路即可生效
4. 当前部署拓扑只新增一个独立管理服务，不把管理逻辑塞回工作进程
5. API、配置、部署风格尽量复用现有 `route-admin`

## 6. 方案比较

### 6.1 方案 A：Redis 真值 + 独立 `control-admin` HTTP 服务

做法：

- 新增单独的 `control-admin` 服务
- 额度规则、平台开关、公告都直接存 Redis
- `dispatcher` 与 `executor` 在运行时直接从 Redis 加载或读取控制面真值

优点：

- 与当前 `route-admin` 模式最一致
- 上线最快，最适合当前 backend 主线节奏
- 不引入数据库依赖，远端验证链路最短

缺点：

- 审计与复杂查询能力较弱
- 未来接数据库时需要再补正式持久化边界

### 6.2 方案 B：数据库真值 + 独立 HTTP 服务

做法：

- 直接以关系库为额度规则、平台开关、公告的唯一真值
- `dispatcher` 与 `executor` 按需查询数据库或加载缓存

优点：

- 与总设计文档的长期数据模型更一致
- 后续后台查询、审计、权限更自然

缺点：

- 当前项目还未把数据库链路接到运行主线
- 本步跨度过大，会把任务从“控制面闭环”扩成“控制面 + 数据层接入”

### 6.3 方案 C：数据库真值 + Redis 运行态投影

做法：

- 管理接口写数据库
- 数据库变更再同步到 Redis，供运行时消费

优点：

- 最接近长期生产形态
- 同时满足持久化、审计和运行时低延迟读取

缺点：

- 本步范围最大
- 会引入双写一致性与投影同步问题

## 7. 推荐方案

推荐采用 `方案 A：Redis 真值 + 独立 control-admin HTTP 服务`。

选择原因：

- 用户已明确希望这一步先走 `HTTP API`
- 当前项目已有 [route_admin_service.py](file:///d:/old/FuRunSystemV4/app/runtime/route_admin_service.py) 可复用的实现风格
- 这一步的核心目标是把管理控制面跑通，而不是立即完成长期数据库架构
- 先把 Redis 控制面闭环打通，后续可以平滑升级到“数据库真值 + Redis 投影”

## 8. 目标运行架构

### 8.1 服务划分

本次新增一个独立服务：

- `control-admin`

职责：

- 接受管理员 HTTP 请求
- 读写控制面 Redis 真值
- 输出控制面结构化事件

不负责：

- 不直接参与公共机会扫描
- 不直接负责用户任务分发
- 不直接执行交易

### 8.2 控制面与运行时的关系

运行主链路保持：

- 主服务器运行 `scanner + dispatcher`
- 执行节点运行 `executor`

控制面生效路径为：

1. 管理员调用 `control-admin` 写入 Redis 真值
2. `dispatcher` 在创建或分发用户级执行任务前进行首次校验
3. `executor` 在真正执行前再次校验同一套规则

这样可以保证：

- 前置过滤尽量发生在主服务器
- 即使规则在任务分发后被改严，执行层仍可兜底拦截

## 9. 控制面对象设计

### 9.1 额度规则 `limit rule`

额度规则用于计算某次开仓请求允许的最大名义金额。

本次至少支持以下层级：

- 平台总额度
- 用户总额度
- 用户按币种额度
- 用户按交易所额度
- 用户按策略额度
- 单次任务额度

本次至少支持以下字段：

- `rule_id`
- `scope_type`
- `scope_id`
- `symbol`
- `exchange`
- `strategy_id`
- `limit_type`
- `limit_value`
- `enabled`
- `priority`
- `updated_at`

约束：

- 本次只支持控制“开新仓额度”
- `limit_type` 先统一为 `max_notional`
- `limit_value <= 0` 视为禁止新开仓

### 9.2 平台开关 `platform switch`

平台开关用于表达是否允许某范围继续开新仓，或是否进入只减仓状态。

本次至少支持以下开关：

- `platform.reduce_only`
- `platform.disable_open`
- `exchange.disable_open`
- `symbol.disable_open`
- `user.disable_open`

本次至少支持以下字段：

- `switch_key`
- `enabled`
- `scope_type`
- `scope_id`
- `updated_at`

说明：

- `reduce_only` 表示禁止新增开仓，但允许减仓、平仓
- `disable_open` 表示禁止该范围继续新开仓
- 本次不实现“强制平仓任务下发”，只保留后续扩展位

### 9.3 公告 `announcement`

公告用于管理端统一维护站内公告与外部推送的消息主体。

本次至少支持以下字段：

- `announcement_id`
- `title`
- `content`
- `priority`
- `is_pinned`
- `audience_type`
- `audience_filter`
- `channels`
- `status`
- `updated_at`

本次支持的状态：

- `draft`
- `active`
- `inactive`

说明：

- 本次公告能力只做到“发布 / 查询 / 上下线状态”
- 不做已读状态
- 不做强制确认
- 不做回执统计

## 10. 运行时真值与生效语义

### 10.1 真值定义

本次控制面的运行时唯一真值定义为 Redis 中的控制面键。

意味着：

- `dispatcher` 与 `executor` 不再依赖硬编码的内存规则作为真值
- 进程重启后只要 Redis 数据仍在，控制面状态即可保留

### 10.2 双层校验语义

#### `dispatcher`

在把公共机会转成用户级执行任务之前执行：

- 平台开关判断
- 额度规则计算

处理结果：

- 若明确禁止开仓，则不再创建或投递执行任务
- 若允许但需要缩量，则以收敛后的 `approved_notional` 创建执行任务

#### `executor`

在真正下单前再次执行：

- 平台开关判断
- 额度规则计算

处理结果：

- 若被新规则拦截，则拒绝执行并发出运行事件
- 若允许但额度进一步降低，则以更小的 `approved_notional` 执行

### 10.3 双层接入的必要性

只接 `dispatcher` 的问题：

- 规则在任务分发后变更时，执行层无兜底

只接 `executor` 的问题：

- 主服务器无法提前过滤无效任务，浪费分发资源

因此本次明确采用：

- `dispatcher` 首次过滤
- `executor` 最终兜底

## 11. 额度计算规则

### 11.1 命中范围

一次开仓请求的额度计算上下文至少包括：

- `user_id`
- `strategy_id`
- `symbol`
- `exchange`
- `requested_notional`

### 11.2 收敛规则

本次沿用并正式化当前 [control_plane.py](file:///d:/old/FuRunSystemV4/app/admin/control_plane.py) 的最小思想：

- 对所有命中的额度规则取最小值
- 最终 `approved_notional = min(requested_notional, 所有命中额度值)`

若没有命中额度规则：

- 默认按 `requested_notional` 放行

若 `approved_notional <= 0`：

- 视为禁止开仓

### 11.3 与总设计文档的一致性

总设计文档定义的长期优先级为：

- 平台总额度
- 用户总额度
- 用户按币种额度
- 用户按交易所额度
- 用户按策略额度
- 单次任务额度

本次不单独实现一套复杂优先级求解器，而是将其收敛为：

- 所有命中规则取最小允许值

这样既满足长期语义，也与当前代码实现路径一致。

## 12. Redis 结构设计

### 12.1 额度规则

新增：

- `control:limits:index`
- `control:limits:{rule_id}`

其中：

- `control:limits:index` 保存全部 `rule_id`
- `control:limits:{rule_id}` 保存单条规则 JSON

### 12.2 平台开关

新增：

- `control:switches:index`
- `control:switches:{switch_key}:{scope_type}:{scope_id}`

其中：

- `control:switches:index` 保存全部开关键 ID
- 单条开关记录按 `switch_key + scope_type + scope_id` 唯一标识

### 12.3 公告

新增：

- `control:announcements:index`
- `control:announcements:{announcement_id}`

其中：

- `control:announcements:index` 保存全部公告 ID
- 单条公告以 JSON 保存

### 12.4 为什么使用索引集合

原因：

- 支持管理接口高效列举全部对象
- 避免使用 `KEYS control:*`
- 与当前路由索引设计风格一致

## 13. HTTP 服务设计

### 13.1 服务形态

新增一个小型 HTTP 服务，例如：

- `app/runtime/control_admin_service.py`

建议：

- 使用 `aiohttp.web`
- 复用 `route-admin` 的 `Bearer Token` 鉴权模式
- 默认只监听 `127.0.0.1` 或内网地址

### 13.2 鉴权

接口除了 `/healthz` 外全部要求：

- `Authorization: Bearer <token>`

要求：

- token 为空时服务拒绝启动
- 未授权请求返回 `401`
- 同时输出未授权结构化事件

### 13.3 接口列表

#### 健康检查

- `GET /healthz`

#### 额度规则

- `GET /control/limits`
- `GET /control/limits/{rule_id}`
- `PUT /control/limits/{rule_id}`
- `DELETE /control/limits/{rule_id}`

#### 平台开关

- `GET /control/switches`
- `GET /control/switches/{switch_id}`
- `PUT /control/switches/{switch_id}`
- `DELETE /control/switches/{switch_id}`

说明：

- `switch_id` 由 `switch_key:scope_type:scope_id` 组成

#### 公告

- `GET /announcements`
- `GET /announcements/{announcement_id}`
- `POST /announcements`
- `PUT /announcements/{announcement_id}`
- `DELETE /announcements/{announcement_id}`

### 13.4 JSON 语义

约定：

- 查询接口返回统一对象列表或单对象
- 写入接口返回 `ok: true` 与实际生效对象
- 删除接口返回 `ok: true` 与被删除对象标识
- 参数非法返回 `400`
- Redis 不可用返回 `503`

## 14. 结构化事件

建议新增以下运行事件：

- `control.admin.limit.updated`
- `control.admin.limit.deleted`
- `control.admin.switch.updated`
- `control.admin.switch.deleted`
- `control.admin.announcement.created`
- `control.admin.announcement.updated`
- `control.admin.announcement.deleted`
- `control.admin.unauthorized`
- `control.rule.blocked`
- `control.rule.resized`

语义：

- `control.admin.*` 用于记录管理员操作
- `control.rule.*` 用于记录运行时拦截或缩量

## 15. 配置与部署

### 15.1 配置项

建议在 [worker_config.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_config.py) 增加控制面服务配置，例如：

- `control_admin_enabled`
- `control_admin_bind_host`
- `control_admin_port`
- `control_admin_token`

### 15.2 部署形态

建议新增：

- `deploy/systemd/furun-control-admin.service`

部署原则：

- 仅主服务器运行
- 默认监听 `127.0.0.1`
- 需要远端访问时通过 SSH 隧道或内网反向代理暴露

## 16. 测试与验证

本次实现完成后，至少需要以下验证：

1. 控制面存储单元测试
2. `control-admin` HTTP 行为测试
3. `dispatcher` 侧规则命中、阻断、缩量测试
4. `executor` 侧二次阻断、二次缩量测试
5. `systemd` 资产与配置样例测试
6. 主服务器本地接口验证
7. 远端主服务器实际部署验证

关键验收场景：

- 平台 `reduce_only` 开启后，`dispatcher` 与 `executor` 都拒绝新开仓
- 用户额度低于请求额度时，任务被自动缩量
- `dispatcher` 放行后，若规则被改严，`executor` 仍能拒绝执行
- 公告可以创建、查询、更新、下线

## 17. 与长期架构的衔接

本次设计是总设计文档中 `admin-control-plane` 的最小可运行切片。

后续可平滑演进为：

1. 数据库作为正式真值
2. Redis 仅保留运行态投影
3. 公告增加已读、强制确认、外部推送回执
4. 平台开关扩展到强制减仓、强制平仓动作下发
5. 接入管理员后台页面

## 18. 结论

本次采用 `Redis 真值 + 独立 control-admin HTTP 服务 + dispatcher/executor 双层校验`。

这样可以在不偏离当前 backend 主线的前提下，最小代价把以下三项能力正式落地：

- 管理员额度规则
- 平台开关
- 公告管理

并为后续数据库控制面、Web 后台和更完整的管理治理能力保留清晰升级路径。
