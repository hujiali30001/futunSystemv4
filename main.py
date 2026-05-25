import argparse
from collections.abc import Sequence

from app.runtime.bootstrap import RuntimeApp, build_runtime


def main(argv: Sequence[str] | None = None) -> RuntimeApp:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default="all", choices=["all", "scanner", "trader"])
    parser.add_argument("--region", default=None)
    args = parser.parse_args(argv)
    return build_runtime(service_name=args.service, region=args.region)


if __name__ == "__main__":
    main()
