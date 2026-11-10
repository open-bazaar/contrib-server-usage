"""The usage on the Bazaar Compute server's overview: how the agents an account
may see are - at work, idle, failed or away - what waits for its decision
and which of its computers are away, and the turns and tokens of today, the
last 7 days and the last 30 on its clock, each against as long before."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy from collections.abc import AsyncIterator
lazy from importlib.resources import files
lazy from pathlib import Path

from bazaar_contrib_server import SERVER, Topic
from bazaar_contrib_server_access import ACCESS
from bazaar_contrib_server_activity import ACTIVITY
from bazaar_contrib_server_agent import AGENTS
from bazaar_contrib_server_computer import COMPUTERS
from bazaar_contrib_server_conversation import CONVERSATIONS
from bazaar_contrib_server_overview import OVERVIEW, OverviewItem
from bazaar_core import Context, Contrib

lazy from .pages import Views

__all__ = ["usage"]


@asynccontextmanager
async def _usage(ctx: Context) -> AsyncIterator[None]:
    server = ctx.accept(SERVER)
    resources = Path(str(files(__name__))) / "resources"
    pages = await server.pages("usage", __name__)
    views = Views(
        pages,
        ctx.accept(ACCESS),
        ctx.accept(AGENTS),
        ctx.accept(COMPUTERS),
        ctx.accept(ACTIVITY),
        ctx.accept(CONVERSATIONS),
    )
    async with (
        server.module(
            "usage",
            ui=pages.application(views.routes),
            static=resources / "static",
            locales=resources / "locales",
        ),
        server.topic(Topic(name="usage.overview", fetch=views.followed)),
        ctx.accept(OVERVIEW).register(
            OverviewItem(
                key="usage",
                title="usage.title",
                src="/ui/usage/",
                order=10,
                columns=3,
            )
        ),
    ):
        yield


usage = Contrib(
    accepts=[SERVER, ACCESS, OVERVIEW, COMPUTERS, AGENTS, ACTIVITY, CONVERSATIONS],
    lifespan=_usage,
)
