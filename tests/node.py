"""A computer's side of the protocol over real HTTP, with the token its page
gives: it answers what the server asks about its machine and its agents, as
at work, idle or failed as a test has them, and reports its audit events as
starlink does, when a test says they happened."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
lazy import asyncio
lazy from collections.abc import AsyncIterator, Mapping, Sequence
lazy from time import time_ns
lazy from typing import Any
lazy from uuid import uuid7

import httpx
from bazaar_contrib_audit import AuditContext, AuditInfo
from bazaar_contrib_harness import HarnessOutput
from pydantic import JsonValue
from served import Served

GIB = 1024**3
INFO = {
    "version": "0.3.0",
    "system": "Linux",
    "name": "Fedora Linux 44",
    "release": "6.19.10-300.fc44.x86_64",
    "machine": "x86_64",
    "started_ms": time_ns() // 1_000_000 - 3_600_000,
    "cpu": {"cores": 4, "percent": 12.5},
    "memory": {"total": 8 * GIB, "used": 2 * GIB},
    "disks": [{"mount": "/", "total": 40 * GIB, "used": 14 * GIB}],
}


def now_ms() -> int:
    return time_ns() // 1_000_000


def agent(
    agent_id: str,
    name: str,
    channel: str = "telegram",
    runtime: str = "claudecode",
) -> dict[str, Any]:
    """An agent as agent-configuration lists it, on one channel and one
    runtime."""
    return {
        "id": agent_id,
        "name": name,
        "mode": "session",
        "session": None,
        "idle_timeout": 600,
        "channel": [
            {
                "kind": channel,
                "connection": f"{channel}:0",
                "values": {"bot": f"@{agent_id}_bot"},
                "secrets": [],
            }
        ],
        "harness": [
            {
                "kind": runtime,
                "connection": f"{runtime}:0",
                "values": {},
                "secrets": [],
            }
        ],
    }


def activity(
    agents: Sequence[dict[str, Any]], states: Mapping[str, int | str]
) -> dict[str, Any]:
    """How agent-activity says the agents are: at work since the moment
    given, failed for the reason given, idle otherwise."""
    said: dict[str, Any] = {}
    for one in agents:
        how = states.get(one["id"])
        said[one["id"]] = {
            "state": "failed" if isinstance(how, str) else "running",
            "reason": how if isinstance(how, str) else None,
            "usage": {"input": 0, "output": 0, "cached": 0, "cost": None},
            "event": None,
            "event_at_ms": None,
            "started_at_ms": now_ms() - 3_600_000,
            "working": {"since_ms": how, "conversation": None}
            if isinstance(how, int)
            else None,
            "channels": [],
            "runtimes": [],
        }
    return said


@dataclass
class Node:
    """A connected computer: its id, how its agents are, and the run its
    events come from."""

    id: str
    session: httpx.AsyncClient
    states: dict[str, int | str]
    run: str = field(default_factory=lambda: str(uuid7()))
    sequence: int = 0

    def event(
        self, name: str, context: AuditContext, payload: JsonValue, at_ms: int
    ) -> dict[str, Any]:
        """The next audit event of the run, as the node publishes it."""
        self.sequence += 1
        return AuditInfo(
            id=str(uuid7()),
            run_id=self.run,
            sequence=self.sequence,
            event_name=name,
            occurred_at_ms=at_ms,
            observed_at_ms=at_ms,
            monotonic_ns=time_ns(),
            context=context,
            payload=payload,
        ).model_dump(mode="json")

    def harness(
        self, context: AuditContext, output: HarnessOutput, at_ms: int
    ) -> dict[str, Any]:
        """A runtime's event as the agent publishes it, its native line left out."""
        return self.event(
            f"harness.{output.payload.type}",
            context,
            output.model_dump(mode="json", exclude={"raw"}),
            at_ms,
        )

    async def report(self, *events: dict[str, Any]) -> None:
        sent = await self.session.post(
            "/report", json={"run_id": self.run, "events": list(events)}
        )
        assert sent.status_code == 200, (sent.status_code, sent.text)


async def _answer(
    session: httpx.AsyncClient,
    agents: Sequence[dict[str, Any]],
    states: Mapping[str, int | str],
) -> None:
    """What the server asks the computer, answered as its node would; what
    the node lacks, as it lacks it."""
    after = 0
    while True:
        calls = (await session.get("/listen", params={"after": after})).json()["calls"]
        for call in calls:
            asked = (call["request"]["service"], call["request"]["method"])
            if asked == ("node", "info"):
                answer = {"kind": "result", "value": INFO}
            elif asked == ("agent-configuration", "list"):
                answer = {
                    "kind": "result",
                    "value": {"revision": "1", "agents": list(agents)},
                }
            elif asked == ("agent-activity", "agents"):
                answer = {"kind": "result", "value": activity(agents, states)}
            else:
                answer = {"kind": "error", "type": "LookupError", "message": "absent"}
            await session.put(f"/announce/{call['number']}", json=answer)
            after = call["number"]


@asynccontextmanager
async def connected(
    served: Served,
    token: str,
    agents: Sequence[dict[str, Any]],
    states: dict[str, int | str] | None = None,
) -> AsyncIterator[Node]:
    """The computer of the token connected, its node answering until it
    stops; its agents as the states say, which a test may change."""
    async with await asyncio.to_thread(
        lambda: httpx.AsyncClient(
            base_url=f"{served.url}/starlink/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=90,
        )
    ) as session:
        node = Node(token.partition(":")[0], session, states or {})
        listening = asyncio.create_task(_answer(session, agents, node.states))
        try:
            yield node
        finally:
            listening.cancel()
            try:
                await listening
            except asyncio.CancelledError:
                pass
