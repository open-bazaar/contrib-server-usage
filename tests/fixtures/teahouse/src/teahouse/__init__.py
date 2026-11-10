"""A channel and a runtime enough for an agent to work on a real node while
the overview shows it: the channel takes the messages a test leaves in its
inbox, from people met before or never, and confirms what the agent sends;
the runtime says what each turn used, and holds the turn while the test
says so."""
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import time_ns
from typing import Any, ClassVar

from bazaar_contrib_audit import AUDIT, AuditContext
from bazaar_contrib_channel import (
    CHANNEL,
    CHANNEL_BUILDERS,
    CHANNEL_INPUT,
    ChannelBuilderConfig,
    ChannelConnected,
    ChannelDeliveryReceipt,
    ChannelIdentity,
    ChannelInput,
    ChannelSendAttempt,
    ChannelSendPart,
    ChannelSendRequest,
    ChannelTarget,
    ChannelTargetPresentation,
    InboundMessage,
    SenderIdentity,
    channel_builder_key,
)
from bazaar_contrib_harness import (
    HARNESS,
    HARNESS_BUILDERS,
    HARNESS_INPUT,
    HarnessBuilderConfig,
    HarnessInspection,
    HarnessOutput,
    HarnessSession,
    HarnessSessionReconciliation,
    HarnessTurn,
    ProviderCallResult,
    ProviderCallStatus,
    SessionInput,
    TokenUsage,
    TurnCompleted,
    TurnStarted,
    UsageUpdated,
    harness_builder_key,
)
from bazaar_core import Context, Contrib
from pydantic import BaseModel, ConfigDict, Field


def teahouse(agent_id: str) -> Path:
    """Where a test talks to an agent: an inbox of messages, each a JSON file
    named by the message's id, and a hold that keeps turns from ending while
    it is there."""
    return Path(os.environ["BCN_HOME"]) / "teahouse" / agent_id


class ChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bot: str = Field(min_length=1)


class TeahouseChannel:
    def __init__(self, input: ChannelInput, bot: str) -> None:
        self.input = input
        self.bot = bot
        self.stopped = asyncio.Event()

    def get_identity(self) -> ChannelIdentity:
        return ChannelIdentity(id=f"{self.input.agent_id}-{self.bot}", name=self.bot)

    def backfill_provider_thread_id(
        self, provider_thread_id: str, *, channel: str, channel_identity: str
    ) -> str:
        return provider_thread_id

    def dm_address(self, request: Any) -> None:
        return None

    async def receive(self) -> AsyncIterator[InboundMessage]:
        inbox = teahouse(self.input.agent_id) / "inbox"
        while not self.stopped.is_set():
            for path in await asyncio.to_thread(lambda: sorted(inbox.glob("*.json"))):
                message = json.loads(await asyncio.to_thread(path.read_text))
                await asyncio.to_thread(path.unlink)
                chat = f"teahouse:{self.input.agent_id}:{message['kind']}:{message['chat']}"
                yield InboundMessage(
                    id=path.stem,
                    provider_message_id=path.stem,
                    channel_session_id=chat,
                    thread_id=chat,
                    target=ChannelTarget(
                        kind=message["kind"], thread_id=message["chat"]
                    ),
                    target_presentation=ChannelTargetPresentation(
                        display_name=message["title"]
                    )
                    if message["kind"] == "group"
                    else ChannelTargetPresentation(handle=message["chat"]),
                    sender=SenderIdentity(**message["sender"]),
                    body=message["text"],
                    occurred_at_ms=time_ns() // 1_000_000,
                    mentions_agent=False,
                    metadata={"review": message["review"]},
                )
            try:
                async with asyncio.timeout(0.1):
                    await self.stopped.wait()
            except TimeoutError:
                pass

    async def prepare_send(
        self, request: ChannelSendRequest
    ) -> tuple[ChannelSendPart, ...]:
        async def send(attempt: ChannelSendAttempt) -> ChannelDeliveryReceipt:
            return ChannelDeliveryReceipt(
                status="confirmed",
                provider_message_id=attempt.id,
                provider_thread_id=request.target.thread_id,
            )

        return (ChannelSendPart(send=send, message=request),)

    async def request_approval(self, request: Any, *, timeout: float) -> Any:
        raise NotImplementedError("the teahouse channel asks nothing")


@asynccontextmanager
async def channel_lifespan(ctx: Context, config: ChannelConfig):
    channel = TeahouseChannel(ctx.accept(CHANNEL_INPUT), config.bot)
    directory = teahouse(channel.input.agent_id)
    await asyncio.to_thread((directory / "inbox").mkdir, parents=True, exist_ok=True)
    ctx.offer(CHANNEL, channel)
    await ctx.accept(AUDIT).publish(
        ChannelConnected.event_name,
        context=AuditContext(
            agent_id=channel.input.agent_id, channel="teahouse-channel"
        ),
        payload=ChannelConnected(identity=channel.get_identity()).model_dump(
            mode="json"
        ),
    )
    try:
        yield
    finally:
        channel.stopped.set()


channel = Contrib(
    config=ChannelConfig,
    accepts=[CHANNEL_INPUT, AUDIT],
    offers=[CHANNEL],
    lifespan=channel_lifespan,
)


class ChannelBuilderParameters(ChannelBuilderConfig):
    kind: ClassVar[str] = "teahouse-channel"
    entity_model: ClassVar[type[BaseModel]] = ChannelConfig
    entry: ClassVar[Contrib[Any]] = channel


@asynccontextmanager
async def channel_builder_lifespan(ctx: Context, config: ChannelBuilderParameters):
    with ctx.accept(CHANNEL_BUILDERS).register(config.builder()) as builder:
        ctx.offer(channel_builder_key(builder.kind), builder)
        yield


channel_builder = Contrib(
    config=ChannelBuilderParameters,
    accepts=[CHANNEL_BUILDERS],
    offers=[channel_builder_key("teahouse-channel")],
    lifespan=channel_builder_lifespan,
)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeahouseTurn:
    """A turn that says what it used, and ends once no hold is there."""

    def __init__(self, turn: HarnessTurn, directory: Path) -> None:
        self.turn = turn
        self.directory = directory
        self.said: list[Any] = [
            TurnStarted(event_name="teahouse.started"),
            UsageUpdated(
                total=TokenUsage(input_tokens=1200, output_tokens=300), cost_usd=0.01
            ),
            None,
        ]

    def __aiter__(self) -> TeahouseTurn:
        return self

    async def __anext__(self) -> HarnessOutput:
        if not self.said:
            raise StopAsyncIteration
        payload = self.said.pop(0)
        if payload is None:
            # the hold is a file another process removes: looked for again
            while True:
                if not await asyncio.to_thread((self.directory / "hold").exists):
                    break
                await asyncio.sleep(0.2)
            payload = TurnCompleted(event_name="teahouse.completed")
        return HarnessOutput(
            session_id=self.turn.session_id,
            turn_id=self.turn.id,
            payload=payload,
            provider="teahouse",
        )

    async def aclose(self) -> None:
        self.said = []


class TeahouseRuntime:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.events: asyncio.Queue[Any] = asyncio.Queue()

    async def receive_event(self) -> Any:
        return await self.events.get()

    async def start_session(
        self, request: SessionInput, *, timeout: float
    ) -> ProviderCallResult[HarnessSession]:
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=HarnessSession(id=request.id, provider_thread_id=request.id),
        )

    async def reconcile_session(
        self, session: HarnessSession, *args: Any, **kwargs: Any
    ) -> ProviderCallResult[HarnessSessionReconciliation]:
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=HarnessSessionReconciliation(session),
        )

    async def start_turn(
        self, session: HarnessSession, turn: HarnessTurn, *args: Any, **kwargs: Any
    ) -> TeahouseTurn:
        return TeahouseTurn(turn, teahouse(self.agent_id))

    async def steer_turn(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def has_background_job(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def stop_session(
        self, session: HarnessSession, *, timeout: float
    ) -> ProviderCallResult[HarnessSession]:
        return ProviderCallResult(status=ProviderCallStatus.CONFIRMED, value=session)


@asynccontextmanager
async def runtime_lifespan(ctx: Context, config: RuntimeConfig):
    del config
    ctx.offer(HARNESS, TeahouseRuntime(ctx.accept(HARNESS_INPUT).agent_id))
    yield


runtime = Contrib(
    config=RuntimeConfig,
    accepts=[HARNESS_INPUT],
    offers=[HARNESS],
    lifespan=runtime_lifespan,
)


class RuntimeBuilderParameters(HarnessBuilderConfig):
    kind: ClassVar[str] = "teahouse-runtime"
    entity_model: ClassVar[type[BaseModel]] = RuntimeConfig
    entry: ClassVar[Contrib[Any]] = runtime

    @classmethod
    async def inspect(cls, workspace: Path, *, timeout: float) -> HarnessInspection:
        return HarnessInspection(available=True)


@asynccontextmanager
async def runtime_builder_lifespan(ctx: Context, config: RuntimeBuilderParameters):
    with ctx.accept(HARNESS_BUILDERS).register(config.builder()) as builder:
        ctx.offer(harness_builder_key(builder.kind), builder)
        yield


runtime_builder = Contrib(
    config=RuntimeBuilderParameters,
    accepts=[HARNESS_BUILDERS],
    offers=[harness_builder_key("teahouse-runtime")],
    lifespan=runtime_builder_lifespan,
)
