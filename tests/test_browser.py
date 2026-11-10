"""The usage on the overview in a real browser, wide and on a phone: on a wide
screen the sentence with the computers away, the choice of range kept as the
update bus draws the usage again, the trend and what the tokens are made of;
on a phone today's tiles alone, without them."""

from __future__ import annotations

lazy from typing import Any

import pytest
from node import agent, connected, now_ms
from playwright.async_api import async_playwright, expect
from said import ended, requested, turn, used
from served import admin, at, client, enrol, noon, serving, until, usage

KANA = agent("kana", "有马佳奈")
AQUA = agent("aqua", "啊夸", runtime="codex")
BARRET = agent("barret", "Barret", runtime="codex")


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["wide", "phone"])
async def test_the_usage_reads_as_the_design_shows(size: tuple[int, int]):
    wide = size[0] >= 960
    zone = await noon()
    async with client() as boss, serving() as served, async_playwright() as driver:
        await admin(served, boss)
        await enrol(served, boss, "ci-runner-01")
        _, token = await enrol(served, boss, "kana-vm")
        async with connected(
            served,
            token,
            [KANA, AQUA, BARRET],
            {"kana": now_ms() - 192_000, "barret": "codex: not logged in"},
        ) as node:
            await until(
                lambda: usage(served, boss), lambda page: "<small>/ 3</small>" in page
            )
            # two turns three days ago, two yesterday and three today, the
            # last of them using what costs $4.37
            events: list[dict[str, Any]] = []
            for number, (back, hour) in enumerate(
                ((3, 15), (3, 16), (1, 9), (1, 10), (0, 9), (0, 10), (0, 11))
            ):
                started = turn("kana", number, session=f"s-{number}")
                events += [
                    requested(node, started, at(zone, back, hour)),
                    ended(node, started, at(zone, back, hour, 2)),
                ]
            events.append(
                used(
                    node,
                    turn("kana", 6, session="s-6"),
                    640_000,
                    410_000,
                    190_000,
                    4.37,
                    at(zone, 0, 11, 1),
                )
            )
            await node.report(*events)
            browser = await driver.chromium.launch()
            try:
                context = await browser.new_context(
                    locale="zh-CN",
                    timezone_id=zone.key,
                    viewport={"width": size[0], "height": size[1]},
                )
                await context.add_cookies(
                    [
                        {"name": c.name, "value": c.value or "", "url": served.url}
                        for c in boss.cookies.jar
                    ]
                )
                page = await context.new_page()
                await page.goto(f"{served.url}/overview")
                shown = page.frame_locator("iframe.frame").frame_locator(
                    "iframe[src='/ui/usage/']"
                )
                tiles = shown.locator(".kpi:visible")
                await expect(tiles).to_have_count(4)
                await expect(tiles.nth(2).locator(".val")).to_have_text("3")
                await expect(tiles.nth(3).locator(".val")).to_contain_text("1.24")
                # the failed agent counted among those seen, not those running
                await expect(tiles.first.locator(".val")).to_have_text("2/ 3")
                await expect(shown.locator(".lantern")).to_have_count(3)
                await expect(shown.locator(".lantern.fail")).to_have_count(1)
                await expect(shown.locator(".kpi.hot")).to_have_count(1)
                if wide:
                    await expect(shown.locator(".head p:visible")).to_have_text(
                        "1 位智能体正在工作，1 台电脑离线。"
                    )
                    await expect(tiles.nth(2).locator(".up")).to_have_text("↑ 50%")
                    await expect(tiles.nth(3).locator(".val small")).to_have_text(
                        "M tokens · $4.37", use_inner_text=True
                    )
                    await expect(shown.locator(".spark:visible")).to_have_count(1)
                    await expect(shown.locator(".bar:visible i")).to_have_count(3)
                    await expect(shown.locator(".legend:visible span")).to_have_text(
                        ["输入 52%", "缓存命中 33%", "输出 15%"]
                    )
                    # the last 7 days, still chosen when the usage is drawn again
                    await shown.locator(".seg label", has_text="近 7 天").click()
                    await expect(tiles.nth(2).locator(".val")).to_have_text("7")
                    await expect(tiles.nth(2).locator(".lbl")).to_have_text(
                        "近 7 天处理轮次", use_inner_text=True
                    )
                    later = turn("kana", 7, session="s-7")
                    await node.report(requested(node, later, now_ms()))
                    await expect(tiles.nth(2).locator(".val")).to_have_text("8")
                    await expect(shown.locator("input[value='7']")).to_be_checked()
                else:
                    await expect(shown.locator(".head p:visible")).to_have_text(
                        "1 位智能体正在工作。"
                    )
                    await expect(shown.locator(".seg")).to_be_hidden()
                    for tile, label in ((0, "运行中"), (2, "今日轮次")):
                        await expect(tiles.nth(tile).locator(".lbl")).to_have_text(
                            label, use_inner_text=True
                        )
                    await expect(tiles.nth(3).locator(".val small")).to_have_text(
                        "M", use_inner_text=True
                    )
                    await expect(tiles.nth(3).locator(".foot")).to_have_text("$4.37")
                    await expect(shown.locator(".spark:visible")).to_have_count(0)
                    await expect(shown.locator(".bar:visible")).to_have_count(0)
                    later = turn("kana", 7, session="s-7")
                    await node.report(requested(node, later, now_ms()))
                    await expect(tiles.nth(2).locator(".val")).to_have_text("4")
            finally:
                await browser.close()
