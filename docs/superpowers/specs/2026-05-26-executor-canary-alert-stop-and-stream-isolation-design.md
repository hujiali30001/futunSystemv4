# Executor Canary 告警止血与 Stream 隔离设计

## 1. 文档目标

本文档定义如何修复主服务器持续出现的 `executor.task.failed` 告警：

- `error = task not found: repair-systemd-canary-1`

本次目标不是扩展业务能力，而是先完成两件高优先级修复：

1. 立即止住飞书上持续重复的 canary 历史告警
2. 防止后续远端 systemd canary 再污染真实生产 stream

## 2. 范围

本次只做以下能力：

- 清理主服务器真实 stream 与任务表中的 `repair-systemd-canary-1` 遗留数据
- 更新远端 canary 脚本，使其只使用 canary 专用 stream
- 确认清理后 `executor` 不再重复读取该 canary 历史消息
- 回填运维文档中的排障与修复记录

本次不做以下能力：

- 不重构 `RedisExecutionTaskConsumer` 为 consumer group
- 不引入通用的 stream offset 持久化机制
- 不修改正常生产任务的 stream 协议
- 不扩大到其他非本次 canary 的历史消息清理

## 3. 背景与现状

当前 `executor` 与 `repair` 的运行时消费模型，都是进程启动后以本地内存中的
`last_id = "0-0"` 开始读取 Redis stream。

这意味着：

- 服务重启后会重新扫描历史 stream
- 如果历史 stream 中残留了旧的 canary 消息，服务会再次处理它们

本次远端 `repair worker systemd` 双服务联调使用了：

- `task_uuid = repair-systemd-canary-1`

并且最初是直接往真实生产 stream 注入：

- `stream:spot_exec_tasks:main`
- `stream:repair_tasks:main`

虽然 canary cleanup 删除了任务真值和临时 `sitecustomize.py`，但没有清除真实
stream 里的历史 canary 消息，因此 `executor` 每次重启后都会再次读到这条旧消息。

由于数据库中的 `arbitrage_tasks` 已被清理，`executor` 对该旧消息会进入：

- `task not found: repair-systemd-canary-1`

随后触发：

- `executor.task.failed`

并继续通过飞书告警外发。

## 4. 问题定义

本次问题链分为两段：

1. **遗留数据止血问题**
   - 主服务器真实 stream 中仍保留 `repair-systemd-canary-1`
   - `executor` 重启后会再次消费这些历史消息
2. **验证流污染问题**
   - 远端 systemd canary 直接使用真实 `main` stream
   - 即使这次清理完，只要后续继续沿用真实 stream，问题仍会重复发生

因此，本次不能只做一次远端手工清理，也不能只改文档，必须同时完成：

- 清理历史 canary 痕迹
- 隔离后续 canary 流量

## 5. 设计目标

本次设计满足以下目标：

1. 飞书停止持续收到 `repair-systemd-canary-1` 对应的 `executor.task.failed`
2. 真实 `stream:spot_exec_tasks:main` 与 `stream:repair_tasks:main` 不再保留该 canary 残留
3. 后续远端 systemd canary 只能使用独立 canary stream，不再污染真实生产 stream
4. 修复范围尽量保持在 `.tmp-ssh` 远端脚本与运维文档，不扩大业务逻辑

## 6. 方案比较

### 6.1 方案 A：只清理主服务器遗留消息

做法：

- 删除 `repair-systemd-canary-1` 的任务真值
- 删除生产 stream 中对应历史消息

优点：

- 止血快

缺点：

- 后续再次跑远端 canary 仍可能复发

### 6.2 方案 B：清理遗留消息 + 隔离 canary stream

做法：

- 清理已有历史 canary
- 把远端 systemd canary 改成独立临时 stream
- cleanup 时连 canary stream 一并清理

优点：

- 同时解决当前飞书噪声与后续复发
- 范围小
- 不动正式业务逻辑

缺点：

- 需要更新 `.tmp-ssh` 验证脚本
- 需要补一段文档说明

### 6.3 方案 C：重构通用 stream 消费架构

做法：

- 引入 consumer groups 或 offset 持久化
- 从根上解决“服务重启重读历史 stream”

优点：

- 通用性最强

缺点：

- 范围过大
- 不适合当前先止血再推送的目标

### 6.4 推荐方案

本次采用方案 B。

原因：

- 当前用户痛点是飞书持续告警，需要先止血
- 当前问题来源于一次特定远端 canary，不值得扩大成 stream 架构重做
- canary stream 隔离是最小、最稳、可立即见效的修补方式

## 7. 核心设计

### 7.1 主服务器遗留 canary 清理

本次清理对象固定为：

- `task_uuid = repair-systemd-canary-1`

清理范围包括：

1. 数据库中的 `arbitrage_tasks`
2. 真实 `stream:spot_exec_tasks:main`
3. 真实 `stream:repair_tasks:main`

Redis 清理策略为：

- 先枚举最近相关 entries
- 筛出字段中 `task_uuid = repair-systemd-canary-1` 的消息
- 获取对应 message id
- 再执行逐条 `XDEL`

本次不扩大到其他真实任务或其他 canary 名称。

### 7.2 远端 canary 专用 stream 隔离

后续远端 systemd canary 不再使用：

- `stream:spot_exec_tasks:main`
- `stream:repair_tasks:main`

而是在 canary 期间临时覆盖 `.env.worker` 中的：

- `EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:repair-canary`
- `REPAIR_STREAM_KEY=stream:repair_tasks:repair-canary`

这样远端 `executor` 和 `repair` 在 canary 期间只处理临时隔离 stream。

cleanup 时必须恢复原始 `.env.worker`，保证：

- 正式服务重新回到真实生产 stream
- canary 临时 stream 不会长期接管正式流量

### 7.3 canary cleanup 补强

旧脚本 cleanup 只恢复：

- `.env.worker`
- `sitecustomize.py`
- 数据库 canary task

本次 cleanup 需要额外保证：

1. 删除 canary 专用 `spot_exec_tasks` stream 中本次 canary 消息
2. 删除 canary 专用 `repair_tasks` stream 中本次 canary 消息
3. 若真实 `main` stream 中仍存在旧 `repair-systemd-canary-1`，也一并清掉

即：

- 这次修复既处理“以后”，也处理“现在已经遗留的历史消息”

### 7.4 告警止血验收

清理与修复完成后，需要验证：

1. 重启 `furun-spot-executor.service`
2. 查看最近 `journalctl`
3. 确认不再新增：
   - `task not found: repair-systemd-canary-1`
   - `executor.task.failed` 对应这条 canary 的错误

若日志不再新增同类错误，可认为飞书持续噪声已止住。

### 7.5 文档回填

需要在运维文档中补一段排障记录，至少说明：

- 为什么会重复告警
- 为什么不是普通业务任务，而是历史 canary 消息重读
- 本次如何清理历史消息
- 后续为什么改为 canary 专用 stream

这样后续再看到类似现象时，可以先判断是否又有远端验证流污染了生产 stream。

## 8. 错误处理

本次不新增业务异常分支。

但脚本层要明确以下错误处理：

- 如果 stream 中找不到 `repair-systemd-canary-1`，视为“已清理”而不是失败
- 如果数据库中找不到该任务，视为“已清理”而不是失败
- 如果 Redis 删除部分成功、部分为空，应在结果 JSON 中分别记录，不中断整体 cleanup
- 如果 cleanup 后仍继续出现同样告警，说明仍有地方持续写入同名 canary，需要记录为“写入源未隔离”，而不是误判为清理失败

## 9. 测试策略

本次以远端实测和脚本结果为主，不新增复杂本地自动化测试。

至少覆盖以下检查：

1. 清理前后分别检查：
   - `XREVRANGE stream:spot_exec_tasks:main + - COUNT 20`
   - `XREVRANGE stream:repair_tasks:main + - COUNT 20`
2. 清理后重启 `furun-spot-executor.service`
3. 检查：
   - `journalctl -u furun-spot-executor.service -n 120 --no-pager`
4. 更新后的 canary 再跑一次：
   - 只命中 `repair-canary` 专用 stream
   - 不再写入真实 `main` stream
5. cleanup 后复查：
   - canary stream 不残留本次消息
   - 真实 `main` stream 不残留 `repair-systemd-canary-1`

## 10. 验收标准

满足以下条件即可视为完成：

1. 主服务器不再持续出现 `task not found: repair-systemd-canary-1`
2. 真实 `stream:spot_exec_tasks:main` 中不再残留该 canary 消息
3. 真实 `stream:repair_tasks:main` 中不再残留该 canary 消息
4. 更新后的远端 canary 只使用独立 canary stream
5. canary cleanup 后，独立 canary stream 也不再残留本次消息
6. 运维文档完成本次告警止血与 stream 隔离记录

## 11. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- 为 `executor` / `repair` 引入更正式的 Redis consumer groups
- 为 worker 持久化 stream offset，避免重启后重读历史消息
- 把远端 canary 验证统一改造成独立的临时环境或独立 Redis namespace
