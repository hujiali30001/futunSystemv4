# 五大交易所全链接入收口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一五所口径，将 `okx/binance/bybit/bitget/gate` 全部接入 scanner / dispatcher / executor / repair 主链默认配置，补齐测试断言与文档。

**Architecture:** 本轮不改执行逻辑，不改 session factory，不改 Redis/DB 结构。只在配置层、探针特判层、测试层、文档层做最小对齐收口。

**Tech Stack:** Python 3.10, pydantic-settings, ccxt, pytest

---

### Task 1: worker_config.py 默认 spot_exchanges 改为五所

**Files:**
- Modify: `app/runtime/worker_config.py:22-24`

- [ ] **Step 1: 修改默认值**

Search for `default_factory=lambda: ["okx", "bitget", "gate"]` in `worker_config.py` line 22-24 and replace:

```python
    spot_exchanges: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["okx", "binance", "bybit", "bitget", "gate"]
    )
```

- [ ] **Step 2: 快速验证默认值**

Run:
```powershell
python -c "from app.runtime.worker_config import WorkerSettings; s = WorkerSettings(); print(s.spot_exchanges); assert s.spot_exchanges == ['okx', 'binance', 'bybit', 'bitget', 'gate'], f'got {s.spot_exchanges}'"
```
Expected: 输出 `['okx', 'binance', 'bybit', 'bitget', 'gate']`，无 AssertionError

- [ ] **Step 3: Commit**

```bash
git add app/runtime/worker_config.py
git commit -m "feat(worker-config): default spot_exchanges to five exchanges"
```

---

### Task 2: .env.worker.example 和 systemd_assets.py 改为五所

**Files:**
- Modify: `deploy/systemd/.env.worker.example:14`
- Modify: `deploy/systemd/.env.worker.example:49-72` (补 BINANCE/BYBIT 凭证块)
- Modify: `app/runtime/systemd_assets.py:50`

- [ ] **Step 1: 更新 .env.worker.example SPOT_EXCHANGES 行**

SearchReplace `SPOT_EXCHANGES=okx,bitget,gate` → `SPOT_EXCHANGES=okx,binance,bybit,bitget,gate`

- [ ] **Step 2: 在 .env.worker.example 中补齐 BINANCE/BYBIT 凭证块**

在 `OKX_API_KEY=` 块之前插入 BINANCE 块，在 BITGET 块之前插入 BYBIT 块。最终凭证块顺序为：OKX → BINANCE → BYBIT → BITGET → GATE。

完整替换：将 `# Exchange credentials` 以下的旧三所凭证块替换为五所凭证块：

```
# Exchange credentials
OKX_API_KEY=
OKX_SECRET=
OKX_PASSWORD=
OKX_PROXY_TYPE=http
OKX_PROXY_HOST=
OKX_PROXY_PORT=
OKX_PROXY_USERNAME=
OKX_PROXY_PASSWORD=
BINANCE_API_KEY=
BINANCE_SECRET=
BINANCE_PASSWORD=
BINANCE_PROXY_TYPE=http
BINANCE_PROXY_HOST=
BINANCE_PROXY_PORT=
BINANCE_PROXY_USERNAME=
BINANCE_PROXY_PASSWORD=
BYBIT_API_KEY=
BYBIT_SECRET=
BYBIT_PASSWORD=
BYBIT_PROXY_TYPE=http
BYBIT_PROXY_HOST=
BYBIT_PROXY_PORT=
BYBIT_PROXY_USERNAME=
BYBIT_PROXY_PASSWORD=
BITGET_API_KEY=
BITGET_SECRET=
BITGET_PASSWORD=
BITGET_PROXY_TYPE=http
BITGET_PROXY_HOST=
BITGET_PROXY_PORT=
BITGET_PROXY_USERNAME=
BITGET_PROXY_PASSWORD=
GATE_API_KEY=
GATE_SECRET=
GATE_PASSWORD=
GATE_PROXY_TYPE=http
GATE_PROXY_HOST=
GATE_PROXY_PORT=
GATE_PROXY_USERNAME=
GATE_PROXY_PASSWORD=
```

- [ ] **Step 3: 更新 systemd_assets.py 中 render_worker_env_example()**

SearchReplace 函数中 `SPOT_EXCHANGES=okx,bitget,gate` → `SPOT_EXCHANGES=okx,binance,bybit,bitget,gate`

并且将凭证块从三所扩展为五所（与 step 2 相同结构）。

完整替换：`render_worker_env_example()` 中从 `# Exchange credentials` 到末尾的凭证块，改为五所版本。

- [ ] **Step 4: 运行 render 测试确认模板输出正确**

```powershell
python -c "from app.runtime.systemd_assets import render_worker_env_example; t = render_worker_env_example(); assert 'SPOT_EXCHANGES=okx,binance,bybit,bitget,gate' in t; assert 'BINANCE_API_KEY=' in t; assert 'BYBIT_API_KEY=' in t; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add deploy/systemd/.env.worker.example app/runtime/systemd_assets.py
git commit -m "feat(env-example): expand to five exchanges with binance/bybit credentials"
```

---

### Task 3: test_worker_config.py 测试更新为五所

**Files:**
- Modify: `tests/test_worker_config.py` (多个测试函数)

- [ ] **Step 1: 更新 test_worker_settings_parse_csv_exchange_list**

将 `spot_exchanges="okx, bitget ,gate"` 改为 `spot_exchanges="okx, binance ,bybit,bitget,gate"`，断言改为五所：

```python
def test_worker_settings_parse_csv_exchange_list():
    settings = WorkerSettings(
        redis_url="redis://127.0.0.1:6379/0",
        spot_exchanges="okx, binance ,bybit,bitget,gate",
        worker_role="scanner",
    )

    assert settings.spot_exchanges == ["okx", "binance", "bybit", "bitget", "gate"]
    assert settings.spot_symbol == "BTC/USDT"
    assert settings.scanner_poll_interval_seconds == 1.0
    assert settings.consumer_block_ms == 1000
```

- [ ] **Step 2: 更新 test_worker_settings_parse_csv_exchange_list_from_env**

将 `monkeypatch.setenv("SPOT_EXCHANGES", "okx,bitget,gate")` 改为五所：

```python
def test_worker_settings_parse_csv_exchange_list_from_env(monkeypatch):
    monkeypatch.setenv("SPOT_EXCHANGES", "okx,binance,bybit,bitget,gate")

    settings = WorkerSettings()

    assert settings.spot_exchanges == ["okx", "binance", "bybit", "bitget", "gate"]
```

- [ ] **Step 3: 新增 test_load_exchange_credentials_for_binance**

```python
def test_load_exchange_credentials_for_binance(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "binance-key")
    monkeypatch.setenv("BINANCE_SECRET", "binance-secret")

    cred = load_exchange_credential_from_env("binance")

    assert cred is not None
    assert cred.api_key == "binance-key"
    assert cred.secret == "binance-secret"
```

- [ ] **Step 4: 新增 test_load_exchange_credentials_for_bybit**

```python
def test_load_exchange_credentials_for_bybit(monkeypatch):
    monkeypatch.setenv("BYBIT_API_KEY", "bybit-key")
    monkeypatch.setenv("BYBIT_SECRET", "bybit-secret")

    cred = load_exchange_credential_from_env("bybit")

    assert cred is not None
    assert cred.api_key == "bybit-key"
    assert cred.secret == "bybit-secret"
```

- [ ] **Step 5: 新增 test_load_exchange_credentials_for_all_five**

```python
def test_load_exchange_credentials_for_all_five(monkeypatch):
    for ex in ["okx", "binance", "bybit", "bitget", "gate"]:
        monkeypatch.setenv(f"{ex.upper()}_API_KEY", f"{ex}-key")
        monkeypatch.setenv(f"{ex.upper()}_SECRET", f"{ex}-secret")

    credentials = load_exchange_credentials_from_env(
        ["okx", "binance", "bybit", "bitget", "gate"]
    )

    assert set(credentials.keys()) == {"okx", "binance", "bybit", "bitget", "gate"}
    for ex in ["okx", "binance", "bybit", "bitget", "gate"]:
        assert credentials[ex].api_key == f"{ex}-key"
```

- [ ] **Step 6: 运行更新后的测试**

Run:
```powershell
pytest tests/test_worker_config.py -v
```
Expected: 所有测试 PASS（包括新增的 3 个）

- [ ] **Step 7: Commit**

```bash
git add tests/test_worker_config.py
git commit -m "test(worker-config): assert five-exchange defaults and credential loading"
```

---

### Task 4: test_systemd_assets.py 断言更新为五所

**Files:**
- Modify: `tests/test_systemd_assets.py:63`

- [ ] **Step 1: 更新 SPOT_EXCHANGES 断言**

SearchReplace `"SPOT_EXCHANGES=okx,bitget,gate"` → `"SPOT_EXCHANGES=okx,binance,bybit,bitget,gate"`

- [ ] **Step 2: 新增测试验证渲染模板包含 BINANCE/BYBIT 凭证块**

```python
def test_render_worker_env_example_contains_binance_and_bybit_credentials():
    content = render_worker_env_example()

    assert "BINANCE_API_KEY=" in content
    assert "BINANCE_SECRET=" in content
    assert "BINANCE_PASSWORD=" in content
    assert "BYBIT_API_KEY=" in content
    assert "BYBIT_SECRET=" in content
    assert "BYBIT_PASSWORD=" in content
```

- [ ] **Step 3: 运行测试**

Run:
```powershell
pytest tests/test_systemd_assets.py -v
```
Expected: 所有测试 PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_systemd_assets.py
git commit -m "test(systemd-assets): assert five-exchange env template"
```

---

### Task 5: test_sessions.py 补充 bybit session 创建测试

**Files:**
- Modify: `tests/test_sessions.py` (末尾追加)

- [ ] **Step 1: 新增 test_exchange_factory_creates_bybit_session**

```python
def test_exchange_factory_creates_bybit_session():
    class FakeExchangeClient:
        def __init__(self, config):
            self.config = config
            self.sandbox_enabled = False

        def set_sandbox_mode(self, enabled):
            self.sandbox_enabled = enabled

    class FakeCcxtModule:
        bybit = FakeExchangeClient

    factory = ExchangeClientFactory(ccxt_module=FakeCcxtModule())
    session = factory.create_session(
        exchange="bybit",
        env_mode="testnet",
        proxies={},
        credentials=ExchangeCredentials(
            api_key="bybit-key",
            secret="bybit-secret",
        ),
    )

    assert session.exchange == "bybit"
    assert session.env_mode == "testnet"
    assert session.client.config["apiKey"] == "bybit-key"
    assert session.client.sandbox_enabled is True
```

- [ ] **Step 2: 新增 test_exchange_factory_creates_all_five_sessions**

```python
def test_exchange_factory_creates_all_five_sessions():
    class FakeExchangeClient:
        def __init__(self, config):
            self.config = config
            self.sandbox_enabled = False

        def set_sandbox_mode(self, enabled):
            self.sandbox_enabled = enabled

    class FakeCcxtModule:
        okx = FakeExchangeClient
        binance = FakeExchangeClient
        bybit = FakeExchangeClient
        bitget = FakeExchangeClient
        gate = FakeExchangeClient

    factory = ExchangeClientFactory(ccxt_module=FakeCcxtModule())

    for exchange in ["okx", "binance", "bybit", "bitget", "gate"]:
        session = factory.create_session(
            exchange=exchange,
            env_mode="testnet",
            proxies={},
            credentials=ExchangeCredentials(api_key="k", secret="s"),
        )
        assert session.exchange == exchange
        assert session.client.sandbox_enabled is True
```

- [ ] **Step 3: 运行测试**

Run:
```powershell
pytest tests/test_sessions.py -v
```
Expected: 所有测试 PASS（包括新增的 2 个）

- [ ] **Step 4: Commit**

```bash
git add tests/test_sessions.py
git commit -m "test(sessions): add bybit and five-exchange session creation tests"
```

---

### Task 6: spot_arbitrage_probe.py post_only 映射扩展到五所

**Files:**
- Modify: `app/runtime/spot_arbitrage_probe.py:115-116`
- Modify: `app/runtime/spot_arbitrage_probe.py:123-124`

- [ ] **Step 1: 更新 post_only 映射**

`_build_limit_buy_request` 中 `post_only` 条件当前为：

```python
        if exchange in {"okx", "gate", "gateio"}:
            request.post_only = True
```

两处都改为五所全集：

```python
        if exchange in {"okx", "binance", "bybit", "bitget", "gate", "gateio"}:
            request.post_only = True
```

Buy 请求在 line ~115，Sell 请求在 line ~123，两处都要改。

- [ ] **Step 2: 验证 post_only 逻辑**

Run:
```powershell
python -c "from app.runtime.spot_arbitrage_probe import SpotArbitrageProbeService; import types; s = SpotArbitrageProbeService(); r = s._build_limit_buy_request(symbol='BTC/USDT', amount=0.001, price=50000.0, exchange='binance'); assert r.post_only is True; r2 = s._build_limit_buy_request(symbol='BTC/USDT', amount=0.001, price=50000.0, exchange='bybit'); assert r2.post_only is True; r3 = s._build_limit_buy_request(symbol='BTC/USDT', amount=0.001, price=50000.0, exchange='bitget'); assert r3.post_only is True; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/runtime/spot_arbitrage_probe.py
git commit -m "feat(spot-probe): expand post_only mapping to all five exchanges"
```

---

### Task 7: live-workers-systemd.md 更新为五所

**Files:**
- Modify: `docs/ops/live-workers-systemd.md` (多处)

- [ ] **Step 1: 更新 env 模板中 SPOT_EXCHANGES**

SearchReplace 文档中所有 `SPOT_EXCHANGES=okx,bitget,gate` → `SPOT_EXCHANGES=okx,binance,bybit,bitget,gate`

查找并更新以下位置：
- env 样例模板块中的 `SPOT_EXCHANGES=okx,bitget,gate`（第 130 行附近 dotenv 代码块）
- PowerShell 脚本中 `$envContent` 的 `SPOT_EXCHANGES=okx,bitget,gate`（第 196 行附近）

- [ ] **Step 2: 更新凭证与代理说明**

在 `Exchange credentials still come from` 行（第 22 行附近）更新为五所：

```
- Exchange credentials still come from `OKX_*`, `BINANCE_*`, `BYBIT_*`, `BITGET_*`, and `GATE_*`; a missing required key should raise `worker.start_failed` and fan out to Feishu plus QQ email.
```

- [ ] **Step 3: 在 dotenv 模板块和 PowerShell 脚本中补齐 BINANCE_*/BYBIT_***

dotenv 模板块中，在 `OKX_PASSWORD=` 和 `BITGET_API_KEY=` 之间插入：

```
BINANCE_API_KEY=<模拟盘 key>
BINANCE_SECRET=<模拟盘 secret>
BINANCE_PASSWORD=<如有则填写>
BYBIT_API_KEY=<模拟盘 key>
BYBIT_SECRET=<模拟盘 secret>
BYBIT_PASSWORD=<如有则填写>
```

PowerShell 生成脚本中，在 `$okxPassword` 提取之后、`$bitgetKey` 提取之前，新增 `BINANCE` 和 `BYBIT` 凭证提取与 env 输出：

```powershell
$binanceKey = [regex]::Match($exchangeText, "binance[\s\S]*?apikey\s*=\s*""([^""]+)""").Groups[1].Value
$binanceSecret = [regex]::Match($exchangeText, "binance[\s\S]*?secretkey\s*=\s*""([^""]+)""").Groups[1].Value
$bybitKey = [regex]::Match($exchangeText, "bybit[\s\S]*?apikey\s*=\s*""([^""]+)""").Groups[1].Value
$bybitSecret = [regex]::Match($exchangeText, "bybit[\s\S]*?secretkey\s*=\s*""([^""]+)""").Groups[1].Value
```

并在 `$envContent` 的 `OKX_PASSWORD=$okxPassword` 之后、`BITGET_API_KEY=$bitgetKey` 之前插入：

```
BINANCE_API_KEY=$binanceKey
BINANCE_SECRET=$binanceSecret
BINANCE_PASSWORD=
BYBIT_API_KEY=$bybitKey
BYBIT_SECRET=$bybitSecret
BYBIT_PASSWORD=
```

> 注意：`local-secrets/五大交易所模拟盘apikey.txt` 的具体格式可能与本 plan 中 regex 不完全匹配，如果 PowerShell 脚本中现有 okx/bitget/gate 的提取正则已经能正常工作，则 binance/bybit 的提取正则沿用同模式。本轮以文档更新为主，实际脚本的正确性依赖 local-secrets 文件格式，不在本 plan 中做真实提取验证。

- [ ] **Step 4: 确认文档无误**

```powershell
python -c "from pathlib import Path; t = Path('docs/ops/live-workers-systemd.md').read_text(encoding='utf-8'); assert 'okx,binance,bybit,bitget,gate' in t; assert 'binance' in t.lower(); print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add docs/ops/live-workers-systemd.md
git commit -m "docs(ops): expand live-workers-systemd to five exchanges"
```

---

### Task 8: 回归验证

**Files:** 无修改

- [ ] **Step 1: 运行 test_worker_config.py 全量**

```powershell
pytest tests/test_worker_config.py -v
```
Expected: 所有测试 PASS（含本次新增）

- [ ] **Step 2: 运行 test_systemd_assets.py 全量**

```powershell
pytest tests/test_systemd_assets.py -v
```
Expected: 所有测试 PASS（含本次新增）

- [ ] **Step 3: 运行 test_sessions.py 全量**

```powershell
pytest tests/test_sessions.py -v
```
Expected: 所有测试 PASS（含本次新增）

- [ ] **Step 4: 运行 test_sandbox_probe.py**

```powershell
pytest tests/test_sandbox_probe.py -v
```
Expected: 所有测试 PASS（无改动，确认不回归）

- [ ] **Step 5: 运行主链回归**

```powershell
pytest tests/test_live_workers.py -v -k "spot or dispatch or executor or repair or recovery or arbitrage"
```
Expected: 已有主链测试不回归

- [ ] **Step 6: 确认无回归后提交**

```bash
git status
```
确认只有本次 plan 内的预期文件变更，无意外修改。

- [ ] **Step 7: 推送**

```bash
git -c http.sslBackend=openssl -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 -c http.version=HTTP/1.1 push origin feature/b1-arbitrage-opportunity-semantics
```
