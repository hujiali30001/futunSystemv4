# 五大交易所全链接入收口设计

## 1. 文档目标

本文档定义本轮新主线目标：将 `okx / binance / bybit / bitget / gate` 五所全部接入现有套利主链（scanner → dispatcher → executor → repair），做到全链可运行闭环。

B1 自动恢复主线已在 B1-5E 完成后暂告段落，本轮不再继续深挖自动恢复链，而是把主线切回交易所接入。

## 2. 范围

本次做：

- 统一全局配置、worker 配置、env 样例、systemd 文档中的交易所口径为五所
- 补齐主链默认值，确保 scanner / dispatcher / executor / repair 启动后都能覆盖五所
- 补齐五所都至少经过 `ExchangeAdapter` / session 工厂的 smoke 路径，不要求首轮深度差异化适配
- 确保 `sandbox_probe` 和 `spot_arbitrage_probe` 的默认交易所列表与主链一致
- 确保五所环境变量凭证加载已覆盖 `BINANCE_*`、`BYBIT_*`、`BITGET_*`、`GATE_*`、`OKX_*`
- 补齐最小测试矩阵，锁住五所配置口径与 session 创建

本次不做：

- 不做单所深度适配（如单独处理某所的风控规则、特有订单类型、特有错误码）
- 不新增交易所级熔断、路由或调度系统
- 不改 `ExchangeAdapter` / `live_spot_flow` / `trade_execution_service` 的核心执行逻辑
- 不引入新的交易所接入框架层
- 不改变现有 Redis stream / DB 表结构
- 不放大远端系统联调，本轮仅完成本地可运行闭环

## 3. 背景与现状

### 3.1 当前五所现状（口径裂缝）

全局配置层已经是五所：

- [settings.py](file:///d:/old/FuRunSystemV4/app/core/settings.py#L19-L21)：
  - `enabled_exchanges = ["okx", "binance", "bybit", "bitget", "gate"]`

但 worker 主链仍然偏三所：

- [worker_config.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_config.py#L22-L24)：
  - `spot_exchanges` 默认 `["okx", "bitget", "gate"]`
- [test_worker_config.py](file:///d:/old/FuRunSystemV4/tests/test_worker_config.py#L11-L18)：
  - 所有 CSV 解析测试只验证 `okx,bitget,gate`
- [test_systemd_assets.py](file:///d:/old/FuRunSystemV4/tests/test_systemd_assets.py#L63)：
  - `SPOT_EXCHANGES=okx,bitget,gate`
- [live-workers-systemd.md](file:///d:/old/FuRunSystemV4/docs/ops/live-workers-systemd.md#L130)：
  - env 样例中 `SPOT_EXCHANGES=okx,bitget,gate`
  - 虽然文档提及 `五大交易所模拟盘apikey.txt`，但从 `local-secrets` 生成 env 的 PowerShell 脚本也只写了 `OKX/BITGET/GATE`，没有 `BINANCE/BYBIT`

### 3.2 探针层已是五所

- [sandbox_probe.py](file:///d:/old/FuRunSystemV4/app/runtime/sandbox_probe.py#L167-L170)：
  - `SANDBOX_PROBE_EXCHANGES` 默认值 `"binance,okx,bybit,bitget,gate"`
- [spot_arbitrage_probe.py](file:///d:/old/FuRunSystemV4/app/runtime/spot_arbitrage_probe.py#L115-L116)：
  - `post_only` 特判只有 `okx,gate,gateio`，未覆盖 `binance/bybit/bitget`

### 3.3 session / adapter 底层已经通用

- [session_manager.py](file:///d:/old/FuRunSystemV4/app/exchanges/session_manager.py#L56-L88)：
  - `ExchangeClientFactory` 使用 `getattr(ccxt_module, exchange)` 动态创建 session
  - 架构上支持任意 ccxt 支持的交易所，不限于三所
- [adapters.py](file:///d:/old/FuRunSystemV4/app/exchanges/adapters.py)：
  - `ExchangeAdapter` 是通用封装，不绑定特定交易所

### 3.4 现有测试覆盖

- [tests/test_sessions.py](file:///d:/old/FuRunSystemV4/tests/test_sessions.py)：
  - session 测试已经用 `binance` / `okx` 覆盖
- [tests/test_worker_config.py](file:///d:/old/FuRunSystemV4/tests/test_worker_config.py)：
  - 凭证加载测试覆盖 `okx` / `gate`
- [tests/test_sandbox_probe.py](file:///d:/old/FuRunSystemV4/tests/test_sandbox_probe.py)：
  - 待确认覆盖范围
- [tests/test_live_workers.py](file:///d:/old/FuRunSystemV4/tests/test_live_workers.py)：
  - 套利主链已有 dispatcher / executor / repair 回归

### 3.5 总结：核心问题

`binance` 和 `bybit` 虽然已在全局 `settings.py` 中声明为 `enabled_exchanges`，且在探针层已出现，但在以下位置仍缺失：

1. worker 主链默认交易所列表（`worker_config.py`）
2. env 样例文件（`.env.worker.example`）
3. systemd 部署文档中的 env 模板
4. 文档中的本地生成 env 脚本
5. 测试中的交易所列表断言
6. `spot_arbitrage_probe` 中的 `post_only` 映射

## 4. 问题定义

### 4.1 口径不一致

同一个项目内存在两套交易所口径：

- 全局配置说五所
- worker 默认只说三所
- 测试锁在三所
- 文档样例锁在三所

这意味着在当前 main 分支上：

- 不显式设置 `SPOT_EXCHANGES=okx,binance,bybit,bitget,gate` 时，`scanner` 只会扫三所
- 即便显式设置了五所，测试断言仍然只验证三所，无法自动保护五所口径
- 新增 `binance/bybit` 账户后，env 样例无法给出正确的配置引导

### 4.2 binance / bybit 在主链覆盖偏薄

当前：

- 没有针对 `binance` 或 `bybit` 的专门错误处理或特判
- 没有人验证过这两个所的 sandbox 模式是否能在现有 `ExchangeClientFactory` 下正常创建 session
- 任何基于 ccxt 行为的差异化（如 `post_only`、`reduce_only`、`set_sandbox_mode` 签名差异）对这两所未经测试

### 4.3 部署文档与 local-secrets 生成脚本落后

- `live-workers-systemd.md` 中用于生成 `.env.worker` 的 PowerShell 脚本只提取 `OKX/BITGET/GATE` 凭证
- 虽然 `local-secrets/五大交易所模拟盘apikey.txt` 应该包含五所凭证，但脚本没有覆盖 `BINANCE/BYBIT`

## 5. 设计目标

1. 统一全局配置、worker 配置、env 样例、测试断言四层交易所口径为五所
2. `binance` 和 `bybit` 能通过现有 `ExchangeClientFactory.create_session()` 创建测试环境 session
3. `sandbox_probe` 和 `spot_arbitrage_probe` 的默认交易所与主链一致
4. 所有 env 模板与部署文档中的交易所列表更新为五所
5. 测试矩阵锁住五所口径，不再只验三所
6. 已有主链行为（scanner / dispatcher / executor / repair）不回归

## 6. 方案比较

### 6.1 方案 A：最小对齐收口（推荐）

做法：

- `worker_config.py` 的 `spot_exchanges` 默认值改为五所
- `.env.worker.example` 中 `SPOT_EXCHANGES` 改为五所
- 部署文档中 env 模板与生成脚本更新为五所
- 测试中交易所列表断言更新为五所
- `spot_arbitrage_probe.py` 中 `post_only` 映射补上 `binance/bybit/bitget`
- 五所 session 创建最少 smoke 测试

优点：

- 改动范围可控，不引入新框架或架构
- 与现有 ccxt session 工厂模式完全兼容
- 可以最短路径验证五所可运行闭环

缺点：

- 不解决各所的深度差异化问题（留待后续）

### 6.2 方案 B：逐所深度适配

优点：

- 长期最完整

缺点：

- 本轮范围过大
- 在当前"先跑通"目标下过于超前

### 6.3 方案 C：只补配置不改代码

优点：

- 改动最小

缺点：

- 测试仍然锁在三所，口径容易滑回三所
- 部署文档与生成脚本依然落后

### 6.4 推荐方案

采用方案 A。

## 7. 核心设计

### 7.1 配置层：统一五所口径

#### worker_config.py

`spot_exchanges` 默认值从三所改为五所：

```python
spot_exchanges: Annotated[list[str], NoDecode] = Field(
    default_factory=lambda: ["okx", "binance", "bybit", "bitget", "gate"]
)
```

#### .env.worker.example

`SPOT_EXCHANGES` 默认值改为五所：

```
SPOT_EXCHANGES=okx,binance,bybit,bitget,gate
```

同时补齐 `BINANCE_*`、`BYBIT_*` 凭证占位：

```
BINANCE_API_KEY=
BINANCE_SECRET=
BINANCE_PASSWORD=
BYBIT_API_KEY=
BYBIT_SECRET=
BYBIT_PASSWORD=
```

#### live-workers-systemd.md

- 更新所有 `SPOT_EXCHANGES=okx,bitget,gate` 为 `okx,binance,bybit,bitget,gate`
- 更新本地生成 env 的 PowerShell 脚本，从 `五大交易所模拟盘apikey.txt` 提取 `BINANCE/BYBIT` 凭证
- 更新凭证与环境变量说明，提及五所凭证来源

### 7.2 探针层：补齐 post_only 映射

- [spot_arbitrage_probe.py](file:///d:/old/FuRunSystemV4/app/runtime/spot_arbitrage_probe.py#L115-L116)

当前 `post_only` 只对 `okx,gate,gateio` 生效。本轮补上五所完整映射，确保 binance / bybit / bitget 在创建限价单时的行为可控。

建议最小规则（基于 ccxt 已知行为）：

- `okx`：`post_only=True`
- `binance`：`post_only=True`
- `bybit`：`post_only=True`
- `bitget`：`post_only=True`
- `gate` / `gateio`：`post_only=True`

实际上五所限价单在 sandbox 场景下建议统一启用 `post_only`，简化差异。

### 7.3 session 层：确认五所均可创建

现有 `ExchangeClientFactory.create_session()` 使用 `getattr(ccxt_module, exchange)`，因此只要 ccxt 支持该 exchange_id，创建路径统一。

需要确认的关键点：

- `binance` 在 ccxt 中 exchange_id 是 `binance`
- `bybit` 在 ccxt 中 exchange_id 是 `bybit`
- 两所的 `set_sandbox_mode(True)` 行为是否与已测试的 `okx/bitget/gate` 一致

本轮不改变 factory 逻辑，只补最少 smoke 测试。

### 7.4 凭证层：确认五所凭证可从 env 加载

- [worker_config.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_config.py#L142-L149)

`load_exchange_credential_from_env()` 按 `{PREFIX}_API_KEY` / `{PREFIX}_SECRET` / `{PREFIX}_PASSWORD` 规则加载，因此：

- `BINANCE_API_KEY` → 加载 binance 凭证
- `BYBIT_API_KEY` → 加载 bybit 凭证

本轮不需要改加载逻辑，只需要确认 env 模板和测试覆盖了五个前缀。

### 7.5 测试层：端口五所口径

需要更新的测试：

| 文件 | 更新内容 |
|------|----------|
| [test_worker_config.py](file:///d:/old/FuRunSystemV4/tests/test_worker_config.py) | CSV 解析测试改为五所；补充 `binance/bybit` 凭证加载测试 |
| [test_systemd_assets.py](file:///d:/old/FuRunSystemV4/tests/test_systemd_assets.py) | `SPOT_EXCHANGES` 断言改为五所 |
| [test_sessions.py](file:///d:/old/FuRunSystemV4/tests/test_sessions.py) | 已有 `binance` session 测试，补充 `bybit` session 创建测试 |
| [test_sandbox_probe.py](file:///d:/old/FuRunSystemV4/tests/test_sandbox_probe.py) | 确认默认交易所为五所 |
| [test_live_workers.py](file:///d:/old/FuRunSystemV4/tests/test_live_workers.py) | 已有套利主链回归，本轮不改行为，确认不回归即可 |

## 8. 数据流

本轮不改任何数据流。scanner → dispatcher → executor → repair 的主链逻辑不变，只是 scanner 默认扫描范围从三所扩展到五所。

## 9. 错误处理

### 9.1 未配置凭证的交易所不应导致启动失败

`load_exchange_credential_from_env()` 在缺少 `API_KEY/SECRET` 时返回 `None`，由调用方决定是否跳过。

本轮不改变该行为。

### 9.2 ccxt exchange_id 映射

如果 `binance` 或 `bybit` 在 ccxt 中的实际 exchange_id 与预期不同，`ExchangeClientFactory.create_session()` 会在 `getattr(ccxt_module, exchange)` 处抛出 `AttributeError`。

这一路径与当前三所行为完全一致，无需特判。

### 9.3 sandbox 模式

如果某所在 ccxt 中没有 `set_sandbox_mode` 方法，`ExchangeClientFactory.create_session()` 的 `hasattr(client, "set_sandbox_mode")` 检查会安全跳过。

本轮不改变该行为。

## 10. 测试策略

### 10.1 配置口径测试

- `WorkerSettings.spot_exchanges` 默认返回五所
- `SPOT_EXCHANGES` env 变量 CSV 解析覆盖五所
- `.env.worker.example` 渲染后包含五所
- `systemd` env 模板渲染后包含五所

### 10.2 凭证加载测试

- `load_exchange_credential_from_env("binance")` 能正确加载 `BINANCE_API_KEY` / `BINANCE_SECRET`
- `load_exchange_credential_from_env("bybit")` 能正确加载 `BYBIT_API_KEY` / `BYBIT_SECRET`
- `load_exchange_credentials_from_env(["okx", "binance", "bybit", "bitget", "gate"])` 能正确收集已配置的凭证

### 10.3 session 创建测试

- `ExchangeClientFactory.create_session(exchange="binance", ...)` 正常创建 session
- `ExchangeClientFactory.create_session(exchange="bybit", ...)` 正常创建 session
- 五所 session 都能正常设置 sandbox 模式

### 10.4 探针默认值测试

- `sandbox_probe` 默认 `SANDBOX_PROBE_EXCHANGES` 包含五所
- `spot_arbitrage_probe` 中 `post_only` 对五所生效

### 10.5 回归测试

- 已有 scanner / dispatcher / executor / repair 行为不回归
- B1-5A/B/C/D/E 套利链行为不回归
- 旧告警与 dedupe 行为不回归
- session 关闭与生命周期不回归

## 11. 验收标准

满足以下条件即可视为本轮完成：

1. `worker_config.py` 的 `spot_exchanges` 默认值为五所
2. `.env.worker.example` 中 `SPOT_EXCHANGES` 为五所，且包含 `BINANCE_*` / `BYBIT_*` 占位
3. `live-workers-systemd.md` 中 env 模板与生成脚本更新为五所
4. `spot_arbitrage_probe.py` 中 `post_only` 映射覆盖五所
5. `test_worker_config.py` 中交易所列表断言更新为五所
6. `test_systemd_assets.py` 中 `SPOT_EXCHANGES` 断言更新为五所
7. `test_sessions.py` 中补充 `binance` 和 `bybit` session 创建测试
8. 已有主链回归通过

## 12. 后续演进

本轮完成后，后续可以推进但不属于本轮范围：

- 逐所深度适配（风控规则、特有订单类型、特有错误码）
- 逐所 sandbox 端到端实测联调
- 交易所级熔断与路由策略
- 五所流动性差异分析
- 生产环境五所全链联调
