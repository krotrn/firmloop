"""A real orchestrator over the real runner container: native tests, the live virtual target and
on-target verification all run for real; only the LLM is the scripted fake provider."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from firmloop.agent.loop import RunOrchestrator
from firmloop.agent.phases import ProviderFactory
from firmloop.agent.templates import load_api_header
from firmloop.clients.runner import RunnerClient
from firmloop.db import repo
from firmloop.db.models import Run
from firmloop.hardware.registry import Registry
from firmloop.llm.fake import FakeProvider
from firmloop.targets.virtual import VirtualTarget
from firmloop.verify import VerifyReport

from .orchestration import RecordingBus, env

VerifyFn = Callable[..., Awaitable[VerifyReport]]


@dataclass
class LiveHarness:
    engine: AsyncEngine
    registry: Registry
    orch: RunOrchestrator
    bus: RecordingBus
    provider: FakeProvider
    runner: RunnerClient
    target: VirtualTarget
    session_id: str

    async def settle(self, run: Run | str) -> Run:
        run_id = run if isinstance(run, str) else run.id
        await asyncio.wait_for(self.orch.wait(run_id), 60)
        fresh = await repo.get_run(self.engine, run_id)
        assert fresh is not None
        return fresh

    async def request(self, text: str = "Fan on above 30", **kwargs: Any) -> Run:
        run = await self.orch.start_request(self.session_id, text, **kwargs)
        return await self.settle(run)

    def reply(self, *responses: Any) -> None:
        """Queue more scripted LLM replies."""
        self.provider._responses.extend(responses)  # pyright: ignore[reportPrivateUsage]

    def user_message(self, call: int) -> str:
        return self.provider.calls[call].messages[-1].content


def new_orchestrator(
    engine: AsyncEngine,
    registry: Registry,
    bus: RecordingBus,
    provider: FakeProvider,
    runner: RunnerClient,
    target: VirtualTarget,
    header: str,
    verify: VerifyFn | None = None,
    providers: ProviderFactory | None = None,
    **settings: Any,
) -> RunOrchestrator:
    """An orchestrator wired like the app's, with the fake provider; also used to simulate a
    backend restart over the same database."""
    extra: dict[str, Any] = {} if verify is None else {"verify": verify}
    return RunOrchestrator(
        engine,
        registry,
        bus,
        env=env(**settings),
        runner=runner,
        virtual_target=target,
        header=header,
        providers=providers or (lambda _settings: (provider, None)),
        **extra,
    )


@asynccontextmanager
async def live_harness(
    engine: AsyncEngine,
    registry: Registry,
    *,
    llm: Sequence[Any] = (),
    provider: FakeProvider | None = None,
    verify: VerifyFn | None = None,
    providers: ProviderFactory | None = None,
    **settings: Any,
) -> AsyncGenerator[LiveHarness]:
    base = env(**settings)
    header = await asyncio.to_thread(load_api_header, base.firmware_dir)
    runner = RunnerClient(base_url=base.runner_url)
    target = VirtualTarget(runner, registry)
    bus = RecordingBus()
    provider = provider or FakeProvider(list(llm))
    orch = new_orchestrator(
        engine,
        registry,
        bus,
        provider,
        runner,
        target,
        header,
        verify=verify,
        providers=providers,
        **settings,
    )
    try:
        await orch.startup()
        session_id = (await repo.create_session(engine, "Fan demo")).id
        yield LiveHarness(engine, registry, orch, bus, provider, runner, target, session_id)
    finally:
        await orch.shutdown()
        await target.aclose()
        await runner.aclose()
