# Worker Service Task 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Task 4 by wiring multi-symbol scanner settings through `worker_service`, documenting the deployment surface, and validating the live remote workers.

**Architecture:** Keep the existing multi-symbol/depth logic in downstream runtime components and patch only the worker entrypoint contract. Add focused regression tests around `WorkerApp` and `ScannerWorker`, update `.env` and ops docs with the new settings plus whitelist verification steps, then run local pytest and remote systemd checks.

**Tech Stack:** Python 3.10+, asyncio, pytest, pytest-asyncio, pydantic-settings, systemd, redis-cli, OpenSSH

---

## Planned File Structure

**Modify**
- `tests/test_live_workers.py`
- `tests/test_worker_service.py`
- `app/runtime/worker_service.py`
- `deploy/systemd/.env.worker.example`
- `docs/ops/live-workers-systemd.md`

## Task 1: Lock Scanner Contract With Tests

**Files:**
- Modify: `tests/test_live_workers.py`
- Modify: `tests/test_worker_service.py`

- [ ] **Step 1: Add/adjust failing tests**

```python
# tests/test_live_workers.py
@pytest.mark.asyncio
async def test_continuous_scanner_emits_success_events_for_multiple_symbols():
    service = FakeFlowService()
    router = FakeEventRouter()
    scanner = ContinuousSpotScanner(
        flow_service=service,
        poll_interval_seconds=0.0,
        event_router=router,
        region="default",
    )

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbols=["BTC/USDT", "ETH/USDT"],
        max_iterations=1,
    )

    assert [event.symbol for event in router.events if event.event_type == "opportunity.detected"] == [
        "BTC/USDT",
        "ETH/USDT",
    ]


# tests/test_worker_service.py
@pytest.mark.asyncio
async def test_scanner_worker_passes_symbols_depth_and_quote_amount():
    scanner = FakeWorker()
    settings = WorkerSettings(
        worker_role="scanner",
        spot_symbol="BTC/USDT",
        spot_symbols=["BTC/USDT", "ETH/USDT"],
        orderbook_depth_limit=9,
        target_quote_amount=250.0,
    )
    worker = ScannerWorker(scanner=scanner, settings=settings)

    await worker.run(
        exchanges=["okx", "bitget"],
        credentials_by_exchange={"okx": object(), "bitget": object()},
        proxies_by_exchange={"okx": {"http": "http://127.0.0.1:1"}},
    )

    assert scanner.calls[0]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert scanner.calls[0]["orderbook_depth_limit"] == 9
    assert scanner.calls[0]["target_quote_amount"] == 250.0


@pytest.mark.asyncio
async def test_worker_app_passes_symbols_depth_and_quote_amount_to_scanner(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="scanner",
            spot_symbol="BTC/USDT",
            spot_symbols=["BTC/USDT", "ETH/USDT"],
            spot_exchanges=["okx", "bitget"],
            orderbook_depth_limit=7,
            target_quote_amount=321.0,
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    await app.run()

    assert factory.scanner_worker.calls[0]["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert factory.scanner_worker.calls[0]["orderbook_depth_limit"] == 7
    assert factory.scanner_worker.calls[0]["target_quote_amount"] == 321.0


@pytest.mark.asyncio
async def test_worker_app_scanner_failure_bubbles_and_closes_redis(monkeypatch):
    seed_credentials(monkeypatch)
    redis_client = FakeRedis()
    factory = FakeFactory()
    factory.scanner_worker.error = RuntimeError("scanner boom")
    app = WorkerApp(
        settings=WorkerSettings(
            worker_role="scanner",
            spot_exchanges=["okx", "bitget"],
            spot_symbols=["BTC/USDT", "ETH/USDT"],
        ),
        alert_settings=AlertSettings(alerts_enabled=True),
        redis_factory=lambda _: redis_client,
        worker_factory=factory,
    )

    with pytest.raises(RuntimeError, match="scanner boom"):
        await app.run()

    assert redis_client.closed is True
```

- [ ] **Step 2: Run the focused tests and capture the red state**

Run:

```powershell
python -m pytest tests/test_live_workers.py tests/test_worker_service.py -q
```

Expected: FAIL because `ScannerWorker` still sends `symbol=` and does not forward `symbols`, `orderbook_depth_limit`, or `target_quote_amount`.

- [ ] **Step 3: Implement the minimal worker wiring**

```python
# app/runtime/worker_service.py
class ScannerWorker:
    async def run(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        proxies_by_exchange: dict,
    ) -> None:
        await self.scanner.run(
            exchanges=exchanges,
            credentials_by_exchange=credentials_by_exchange,
            symbols=self.settings.active_spot_symbols,
            env_mode=self.settings.env_mode,
            proxies_by_exchange=proxies_by_exchange,
            orderbook_depth_limit=self.settings.orderbook_depth_limit,
            target_quote_amount=self.settings.target_quote_amount,
            max_iterations=None,
        )
```

- [ ] **Step 4: Re-run the focused tests and verify green**

Run:

```powershell
python -m pytest tests/test_live_workers.py tests/test_worker_service.py -q
```

Expected: PASS.

## Task 2: Update Deployment Surface

**Files:**
- Modify: `deploy/systemd/.env.worker.example`
- Modify: `docs/ops/live-workers-systemd.md`

- [ ] **Step 1: Add the new env keys to the example file**

```dotenv
SPOT_SYMBOL=BTC/USDT
SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
SPOT_EXCHANGES=okx,bitget,gate
ORDERBOOK_DEPTH_LIMIT=5
TARGET_QUOTE_AMOUNT=100.0
```

- [ ] **Step 2: Update the ops guide with whitelist validation**

```markdown
- Add `SPOT_SYMBOLS`, `ORDERBOOK_DEPTH_LIMIT`, and `TARGET_QUOTE_AMOUNT` to the sample `.env.worker`.
- Document the remote whitelist rollout value `BTC/USDT,ETH/USDT,SOL/USDT`.
- Add a verification step that checks recent scanner activity or Redis stream entries and confirms at least two whitelist symbols appear.
- Add a whitelist safety step that greps `.env.worker` on the remote host before restart.
```

## Task 3: Validate Locally And Remotely

**Files:**
- Modify: none

- [ ] **Step 1: Run full local pytest**

Run:

```powershell
python -m pytest
```

Expected: full suite PASS.

- [ ] **Step 2: Sync required files to the remote host**

Run:

```powershell
scp app/runtime/worker_config.py app/exchanges/adapters.py app/market/opportunity.py app/runtime/redis_flow.py app/runtime/live_spot_flow.py app/runtime/live_workers.py app/runtime/worker_service.py deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/...
```

Expected: upload succeeds into the matching remote directories.

- [ ] **Step 3: Update remote `.env.worker` and restart services**

Run:

```bash
grep -E '^(SPOT_SYMBOLS|ORDERBOOK_DEPTH_LIMIT|TARGET_QUOTE_AMOUNT)=' .env.worker
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-consumer.service
```

Expected: `.env.worker` shows `BTC/USDT,ETH/USDT,SOL/USDT`; both services restart cleanly.

- [ ] **Step 4: Verify runtime health and whitelist activity**

Run:

```bash
sudo systemctl is-active furun-spot-scanner.service
sudo systemctl is-active furun-spot-consumer.service
redis-cli ZCARD arb:zset:spot
redis-cli XLEN stream:spot_opps
redis-cli XREVRANGE stream:spot_opps + - COUNT 10
sudo journalctl -u furun-spot-scanner.service -n 100 --no-pager
```

Expected: both services are `active`; Redis counters continue growing; latest entries include `effective_buy_price`, `effective_sell_price`, `target_quote_amount`, `buy_depth_levels_used`, `sell_depth_levels_used`; recent scanner activity or Redis entries show at least two of `BTC/USDT`, `ETH/USDT`, `SOL/USDT`.
