"""The turns and tokens of today, the last 7 days and the last 30 on the
viewer's clock, over real HTTP: as many as the events reported say, against
as long before, by the hour or the day, and what the tokens cost where a
runtime gave a cost."""

from __future__ import annotations

lazy import re
lazy from datetime import datetime
lazy from typing import Any

import pytest
from node import agent, connected
from said import ended, requested, turn, used
from served import admin, at, client, enrol, noon, serving, until, usage

KANA = agent("kana", "有马佳奈")
AQUA = agent("aqua", "啊夸", runtime="codex")


def ranges(page: str) -> dict[int, str]:
    """Each range's part of the usage, by the days it reaches over."""
    parts = re.split(r'<div class="span d(\d+)', page)[1:]
    return {int(days): part for days, part in zip(parts[::2], parts[1::2])}


def turns(part: str) -> tuple[int, str | None, list[float]]:
    """A range's turns, how they compare with as long before, and how they
    went: each hour's or day's against the busiest."""
    count = re.search(r'class="val num">(\d+)<', part)
    change = re.search(r'class="(?:up|down|even)">([^<]*)<', part)
    line = re.search(r'<path class="line" d="M([^"]*)"', part)
    assert count and line, part[:3000]
    return (
        int(count.group(1)),
        None if change is None else change.group(1),
        [(20 - float(point.split()[1])) / 16 for point in line.group(1).split(" L")],
    )


def tokens(part: str) -> tuple[str, str, dict[str, int]]:
    """A range's tokens as written, what follows them on a wide screen, and
    the share of each kind."""
    found = re.search(
        r'class="val num">([^<]*)<small><span class="wide-only">([^<]*)<', part
    )
    assert found, part[:3000]
    return (
        found.group(1),
        found.group(2),
        {
            kind: int(share)
            for kind, share in re.findall(
                r'<span class="(fresh|cached|output)">[^<]*?(\d+)%<', part
            )
        },
    )


async def test_each_range_counts_its_turns_against_as_long_before():
    zone = await noon()
    # the turns started, by the days back and the time on the viewer's clock,
    # reported oldest first: today's drawn means every one is in
    started = {
        40: [(hour, 0) for hour in range(21)],
        20: [(hour, 0) for hour in range(8, 14)],
        10: [(9, 0), (10, 0), (11, 0)],
        6: [(20, 0)],
        3: [(15, 0), (16, 0)],
        1: [(8, 0), (9, 0), (10, 0), (11, 0), (16, 0)],
        0: [(1, 30), (1, 45), (9, 10), (10, 5)],
    }
    async with client() as boss, serving() as served:
        await admin(served, boss)
        _, token = await enrol(served, boss, "kana-vm")
        async with connected(served, token, [KANA]) as node:
            await until(
                lambda: usage(served, boss, zone.key),
                lambda page: "<small>/ 1</small>" in page,
            )
            events: list[dict[str, Any]] = []
            for number, begun in enumerate(
                at(zone, back, hour, minute)
                for back, times in started.items()
                for hour, minute in times
            ):
                context = turn("kana", number)
                events += [
                    requested(node, context, begun),
                    ended(node, context, begun + 60_000),
                ]
            await node.report(*events)
            hour = datetime.now(zone).hour
            drawn = ranges(
                await until(
                    lambda: usage(served, boss, zone.key),
                    lambda page: turns(ranges(page)[1])[0] == 4,
                )
            )
            hours = {hour, datetime.now(zone).hour}

    today, change, trend = turns(drawn[1])
    assert (today, change) == (4, "↓ 20%")
    # from midnight to the hour now, the busiest hour the top
    assert len(trend) - 1 in hours
    assert trend == pytest.approx(
        [0, 1, 0, 0, 0, 0, 0, 0, 0, 0.5, 0.5] + [0] * (len(trend) - 11), abs=0.01
    )
    week, change, trend = turns(drawn[7])
    assert (week, change) == (12, "↑ 300%")
    assert trend == pytest.approx([n / 5 for n in (1, 0, 0, 2, 0, 5, 4)], abs=0.01)
    month, change, trend = turns(drawn[30])
    # as many as in the 30 days before
    assert (month, change) == (21, "0%")
    days = [0] * 30
    for back, count in ((20, 6), (10, 3), (6, 1), (3, 2), (1, 5), (0, 4)):
        days[29 - back] = count
    assert trend == pytest.approx([n / 6 for n in days], abs=0.01)


async def test_each_range_adds_up_its_tokens_and_what_a_runtime_said_they_cost():
    zone = await noon()
    async with client() as boss, serving() as served:
        await admin(served, boss)
        _, token = await enrol(served, boss, "kana-vm")
        async with connected(served, token, [KANA, AQUA]) as node:
            await until(
                lambda: usage(served, boss, zone.key),
                lambda page: "<small>/ 2</small>" in page,
            )
            # three days ago a runtime giving the cost, today one like Codex giving none
            claude = turn("kana", 1, session="s-claude")
            codex = turn("aqua", 2, session="s-codex", runtime="codex")
            await node.report(
                requested(node, claude, at(zone, 3, 12)),
                used(node, claude, 6000, 3000, 1000, 0.25, at(zone, 3, 12, 1)),
                ended(node, claude, at(zone, 3, 12, 2)),
                requested(node, codex, at(zone, 0, 9)),
                used(node, codex, 2000, 1000, 500, None, at(zone, 0, 9, 1)),
                ended(node, codex, at(zone, 0, 9, 2)),
            )
            drawn = ranges(
                await until(
                    lambda: usage(served, boss, zone.key),
                    lambda page: tokens(ranges(page)[1])[0] == "3.5",
                )
            )

    assert tokens(drawn[1]) == (
        "3.5",
        "k tokens",
        {"fresh": 57, "cached": 29, "output": 14},
    )
    for days in (7, 30):
        assert tokens(drawn[days]) == (
            "13.5",
            "k tokens · $0.25",
            {"fresh": 59, "cached": 30, "output": 11},
        )
    # nothing before to compare with
    for days in (1, 7, 30):
        assert turns(drawn[days])[1] is None
