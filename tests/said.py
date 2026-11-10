"""What an agent's node says of its turns, by the harness contract, when a test
says they happened: a turn requested, what its runtime session used so far,
and the turn done."""

from __future__ import annotations

lazy from typing import Any

from bazaar_contrib_audit import AuditContext
from bazaar_contrib_harness import (
    HarnessOutput,
    HarnessTurn,
    TokenUsage,
    TurnCompleted,
    UsageUpdated,
)
from node import Node


def turn(
    agent_id: str, number: int, *, session: str = "s-1", runtime: str = "claudecode"
) -> AuditContext:
    """The context of a turn a message in a group set off, as the agent gives it."""
    return AuditContext(
        agent_id=agent_id,
        channel="telegram",
        connection=f"{runtime}:0",
        harness=runtime,
        target_kind="group",
        thread_id="t-dev",
        message_id=f"m-{number}",
        sender_id="yuchanns",
        session_id=session,
        turn_id=f"turn-{number}",
    )


def requested(node: Node, context: AuditContext, at_ms: int) -> dict[str, Any]:
    return node.event(
        "harness.turn.requested",
        context,
        HarnessTurn(
            id=str(context.turn_id),
            session_id=str(context.session_id),
            input_text="…",
        ).model_dump(mode="json"),
        at_ms,
    )


def output(context: AuditContext, payload: Any) -> HarnessOutput:
    return HarnessOutput(
        session_id=str(context.session_id),
        turn_id=str(context.turn_id),
        payload=payload,
    )


def ended(node: Node, context: AuditContext, at_ms: int) -> dict[str, Any]:
    return node.harness(
        context, output(context, TurnCompleted(event_name="turn/completed")), at_ms
    )


def used(
    node: Node,
    context: AuditContext,
    fresh: int,
    cached: int,
    output_tokens: int,
    cost: float | None,
    at_ms: int,
) -> dict[str, Any]:
    """The runtime session's running totals; a runtime like Codex gives no cost."""
    return node.harness(
        context,
        output(
            context,
            UsageUpdated(
                total=TokenUsage(
                    input_tokens=fresh,
                    cached_input_tokens=cached,
                    output_tokens=output_tokens,
                ),
                cost_usd=cost,
            ),
        ),
        at_ms,
    )
