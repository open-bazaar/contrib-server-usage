"""The server's entries as installed, server-usage among them, a viewer's
clock on which today's hours have begun, and what a test reads off the
usage the server draws."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
lazy import asyncio
lazy import json
lazy import logging
lazy import re
lazy from collections.abc import AsyncIterator, Awaitable, Callable
lazy from datetime import UTC, datetime, time, timedelta
lazy from html import unescape
lazy from typing import Any
lazy from zoneinfo import ZoneInfo

import httpx
from bazaar_contrib_http import HTTP
from bazaar_core import Context, Contrib, ContribEntry, ContribHub, ContribSpec, load

ENTRIES = (
    ("timer-wheel", {}),
    ("http", {"listen": "127.0.0.1:0"}),
    ("server-database", {}),
    ("server-migrations", {}),
    ("server", {}),
    ("server-theme-night", {}),
    ("server-theme-day", {}),
    ("server-access", {}),
    ("server-overview", {}),
    ("server-relay-memory", {}),
    ("server-computer", {}),
    ("server-agent", {}),
    ("server-activity", {}),
    ("server-conversation", {}),
    ("server-usage", {}),
)


@dataclass
class Served:
    url: str
    password: str


@asynccontextmanager
async def serving() -> AsyncIterator[Served]:
    found: dict[str, Any] = {}
    printed: list[str] = []

    class Printed(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            printed.append(record.getMessage())

    @asynccontextmanager
    async def look(ctx: Context) -> AsyncIterator[None]:
        found["url"] = ctx.accept(HTTP).url
        yield

    composition = await asyncio.to_thread(
        load,
        [ContribSpec(contrib=name, config=config) for name, config in ENTRIES],
    )
    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="look", definition=Contrib(accepts=[HTTP], lifespan=look)
                ),
            )
        }
    )
    logger = logging.getLogger("bazaar_contrib_server_access")
    handler = Printed()
    logger.addHandler(handler)
    try:
        async with ContribHub() as hub, hub.enable(composition):
            password = next(
                (
                    created.group(1)
                    for message in printed
                    if (created := re.search(r"with password (\S+);", message))
                ),
                "",
            )
            yield Served(found["url"], password)
    finally:
        logger.removeHandler(handler)


@asynccontextmanager
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # building the client loads certificates from disk, which is not the loop's work
    async with await asyncio.to_thread(httpx.AsyncClient, timeout=90) as session:
        yield session


async def admin(served: Served, session: httpx.AsyncClient) -> None:
    await session.post(
        f"{served.url}/login/", data={"name": "admin", "password": served.password}
    )


async def enrol(
    served: Served, session: httpx.AsyncClient, name: str
) -> tuple[str, str]:
    """A computer the account connects; its ref and its token, as the page
    showing the commands gives them."""
    page = await session.post(f"{served.url}/ui/computers/", data={"name": name})
    assert page.status_code == 200, (page.status_code, page.text[:3000])
    token = re.search(r"--option token=([^\"&\s]+)\"", page.text)
    ref = re.search(r'hx-get="/ui/computers/(\d+)"', page.text)
    assert token is not None and ref is not None
    return ref.group(1), token.group(1)


async def noon() -> ZoneInfo:
    """A zone without daylight time where it is past noon and not yet one: the
    hours and days a test puts turns in have begun wherever it runs."""
    # a zone is read from disk the first time it is named
    return await asyncio.to_thread(ZoneInfo, f"Etc/GMT{datetime.now(UTC).hour - 12:+d}")


def at(zone: ZoneInfo, back: int, hour: int, minute: int = 0) -> int:
    """The moment of the day as many days back, on the zone's clock."""
    day = datetime.now(zone).date() - timedelta(days=back)
    return int(datetime.combine(day, time(hour, minute), zone).timestamp() * 1000)


async def usage(
    served: Served, session: httpx.AsyncClient, zone: str | None = None
) -> str:
    """The usage as the account sees it, on the zone's clock when given."""
    answer = await session.get(
        f"{served.url}/ui/usage/",
        headers={} if zone is None else {"X-Timezone": zone},
    )
    assert answer.status_code == 200, (answer.status_code, answer.text[:2000])
    return answer.text


def said(page: str) -> dict[str, str]:
    """The sentence above the tiles as a wide screen and a phone read it,
    when it says anything."""
    return {
        layout: unescape(re.sub(r"<[^>]+>", "", text))
        for layout, text in re.findall(
            r'<p class="(wide|narrow)-only">(.*?)</p>', page, re.DOTALL
        )
    }


def seen(page: str) -> object:
    """What the page follows, as it was when drawn."""
    found = re.search(r"data-topic=\"usage.overview\"[^>]*data-seen='([^']*)'", page)
    assert found, page[:3000]
    return json.loads(found.group(1))


async def told(served: Served, session: httpx.AsyncClient, state: object) -> bool:
    """Whether the update bus says the usage changed since it was drawn."""
    answer = await session.post(
        f"{served.url}/api/server/poll",
        json={
            "subscriptions": [{"id": "usage", "topic": "usage.overview", "seen": state}]
        },
    )
    return [change["id"] for change in answer.json()["changes"]] == ["usage"]


async def until[T](read: Callable[[], Awaitable[T]], done: Callable[[T], bool]) -> T:
    """What a read gives once the server has caught up."""
    value = None
    try:
        async with asyncio.timeout(90):
            while True:
                value = await read()
                if done(value):
                    return value
                await asyncio.sleep(0.5)
    except TimeoutError:
        raise AssertionError(value) from None
