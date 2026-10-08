import logging
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace


def add_trace_id(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Stamp the active OTel trace id so a log line links to its trace."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event.setdefault("trace_id", format(ctx.trace_id, "032x"))
    return event


def configure_logging(level: str = "INFO") -> None:
    """JSON logs; bind tenant_id / run_id with structlog.contextvars, trace_id comes from OTel."""
    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_trace_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
    )
