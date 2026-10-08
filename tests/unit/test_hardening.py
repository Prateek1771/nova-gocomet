"""Regression tests for the M0/M1 deep-test findings: generic 401s, bounded task updates, log context."""

from dataclasses import dataclass
from typing import Any

import pytest
import structlog
from fastapi import HTTPException
from fastapi.testclient import TestClient
from temporalio.client import WorkflowUpdateRPCTimeoutOrCancelledError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput

from nova_api import deps
from nova_api.main import app
from nova_api.routers import tasks
from nova_core.auth import AuthError
from nova_core.temporal import LogContext


def test_401_does_not_leak_decoder_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    class Bad:
        def verify(self, _: str) -> None:
            raise AuthError("Invalid crypto padding")

    monkeypatch.setattr(deps, "verifier", lambda: Bad())
    r = TestClient(app).get("/api/v1/me", headers={"Authorization": "Bearer x.y.z"})
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "invalid token"
    assert "padding" not in r.text


@pytest.mark.parametrize(
    ("err", "http"),
    [
        (WorkflowUpdateRPCTimeoutOrCancelledError(), 503),  # what the SDK raises on rpc_timeout
        (RPCError("x", RPCStatusCode.UNAVAILABLE, b""), 503),
        (RPCError("x", RPCStatusCode.NOT_FOUND, b""), 409),
    ],
)
async def test_update_without_worker_is_503_not_a_hang(
    monkeypatch: pytest.MonkeyPatch, err: Exception, http: int
) -> None:
    seen: dict[str, Any] = {}

    class Handle:
        async def execute_update(self, name: str, *, args: list[Any], rpc_timeout: Any) -> None:
            seen["timeout"] = rpc_timeout
            raise err

    class Client:
        def get_workflow_handle(self, _: str) -> Handle:
            return Handle()

    async def client() -> Client:
        return Client()

    monkeypatch.setattr(tasks.t, "client", client)
    with pytest.raises(HTTPException) as e:
        await tasks.send_update("run-x", "claim_task", [])
    assert e.value.status_code == http
    assert seen["timeout"] == tasks.UPDATE_TIMEOUT


async def test_activity_logs_carry_run_context() -> None:
    @dataclass
    class Req:
        tenant_id: str
        run_id: str
        node_id: str

    class Next(ActivityInboundInterceptor):
        def __init__(self) -> None:
            pass

        async def execute_activity(self, input: ExecuteActivityInput) -> Any:
            return structlog.contextvars.get_contextvars()

    icpt = LogContext().intercept_activity(Next())
    got = await icpt.execute_activity(
        ExecuteActivityInput(fn=lambda: None, args=[Req("t1", "r1", "n1")], executor=None, headers={})
    )
    assert got == {"tenant_id": "t1", "run_id": "r1", "node_id": "n1"}
    assert structlog.contextvars.get_contextvars() == {}  # unbound after the activity
