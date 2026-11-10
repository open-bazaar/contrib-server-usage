"""The usage on the overview, as the account sees it: a sentence on the agents
at work, the new conversations waiting and the computers away, beside a
choice of today, the last 7 days or the last 30; the agents running and those
at work; and the turns started and the tokens used in each range on the
viewer's clock, the turns against as long before it. Every range is drawn
at once and the choice shows one; the page follows the agents, what waits,
the computers and the quarter hour through the update bus."""

from __future__ import annotations

from dataclasses import dataclass
lazy from collections.abc import Mapping, Sequence
lazy from datetime import datetime, timedelta
lazy from time import time_ns

lazy from bazaar_contrib_server import Account, Pages
lazy from bazaar_contrib_server_access import Access
lazy from bazaar_contrib_server_activity import Activity, Bucket
lazy from bazaar_contrib_server_agent import Agent, Agents
lazy from bazaar_contrib_server_computer import Computers
lazy from bazaar_contrib_server_conversation import Conversations
lazy from starlette.requests import Request
lazy from starlette.responses import Response
lazy from starlette.routing import Route

# the ranges by the days they reach over: today, the last 7 and the last 30
DAYS = (1, 7, 30)
# every zone's hours begin on a quarter hour: the usage drawn again on each
# moves today and the ranges on at the viewer's midnight
QUARTER_MS = 900_000


def state(agent: Agent) -> str:
    """working, failed, idle or offline, as the agents' list has them."""
    if not agent.online:
        return "offline"
    if agent.activity is None:
        return "idle"
    if agent.activity.state in ("failed", "blocked"):
        return "failed"
    return "working" if agent.activity.working is not None else "idle"


def shares(parts: Sequence[int]) -> list[int]:
    """Each part's share of their sum in whole percents adding up to 100: all
    rounded down, then those losing the most rounded up instead."""
    total = sum(parts)
    if not total:
        return [0 for _ in parts]
    cut = [divmod(part * 100, total) for part in parts]
    given = [whole for whole, _ in cut]
    losing = sorted(range(len(cut)), key=lambda index: -cut[index][1])
    for index in losing[: 100 - sum(given)]:
        given[index] += 1
    return given


@dataclass(frozen=True, slots=True)
class Span:
    """A range on the viewer's clock: the turns and tokens of each hour of
    today or each of its days, and the turns of as long before it."""

    days: int
    buckets: Sequence[Bucket]
    before: int


@dataclass(frozen=True, slots=True)
class Glance:
    """How things stand for an account: each agent it may see and how that
    is, the conversations waiting for it, its computers away, its agents'
    newest event, and the quarter hour it is now."""

    agents: Sequence[tuple[Agent, str]]
    pending: int
    offline: int
    event: int
    quarter: int

    @property
    def seen(self) -> dict[str, object]:
        """What the page follows of it: the newest event, each agent's state
        and since when it works, how many wait and are away, and the
        quarter hour."""
        return {
            "event": self.event,
            "agents": [
                [
                    agent.id,
                    now,
                    None
                    if agent.activity is None or agent.activity.working is None
                    else agent.activity.working.since_ms,
                ]
                for agent, now in self.agents
            ],
            "pending": self.pending,
            "offline": self.offline,
            "quarter": self.quarter,
        }


class Views:
    def __init__(
        self,
        pages: Pages,
        access: Access,
        agents: Agents,
        computers: Computers,
        activity: Activity,
        conversations: Conversations,
    ) -> None:
        self._pages = pages
        self._access = access
        self._agents = agents
        self._computers = computers
        self._activity = activity
        self._conversations = conversations
        pages.environment.filters |= {"shares": shares}
        self.routes = [Route("/", self._page)]

    async def _glance(self, account: Account) -> Glance:
        visible = await self._access.targets(account, "computer", "computers.view")
        computers = await self._computers.computers(
            None if visible is None else sorted(visible)
        )
        return Glance(
            agents=[
                (agent, state(agent)) for agent in await self._agents.agents(account)
            ],
            pending=await self._conversations.pending(account),
            offline=sum(1 for computer in computers if not computer.online),
            event=await self._activity.cursor(account),
            quarter=time_ns() // 1_000_000 // QUARTER_MS,
        )

    async def _spans(self, account: Account, request: Request) -> list[Span]:
        """Today by the hour up to the one now, and the last 7 and 30 days by
        the day, each until now; with the turns of the days before each."""
        now = datetime.now(await self._pages.zone(request))
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # where each day starts, from as long before the longest range to today
        midnights = [
            int((midnight - timedelta(days=back)).timestamp() * 1000)
            for back in range(2 * DAYS[-1] - 1, -1, -1)
        ]
        hours = [
            int(midnight.replace(hour=hour).timestamp() * 1000)
            for hour in range(now.hour + 1)
        ]
        spans = []
        for days in DAYS:
            # the range before as one bucket ahead of the range's own
            before, *buckets = await self._activity.stats(
                account,
                [midnights[-2 * days], *(hours if days == 1 else midnights[-days:])],
                int(now.timestamp() * 1000),
            )
            spans.append(Span(days, buckets, before.turns))
        return spans

    async def _page(self, request: Request) -> Response:
        """The usage, the range chosen before kept as the update bus has the
        page drawn again; today at first."""
        account: Account = request.state.account
        days = request.query_params.get("days")
        glance = await self._glance(account)
        return await self._pages.render(
            request,
            "usage/overview.html",
            glance=glance,
            spans=await self._spans(account, request),
            chosen=int(days) if days in ("7", "30") else 1,
            seen=glance.seen,
        )

    async def followed(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """The newest event of the agents the account sees, how each is, how
        many conversations wait, how many of its computers are away, and the
        quarter hour."""
        del scope
        return (await self._glance(account)).seen
