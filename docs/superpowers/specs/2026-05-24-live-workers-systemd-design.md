# 常驻行情扫描与 Redis 消费者服务化设计

## 1. 文档目标

本文档定义如何将当前已完成短时验证的现货机会扫描链路服务化，目标是把以下运行能力从“一次性脚本验证”升级为“远端可长期运行的生产形态”：

- `ContinuousSpotScanner`
- `RedisSpotConsumer`
- Redis 机会写入与消费链路

本文档只覆盖运行时服务化，不扩展行情算法，不引入新的交易所接入方式，也不改变现有机会计算与套利任务核心逻辑。

## 2. 背景与现状

当前代码已经具备以下能力：

- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中存在常驻扫描器 `ContinuousSpotScanner`
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中存在 Redis 消费者 `RedisSpotConsumer`
- [live_spot_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/live_spot_flow.py) 已能拉取多交易所 ticker、计算最优现货机会并写入 Redis
- [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py) 已能把机会写入 `ZSET + Stream`，并将消息分发给现货套利任务服务
- 远端主机已完成短时验证，确认 `processed_count`、`zset_count`、`stream_count` 均有实际输出

当前缺口不在业务逻辑，而在运行壳层：

- 缺少专门的 worker 启动入口
- 缺少统一环境变量装配方式
- 缺少 `systemd` 单元文件
- 缺少自动拉起、失败重启、日志查看、开机自启的运维约束

## 3. 设计目标

本次服务化满足以下目标：

1. 扫描器与消费者可以作为两个独立 Linux 服务长期运行
2. 两个服务均支持自动重启、开机自启、日志可观测
3. 服务与当前 Python 代码边界清晰，不把运行控制逻辑塞回业务模块
4. 不破坏现有测试与短链路验证能力
5. 便于后续接入飞书/QQ 告警、更多 symbol、更多 region

## 4. 非目标

本次不做以下内容：

- 不升级为 `ccxt.pro` websocket 深度行情
- 不引入 `Redis Streams consumer group`
- 不把 Redis 机会链路改造成多消费者分片模型
- 不引入数据库持久化任务回放
- 不实现完整进程监控平台，仅依赖 `systemd + journalctl`

## 5. 推荐方案

推荐采用“双进程、双 unit、统一 Python worker 入口”的方案。

### 5.1 方案说明

- 新增一个专用 worker 启动模块，例如 `app/runtime/worker_service.py`
- 启动模块只负责：
  - 加载配置
  - 创建 Redis 客户端
  - 读取交易所凭证与代理配置
  - 根据 `role=scanner|consumer` 构造对应对象
  - 托管长期运行循环与退出清理
- 部署层新增两个 `systemd` unit：
  - `furun-spot-scanner.service`
  - `furun-spot-consumer.service`

### 5.2 选择原因

- 与现有代码边界最一致，改动最小
- 扫描器和消费者故障域分离，单个服务失败不会直接拖垮另一个
- 后续可以单独扩容消费者，而不影响扫描器
- 便于在多区域部署时做按区域拆分，例如 `scanner@sg`、`consumer@sg`

### 5.3 未选方案

单进程内同时跑扫描器和消费者也可行，但不推荐，原因如下：

- 任一子循环异常会影响整进程
- 日志难按职责拆分
- 运维时无法单独重启某一侧
- 后续扩展为多实例时边界不清晰

## 6. 逻辑架构

### 6.1 运行组件

新增或约定的运行组件如下：

1. `WorkerSettings`
2. `CredentialLoader`
3. `WorkerApp`
4. `ScannerRunner`
5. `ConsumerRunner`
6. `systemd unit files`

### 6.2 组件职责

#### `WorkerSettings`

负责从环境变量中解析运行参数，至少包括：

- `redis_url`
- `env_mode`
- `spot_symbol`
- `spot_exchanges`
- `scanner_poll_interval_seconds`
- `consumer_block_ms`
- `worker_role`
- `worker_region`

如果后续需要支持多 symbol，可扩展为 `spot_symbols` 列表，但首版先保持单 symbol，降低服务化复杂度。

#### `CredentialLoader`

负责从环境变量或本地部署文件读取：

- 各交易所 API Key
- secret
- password/passphrase
- 每交易所代理配置

该组件不负责密钥加密解密体系升级，只负责把当前已有凭证装配为 `ExchangeCredentials` 与 `proxies_by_exchange`。

#### `WorkerApp`

负责统一资源生命周期：

- 创建 Redis 客户端
- 创建 `ExchangeClientFactory`
- 创建 `SpotArbitrageProbeService`
- 按角色创建 runner
- 捕获顶层异常并返回非零退出码
- 在退出时关闭 Redis 连接

#### `ScannerRunner`

只负责构造并运行：

- `LiveSpotFlowService`
- `ContinuousSpotScanner`

以无限循环模式调用 `scanner.run(..., max_iterations=None)`。

#### `ConsumerRunner`

只负责构造并运行：

- `SpotArbitrageProbeService`
- `RedisOpportunityDispatcher`
- `RedisSpotConsumer`

以无限循环模式调用 `consumer.run(..., max_iterations=None)`。

## 7. 启动入口设计

### 7.1 CLI 入口

首版推荐新增独立入口，而不是过度复用当前 [main.py](file:///d:/old/FuRunSystemV4/main.py) 的 `all/scanner/trader` 三态参数。

建议新增如下调用方式：

```bash
.venv/bin/python -m app.runtime.worker_service --role scanner
.venv/bin/python -m app.runtime.worker_service --role consumer
```

原因：

- 当前 `main.py` 更偏向总入口与服务选择，不适合直接承载 worker 服务化细节
- 独立模块能避免把部署脚本、环境变量装配、长期循环管理混入通用入口
- 后续如果要接 `systemd template unit` 或 region 参数，更易扩展

### 7.2 参数与环境变量优先级

优先级定义如下：

1. CLI 显式参数
2. 环境变量
3. `Settings` 默认值

这样便于本地调试时快速覆盖，但线上部署仍以环境文件为主。

## 8. systemd 设计

### 8.1 Unit 划分

定义两个 unit：

- `furun-spot-scanner.service`
- `furun-spot-consumer.service`

两个 unit 使用相同工作目录与环境文件，但 `ExecStart` 中的 `--role` 不同。

### 8.2 Unit 关键约束

每个 unit 至少包含以下约束：

- `WorkingDirectory=/home/ubuntu/furunsystemv4/current`
- `EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker`
- `ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role <role>`
- `Restart=always`
- `RestartSec=3`
- `User=ubuntu`
- `WantedBy=multi-user.target`

### 8.3 日志策略

首版日志直接交给 `journald`：

- 标准输出进入 `journalctl`
- 标准错误进入 `journalctl`
- 业务代码中补充关键 lifecycle 日志，例如：
  - worker start
  - scanner iteration success/failure
  - consumer processed message count
  - redis reconnect
  - graceful shutdown

首版不额外引入文件日志轮转，避免重复维护日志通道。

## 9. 数据流与故障恢复

### 9.1 Scanner 服务数据流

1. `systemd` 拉起 scanner unit
2. `worker_service` 读取配置与凭证
3. 创建 `LiveSpotFlowService`
4. 创建 `ContinuousSpotScanner`
5. 按固定轮询周期拉取 ticker、计算机会、写入 Redis
6. 如果单次轮询报错，记录日志并进入短暂 sleep，再继续循环
7. 如果顶层进程崩溃，由 `systemd` 自动重启

### 9.2 Consumer 服务数据流

1. `systemd` 拉起 consumer unit
2. `worker_service` 读取配置与凭证
3. 创建 `RedisSpotConsumer`
4. 从 `stream:spot_opps` 持续 `xread`
5. 分发给 `SpotArbitrageProbeService`
6. 成功时推进 `last_id`
7. 如果单条消息处理失败，记录日志并继续处理后续消息
8. 如果顶层进程崩溃，由 `systemd` 自动重启

### 9.3 `last_id` 策略

首版保持当前内存态 `last_id` 策略，不做 Redis 持久化 offset。

这意味着：

- 进程重启后会从 `0-0` 重新读取
- 存在重复消费风险
- 但当前链路主要用于机会触发与模拟盘排演，可接受

为了让首版可控，要求 `RedisOpportunityDispatcher` 以及其下游执行链路具备幂等或近幂等容忍能力。后续若切换到真实常驻交易，优先升级为：

- Redis consumer group
- 外部 offset 持久化
- 幂等键去重

## 10. 错误处理设计

### 10.1 可恢复错误

以下错误定义为可恢复错误，发生后应记录日志并重试：

- Redis 临时断连
- 单次 ticker 拉取超时
- 个别交易所 API 报错
- 单条 stream 消息处理异常

处理策略：

- 捕获异常
- 记录 exchange、symbol、role、异常摘要
- `asyncio.sleep()` 短暂退避后继续

### 10.2 不可恢复错误

以下错误定义为不可恢复错误，由进程直接退出并交给 `systemd` 拉起：

- 必需环境变量缺失
- Redis URL 非法且无法初始化客户端
- 凭证装配失败导致服务无法启动
- 关键依赖对象初始化失败

### 10.3 停机策略

服务接收到 `SIGTERM` 时应：

1. 停止新的循环迭代
2. 等待当前 Redis 或交易所请求自然结束
3. 关闭 Redis 客户端
4. 输出 shutdown 完成日志

这样可以保证 `systemctl stop` 行为可预测。

## 11. 配置设计

### 11.1 环境文件

推荐远端维护单独环境文件：

- `/home/ubuntu/furunsystemv4/current/.env.worker`

建议包含：

- `REDIS_URL`
- `SPOT_SYMBOL`
- `SPOT_EXCHANGES`
- `ENV_MODE`
- `SCANNER_POLL_INTERVAL_SECONDS`
- `CONSUMER_BLOCK_MS`
- 各交易所凭证
- 各交易所代理配置

### 11.2 默认值策略

建议默认值如下：

- `ENV_MODE=testnet`
- `SPOT_SYMBOL=BTC/USDT`
- `SPOT_EXCHANGES=okx,bitget,gate`
- `SCANNER_POLL_INTERVAL_SECONDS=1.0`
- `CONSUMER_BLOCK_MS=1000`

首版延续当前短时验证使用的交易所组合，避免一次切换过多变量。

## 12. 验收标准

服务化完成后，以以下标准验收：

1. 本地测试继续全绿
2. 远端可以成功执行 `systemctl daemon-reload`
3. `furun-spot-scanner.service` 能启动并保持 `active (running)`
4. `furun-spot-consumer.service` 能启动并保持 `active (running)`
5. `journalctl -u furun-spot-scanner.service -n 50` 可看到周期扫描日志
6. `journalctl -u furun-spot-consumer.service -n 50` 可看到消息消费日志
7. Redis 中 `arb:zset:spot` 与 `stream:spot_opps` 持续有新增数据
8. 停止某一个服务后可被正常重启，不影响另一个服务继续运行

## 13. 测试策略

### 13.1 单元测试

补充以下测试方向：

- worker 配置解析
- `role=scanner|consumer` 的 runner 选择
- 顶层资源清理逻辑
- 启动参数优先级

### 13.2 集成验证

保留远端短时验证之外，再增加：

- `systemd` 启停验证
- 重启后服务状态验证
- Redis 数据持续增长验证

### 13.3 暂不测试项

首版不做：

- `systemd` 在 pytest 中的自动化仿真
- 真实主网长时间 soak test
- 多 symbol 并发压测

## 14. 后续演进

本次服务化完成后，下一批自然演进方向如下：

1. 接入飞书/QQ 异常告警
2. 引入 Redis consumer group 与 offset 持久化
3. 升级为 websocket/orderbook 深度机会流
4. 按 region 与 symbol 拆分多实例 worker
5. 接入数据库任务持久化与恢复

## 15. 结论

本次推荐采用“双独立 worker + 独立 Python 启动入口 + 双 systemd unit”的方案，在不重写现有业务逻辑的前提下，把已经验证可用的扫描与消费链路升级为可长期运行、可重启、可观测的服务形态。

该方案改动聚焦、风险较低，并且为后续告警、深度行情和多区域扩展预留了清晰演进路径。
