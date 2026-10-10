import os
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer

ROOT = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="module")  # a fresh database per test module
def pg() -> Iterator[tuple[str, str]]:
    with PostgresContainer("postgres:17-alpine", password="postgres").with_volume_mapping(
        str(ROOT / "infra/postgres/init"), "/docker-entrypoint-initdb.d", "ro"
    ) as c:
        host, port = c.get_container_host_ip(), c.get_exposed_port(5432)
        owner = f"postgresql+asyncpg://nova_owner:nova_owner@{host}:{port}/nova"
        app = f"postgresql+asyncpg://nova_app:nova_app@{host}:{port}/nova"
        cfg = Config(str(ROOT / "services/api/alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", owner)
        command.upgrade(cfg, "head")
        yield owner, app


@pytest.fixture(scope="session", autouse=True)
def openfga() -> Iterator[str]:
    """Real OpenFGA (in-memory) for every integration test: the API loads infra/openfga/model.json."""
    with DockerContainer("openfga/openfga:v1.10.2").with_command("run").with_exposed_ports(8080) as c:
        url = f"http://{c.get_container_host_ip()}:{c.get_exposed_port(8080)}"
        for _ in range(100):
            try:
                if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        os.environ["OPENFGA_URL"] = url
        yield url
