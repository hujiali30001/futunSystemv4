from dataclasses import dataclass

from config import get_settings


@dataclass(slots=True)
class RuntimeApp:
    service_name: str
    region: str


def build_runtime(service_name: str, region: str | None = None) -> RuntimeApp:
    settings = get_settings()
    return RuntimeApp(
        service_name=service_name,
        region=region or settings.default_region,
    )
