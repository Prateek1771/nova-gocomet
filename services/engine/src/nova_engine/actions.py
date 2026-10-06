"""Registered side effects (`action` nodes). Business integrations (tms.*, erp.*) register here too."""

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from nova_core.registry import Registry

Action = Callable[[dict[str, Any]], Awaitable[Any]]
ACTIONS: Registry[Action] = Registry("action")
log = structlog.get_logger()


@ACTIONS.register("noop")
async def noop(params: dict[str, Any]) -> dict[str, Any]:
    return {}


@ACTIONS.register("notify.log")
async def notify_log(params: dict[str, Any]) -> dict[str, Any]:
    # ponytail: logs instead of email/Slack; a real notifier registers under notify.email etc.
    log.info("notify", **params)
    return {"delivered": True}
