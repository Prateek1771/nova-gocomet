import logging
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace

# Document fields that identify people or companies (BoL parties) or carry free text from a document.
# Logs never need their values; the run id finds them in the DB under RLS.
PII_KEYS = frozenset(
    {
        "shipper",
        "consignee",
        "notify_party",
        "email",
        "name",
        "address",
        "phone",
        "description_of_goods",
        "fields",
        "reason",
    }
)


def _redact(v: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[deep]"
    if isinstance(v, dict):
        return {k: "[redacted]" if k in PII_KEYS else _redact(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_redact(x, depth + 1) for x in v]
    return v


def redact_pii(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Mask PII-tagged keys at any depth before a line is rendered (08 §4 / 10: redaction filter)."""
    for k in list(event):
        if k in PII_KEYS:
            event[k] = "[redacted]"
        elif isinstance(event[k], dict | list | tuple):
            event[k] = _redact(event[k])
    return event


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
            redact_pii,
            add_trace_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
    )
