"""CEL (cel-python) for `rule.when` and `${{ }}` templates (LLD §2.3). Deterministic and pure,
so the engine evaluates it inside Temporal workflow code."""

import json
import re
from datetime import datetime
from functools import lru_cache
from typing import Any

import celpy
from celpy import adapter, celtypes
from celpy.adapter import json_to_cel
from celpy.celparser import CELParseError
from celpy.evaluation import CELEvalError

TEMPLATE = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")


class CelError(Exception):
    pass


_env = celpy.Environment()


@lru_cache(maxsize=4096)
def program(expr: str) -> celpy.Runner:
    """Compile once per expression text; definitions are immutable per version, so this is the
    per-version AST cache."""
    try:
        return _env.program(_env.compile(expr))
    except CELParseError as e:
        raise CelError(f"{expr!r}: {e}") from e


def _alternatives(expr: str) -> list[str]:
    # `a ?? b`: first alternative that evaluates to non-null. Not CEL, so split it off here.
    # ponytail: a `??` inside a string literal would split too; nobody writes that in a template.
    return [p.strip() for p in expr.split("??")]


def check(expr: str) -> None:
    for alt in _alternatives(expr):
        program(alt)


def activation(ctx: dict[str, Any], now: datetime) -> dict[str, Any]:
    act = {k: json_to_cel(ctx.get(k) or {}) for k in ("input", "nodes", "tenant")}
    act["now"] = celtypes.TimestampType(now)
    return act


def evaluate(expr: str, act: dict[str, Any]) -> Any:
    """Evaluate to plain JSON values. `??` falls through on null *or* a missing member."""
    last: Exception | None = None
    for alt in _alternatives(expr):
        try:
            value = program(alt).evaluate(act)
        except CELEvalError as e:
            last = e
            continue
        if value is not None:
            return json.loads(json.dumps(value, cls=adapter.CELJSONEncoder))
    if last and len(_alternatives(expr)) == 1:
        raise CelError(f"{expr!r}: {last.args[0]}") from last
    return None


def truthy(expr: str, act: dict[str, Any]) -> bool:
    v = evaluate(expr, act)
    if not isinstance(v, bool):
        raise CelError(f"{expr!r} must be a bool, got {type(v).__name__}")
    return v


def render(value: Any, act: dict[str, Any]) -> Any:
    """Resolve templates in nested values. A string that is exactly one template keeps the value's type."""
    if isinstance(value, dict):
        return {k: render(v, act) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, act) for v in value]
    if not isinstance(value, str):
        return value
    if (m := TEMPLATE.fullmatch(value.strip())) is not None:
        return evaluate(m[1], act)
    return TEMPLATE.sub(lambda m: _text(evaluate(m[1], act)), value)


def _text(v: Any) -> str:
    return "" if v is None else v if isinstance(v, str) else json.dumps(v)


def templates(value: Any) -> list[str]:
    """Every `${{ expr }}` inside a nested value (for compile-time checks)."""
    if isinstance(value, dict):
        return [e for v in value.values() for e in templates(v)]
    if isinstance(value, list):
        return [e for v in value for e in templates(v)]
    return TEMPLATE.findall(value) if isinstance(value, str) else []
