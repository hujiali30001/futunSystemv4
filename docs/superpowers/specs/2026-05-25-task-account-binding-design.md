# buy_account_id / sell_account_id 固化到任务链设计

## 1. 文档目标

本文档定义如何把 `buy_account_id / sell_account_id` 从“executor 执行期再次选择”推进到“dispatcher 选定后固化为任务真值”，让同一条任务从创建到节点投递再到执行，始终绑定同一组数据库账户记录。

本次目标是在不引入腿级明细表、不改变当前任务粒度、也不新增账户管理后台的前提下，把系统的账户选择模型从：

- dispatcher 只做账户覆盖与放行过滤
- executor 消费任务时再按 `user_id + exchange + env_mode` 选择具体账户

推进到：

- dispatcher 在任务创建前选定买卖两边具体账户
- `arbitrage_tasks` 落库时写入 `buy_account_id / sell_account_id`
- 节点任务 payload 同时携带 `buy_account_id / sell_account_id`
- executor 按绑定账户 ID 读取账户并做最小一致性校验

这样系统就从“派发层与执行层两次账户选择”推进到“账户选择在任务创建时一次定稿，并沿任务链透传”。

## 2. 范围

本次只做以下能力：

- 定义 dispatcher 如何从“账户放行判断”升级为“选定具体账户”
- 定义 `arbitrage_tasks` 如何新增并保存 `buy_account_id / sell_account_id`
- 定义节点任务 payload 如何携带 `buy_account_id / sell_account_id`
- 定义 executor 如何优先按绑定账户 ID 取账户并做最小一致性校验
- 定义绑定账户缺失或失效时的失败语义

本次不做以下能力：

- 不新增腿级事实表，例如 `arbitrage_legs`
- 不新增 order 级 `exchange_account_id` 持久化
- 不引入复杂的多账户优选算法
- 不修改账户 CRUD 或管理后台
- 不改变当前任务粒度，仍是“用户 + 策略 + 机会”
- 不实现自动重选账户或自动改派

## 3. 背景与现状

当前系统已经具备以下基础：

- [models.py](file:///d:/old/FuRunSystemV4/models.py) 中 `ArbitrageTask` 已承载任务真值，包含：
  - `task_uuid`
  - `user_id`
  - `strategy_config_id`
  - `opportunity_id`
  - `spot_exchange`
  - `derivative_exchange`
  - `target_notional`
  - `status`
  - `status_reason`
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 dispatcher 已基于数据库账户完成：
  - 交易所覆盖过滤
  - 自动交易过滤
  - `market_type_scope` 过滤
  - `account_region` 过滤
- [executor_account_truth.py](file:///d:/old/FuRunSystemV4/app/runtime/executor_account_truth.py) 中 executor 已能按数据库账户真值选择并装配执行期 credentials/proxy
- [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py) 中节点任务 payload 已携带：
  - `user_id`
  - `source_message_id`
  - `task_uuid`
  - `strategy_config_id`

但当前仍有明显缺口：

1. dispatcher 只判断“是否有可执行账户”，并不写定具体账户记录
2. executor 仍会在消费节点任务时再次按 `user_id + exchange` 选择账户
3. 任务表和节点 payload 目前都看不到最终选中的账户 ID
4. 运维侧无法从任务真值直接回答“这条任务绑定了哪两个账户”

这意味着数据库账户虽然已经进入派发链和执行链，但“最终到底选中了哪条账户”仍不是任务级真值。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. dispatcher 与 executor 分别做账户选择，容易形成“双阶段选择”
2. 在任务已派发但执行前账户列表发生变化时，executor 可能选到与 dispatcher 预期不同的账户
3. 任务表无法直接审计买卖两边最终绑定账户
4. 节点流本身不自描述，必须结合 executor 当时的 DB 状态才能还原账户选择结果

因此，本次需要把 `buy_account_id / sell_account_id` 正式固化为任务链真值。

## 5. 设计目标

本次设计满足以下目标：

1. dispatcher 在创建任务前选定买卖两边具体账户
2. `arbitrage_tasks` 与节点 payload 同时保存 `buy_account_id / sell_account_id`
3. executor 不再对正常任务做二次账户选择，只按绑定账户 ID 读取账户
4. executor 仍做最小一致性兜底校验，避免执行绑定失效任务
5. 当前控制规则、任务状态机和节点任务流整体保持兼容

## 6. 方案比较

### 6.1 方案 A：任务表 + 节点流同时固化账户 ID

做法：

- dispatcher 选定具体账户
- 写入 `arbitrage_tasks.buy_account_id / sell_account_id`
- 同时写入节点 payload
- executor 直接按 ID 取账户

优点：

- 任务真值和节点真值完全一致
- 运维回溯最清楚
- 可以彻底结束 dispatcher / executor 的双阶段账户选择

缺点：

- 需要同时修改 schema、dispatcher 和 payload

### 6.2 方案 B：只写节点 payload

做法：

- dispatcher 选定账户后，只把账户 ID 放入节点 payload
- 任务表保持不变

优点：

- schema 改动较小

缺点：

- 数据库任务真值里仍看不到最终绑定账户
- 审计、排障和回查会断层

### 6.3 方案 C：只写任务表

做法：

- dispatcher 把账户 ID 写入任务表
- payload 仍不带账户 ID
- executor 通过 `task_uuid` 再回查任务表取账户

优点：

- payload 更简洁

缺点：

- executor 每次多一次任务表查询
- 节点流本身不自描述
- 节点消费者和任务真值之间又多一层耦合

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它能一次把“任务真值、节点真值、执行真值”锁死到同一组账户
- 当前系统已经以 `task_uuid` 为任务链主键，继续把 account binding 写入任务和 payload 最自然
- 这也是最适合后续演进到腿级事实表的中间形态

## 7. 核心设计决策

### 7.1 首版 binding 真值边界

本次只覆盖当前 `spot_futures` 任务链。

首版明确如下边界：

- dispatcher 负责选定 `buy_account_id / sell_account_id`
- `arbitrage_tasks` 增加这两个字段
- 节点任务 payload 增加这两个字段
- executor 优先按这两个字段读取账户

说明：

- 本次不要求新增新的腿级表
- 本次不要求把账户 ID 再拆到更细粒度的订单事实中
- 本次先把“任务绑定账户”闭环打通

### 7.2 dispatcher 的账户选择规则

dispatcher 当前已经具备账户放行判断能力，但它只返回“可执行 / 不可执行”。

本次需把它升级为“选定账户”。

首版规则：

- 买边从 `buy_exchange` 对应账户集中选择
- 卖边从 `sell_exchange` 对应账户集中选择
- 候选账户必须满足：
  - `is_enabled = true`
  - `is_auto_trade_enabled = true`
  - `market_type_scope` 与当前机会兼容
  - `account_region` 与当前 dispatcher `region` 兼容
- 多账户场景按 `id` 升序选第一条

说明：

- 这与当前 executor 的首版确定性规则保持一致
- 这样可以把当前 executor 的选择逻辑前移到 dispatcher

### 7.3 账户选择与现有过滤关系

本次不是新增一套独立逻辑，而是把当前过滤逻辑升级为“过滤 + 选定”。

目标流程：

1. 查当前用户启用账户
2. 校验交易所双边覆盖
3. 校验自动交易
4. 校验 `market_type_scope`
5. 校验 `account_region`
6. 在每一边交易所的候选账户中按确定性规则选出第一条
7. 用选中的账户 ID 创建任务并写节点流

说明：

- 若任意一边无法选出账户，仍沿用现有 skip 语义
- 只有能真正选出买卖两边账户时，才创建任务

### 7.4 `arbitrage_tasks` schema 变更

本次建议在 `ArbitrageTask` 中新增：

- `buy_account_id`
- `sell_account_id`

字段语义：

- 都是 `ExchangeAccount.id`
- 在任务创建时即确定
- 创建后不再修改

说明：

- 这两个字段应被视为任务绑定真值的一部分
- 任务一旦创建，它应绑定到一对明确账户，而不是继续依赖执行期选择

### 7.5 节点 payload 变更

本次建议在 `build_node_execution_task_payload()` 中新增：

- `buy_account_id`
- `sell_account_id`

要求：

- 和 `task_uuid` 一样进入节点任务 payload
- 以字符串形式写入 Redis stream

说明：

- 这样节点消息本身就能完整表达“谁来执行、执行哪条任务、绑定哪两个账户”
- 节点流从“任务引用”升级为“任务绑定引用”

### 7.6 executor 的新职责边界

本次之后，executor 的职责从“选择账户 + 装配凭证”调整为：

- 按绑定账户 ID 读取账户
- 校验账户仍属于该 `user_id`
- 校验账户交易所仍匹配 `buy_exchange/sell_exchange`
- 校验账户仍启用，且 `env_mode` 匹配
- 解密凭证并装配代理

说明：

- executor 不再为正常任务做二次账户选择
- executor 仍然保留最小兜底校验，不假设 dispatcher 永远正确

### 7.7 executor 的 binding 校验规则

executor 读取绑定账户时，至少需要校验：

- `buy_account_id` 对应账户存在
- `sell_account_id` 对应账户存在
- 两个账户的 `user_id` 都等于 payload 中的 `user_id`
- 买边账户 `exchange == buy_exchange`
- 卖边账户 `exchange == sell_exchange`
- 两个账户 `env_mode` 都等于当前 executor `env_mode`
- 两个账户仍为 `is_enabled = true`

本次还建议保留最小一致性兜底：

- 若账户已失去 `is_auto_trade_enabled`
- 若 `market_type_scope` 已不兼容
- 若 `account_region` 已不兼容

也应作为执行前失败处理。

说明：

- dispatcher 负责“选定”
- executor 负责“校验绑定仍然有效”

### 7.8 失败语义

本次需要把“绑定账户 ID 无法使用”的情况与现有账户真值错误区分开来。

建议新增或明确使用如下失败类型：

- `executor_account_binding_not_found`
- `executor_account_binding_invalid`

适用场景：

- payload 带的账户 ID 在 DB 中不存在
- 账户不属于该用户
- 账户交易所与 payload 不匹配
- 账户 `env_mode` 不匹配
- 账户已被禁用

而以下仍属于已有执行前账户真值失败：

- `executor_account_decrypt_failed`
- `executor_account_proxy_invalid`
- `executor_account_market_type_invalid`
- `executor_account_region_mismatch`

说明：

- binding 失败和“按条件筛不到账户”不再是同一类问题
- 首版应能从任务失败原因和日志直接区分这两类故障

### 7.9 与当前 executor 账户解析器的关系

当前 `ExecutorAccountTruthResolver` 的主要职责是“按条件选择账户并解密装配”。

本次之后建议职责拆分为：

- dispatcher 侧：账户选择逻辑
- executor 侧：绑定账户加载与校验逻辑

这意味着：

- executor 解析器不再以 `buy_exchange/sell_exchange` 为主输入做选择
- 而应以 `buy_account_id/sell_account_id` 为主输入做加载

说明：

- 如果保留现有按条件选择方法，也应降级为兼容或测试辅助路径
- 正常执行主链应以 binding 为准

### 7.10 与运维和审计的关系

本次变更后，运维与审计可以直接从两个层面看到账户绑定事实：

- `arbitrage_tasks.buy_account_id / sell_account_id`
- 节点流 payload 的 `buy_account_id / sell_account_id`

这意味着可以直接回答：

- 这条任务绑定了哪两个账户
- 节点上收到的是不是同一组账户
- executor 是否按预期消费了绑定账户

说明：

- 这是后续做腿级明细和 order 级 account_id 落库前的重要过渡层

## 8. 测试与验证

本次应至少覆盖以下验证：

1. dispatcher 在当前过滤链通过后，能选出 `buy_account_id / sell_account_id`
2. 创建任务时，这两个账户 ID 写入 `arbitrage_tasks`
3. 写节点流时，payload 同时携带 `buy_account_id / sell_account_id`
4. 多账户场景下，dispatcher 按 `id` 升序稳定选择账户
5. executor 读取 payload 中的账户 ID 时，不再走二次选择路径
6. binding 账户不存在时，任务明确失败为 `executor_account_binding_not_found`
7. binding 账户存在但用户、交易所或 `env_mode` 不匹配时，任务明确失败为 `executor_account_binding_invalid`
8. 当前任务状态机、控制规则和现有 executor 账户真值解密链不回归

远端验证至少覆盖：

1. canary 任务创建后，DB 任务与节点流都能看到 `buy_account_id / sell_account_id`
2. 正常绑定账户时，executor 成功执行
3. 人工破坏其中一个绑定账户后，executor 明确失败
4. 恢复绑定账户后，任务重新成功

## 9. 风险与兼容性

本次方案的主要风险在于：

1. dispatcher 与 executor 之间的账户 binding 一旦写错，会直接把错误固化到任务链
2. `arbitrage_tasks` schema 需要新增字段，带来迁移成本
3. 若当前 executor 逻辑仍残留旧的按条件选账户路径，可能出现新旧逻辑并存

对应缓解方式：

- 用本地测试锁定 dispatcher 的选择规则与 executor 的 binding 校验规则
- 用远端 canary 验证 DB 任务、节点流、执行结果三者一致
- 明确把“正常主链按 ID 绑定加载”作为唯一推荐路径

## 10. 结论

本次设计把 `buy_account_id / sell_account_id` 从执行期临时选择推进为任务链真值：

- dispatcher 负责选定账户
- `arbitrage_tasks` 保存绑定账户
- 节点 payload 透传绑定账户
- executor 按绑定账户执行并做最小一致性校验

这是把“数据库账户真值”从执行主链进一步推进到“任务级绑定真值”的关键一步，也为后续腿级事实表与 order 级账户审计打下基础。
