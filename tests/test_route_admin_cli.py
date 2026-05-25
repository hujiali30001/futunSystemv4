import importlib
import json
import sys

import pytest


class FakeRouteStore:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def backfill_route_index(self, dry_run: bool = False):
        self.calls.append(dry_run)
        return self.result | {"dry_run": dry_run}


def import_route_admin_cli_fresh():
    sys.modules.pop("app.runtime.route_admin_cli", None)
    return importlib.import_module("app.runtime.route_admin_cli")


def test_route_admin_cli_imports_without_route_admin_service(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.runtime.route_admin_service", None)

    module = import_route_admin_cli_fresh()

    assert callable(module.build_parser)


def test_build_parser_accepts_backfill_index_and_dry_run():
    parser = import_route_admin_cli_fresh().build_parser()

    args = parser.parse_args(["backfill-index", "--dry-run"])

    assert args.command == "backfill-index"
    assert args.dry_run is True


@pytest.mark.asyncio
async def test_run_backfill_index_returns_json_summary(capsys):
    store = FakeRouteStore(
        {
            "ok": True,
            "found": 3,
            "newly_indexed": 2,
            "already_indexed": 1,
            "skipped": 1,
        }
    )

    exit_code = await import_route_admin_cli_fresh().run_backfill_index(
        store, dry_run=True
    )

    assert exit_code == 0
    assert store.calls == [True]
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["dry_run"] is True
    assert output["found"] == 3
    assert output["newly_indexed"] == 2
    assert output["already_indexed"] == 1
    assert output["skipped"] == 1
    assert "scanned" not in output
    assert "indexed" not in output
