"""The sentence and the agents above the usage, with a real node: the agents
at work and idle as the node says, a first contact waiting as it holds it,
and a computer that never connected; whatever is none left out, the
computers away left out on a phone, and the update bus telling the page
when any of it changes."""

from __future__ import annotations

lazy import re
lazy from pathlib import Path

from computer import agent, computer, hold, say
from served import admin, client, enrol, said, seen, serving, told, until, usage

KANA = agent("kana", "有马佳奈")
TIFA = agent("tifa", "Tifa")
YU = {"id": "u-yu", "name": "yuchanns", "display_name": "yuchanns"}
LI = {"id": "u-li", "name": "lilei", "display_name": "李雷"}


def lanterns(page: str) -> dict[str, str]:
    """How each agent is, as its lantern shows it."""
    return {
        name: state
        for state, name in re.findall(
            r'<span class="lantern (\w+)" title="([^"]*)"', page
        )
    }


def running(page: str) -> tuple[str, str]:
    """How many agents run, of all the account sees, and who is at work."""
    counted = re.search(r'class="val num">(\d+)<small>/ (\d+)<', page)
    names = re.search(r'class="foot names">([^<]*)<', page)
    assert counted and names, page[:3000]
    return f"{counted.group(1)}/{counted.group(2)}", names.group(1)


async def test_the_sentence_says_what_there_is_and_leaves_out_what_is_none(
    tmp_path: Path,
):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        await enrol(served, boss, "ci-runner-01")
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA, TIFA]
        ) as pc:
            await hold(pc, "kana", True)
            await say(pc, "kana", "yuchanns", YU, "帮我看看 CI 为什么只跑了 Plan")
            await say(pc, "tifa", "lilei", LI, "你好，想请你看看排期", review="pending")
            busy = await until(
                lambda: usage(served, boss),
                lambda page: (
                    said(page)
                    == {
                        "wide": "1 agent is working, 1 new conversation awaits "
                        "approval, 1 computer is offline.",
                        "narrow": "1 agent is working, 1 new conversation awaits "
                        "approval.",
                    }
                ),
            )
            assert lanterns(busy) == {"有马佳奈": "work", "Tifa": "on"}
            assert running(busy) == ("2/2", "有马佳奈")

            # the turn done, nobody works and the sentence leaves it out
            await hold(pc, "kana", False)
            idle = await until(
                lambda: usage(served, boss),
                lambda page: lanterns(page) == {"有马佳奈": "on", "Tifa": "on"},
            )
            assert said(idle) == {
                "wide": "1 new conversation awaits approval, 1 computer is offline.",
                "narrow": "1 new conversation awaits approval.",
            }
            assert running(idle) == ("2/2", "")
            assert await told(served, boss, seen(busy))
