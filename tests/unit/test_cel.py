from datetime import UTC, datetime

import pytest

from nova_dsl import cel

NOW = datetime(2026, 10, 5, tzinfo=UTC)
ACT = cel.activation(
    {
        "input": {"amount": 1500, "note": None},
        "nodes": {"extract": {"output": {"fields": {"bol": "B1"}, "min_confidence": 0.8}}},
        "tenant": {"approval_limits": {"ops_lead": 10000}, "auto_approve_below": 1000},
    },
    NOW,
)


def test_rule_conditions() -> None:
    assert cel.truthy("nodes.extract.output.min_confidence < 0.85", ACT)
    assert not cel.truthy("double(input.amount) < double(tenant.auto_approve_below)", ACT)
    assert cel.truthy("input.amount <= tenant.approval_limits.ops_lead", ACT)


def test_non_bool_rule_is_an_error() -> None:
    with pytest.raises(cel.CelError):
        cel.truthy("input.amount", ACT)


def test_templates_keep_types_and_interpolate() -> None:
    assert cel.render("${{ input.amount }}", ACT) == 1500
    assert cel.render({"t": "BoL ${{ nodes.extract.output.fields.bol }}!"}, ACT) == {"t": "BoL B1!"}
    assert cel.render(["${{ tenant.approval_limits }}"], ACT) == [{"ops_lead": 10000}]
    assert cel.render(7, ACT) == 7
    # starts and ends with a template but holds two: text, not one expression spanning both
    assert cel.render("${{ input.amount }} · ${{ nodes.extract.output.fields.bol }}", ACT) == "1500 · B1"


def test_coalesce_falls_through_null_and_missing() -> None:
    assert cel.render("${{ input.note ?? 'none' }}", ACT) == "none"
    assert cel.render("${{ nodes.review.output.by ?? 'auto' }}", ACT) == "auto"
    assert cel.render("x${{ nodes.ghost.output ?? input.ghost }}y", ACT) == "xy"


def test_missing_member_without_coalesce_raises() -> None:
    with pytest.raises(cel.CelError):
        cel.render("${{ nodes.ghost.output }}", ACT)


def test_compile_errors_and_now() -> None:
    with pytest.raises(cel.CelError):
        cel.check("a +")
    assert cel.render("${{ now }}", ACT) == "2026-10-05T00:00:00Z"
    assert cel.templates({"a": ["${{ x }} ${{ y }}"], "b": 1}) == ["x", "y"]
