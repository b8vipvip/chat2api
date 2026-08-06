from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DesktopAgent:
    agent_id: str
    name: str
    platform: str
    version: str
    metadata: dict[str, Any]
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    last_seen_monotonic: float = field(default_factory=time.monotonic)


class DesktopAgentHub:
    def __init__(self, stale_after_seconds: int = 90) -> None:
        self.agents: dict[str, DesktopAgent] = {}
        self.stale_after_seconds = stale_after_seconds
        self.lock = asyncio.Lock()

    async def register(
        self,
        name: str,
        platform: str,
        version: str,
        metadata: dict[str, Any],
    ) -> DesktopAgent:
        async with self.lock:
            agent_id = "agent_" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")
            agent = DesktopAgent(
                agent_id=agent_id,
                name=name,
                platform=platform,
                version=version,
                metadata=dict(metadata),
            )
            self.agents[agent_id] = agent
            return agent

    def _active_agents(self) -> list[DesktopAgent]:
        now = time.monotonic()
        return [
            agent
            for agent in self.agents.values()
            if now - agent.last_seen_monotonic <= self.stale_after_seconds
        ]

    def online_count(self) -> int:
        return len(self._active_agents())

    async def wait(self, agent_id: str, timeout_seconds: int = 25) -> dict[str, Any]:
        agent = self.agents.get(agent_id)
        if not agent:
            raise KeyError("Unknown desktop agent")
        agent.last_seen_monotonic = time.monotonic()
        try:
            command = await asyncio.wait_for(agent.queue.get(), timeout=max(1, min(timeout_seconds, 30)))
            agent.last_seen_monotonic = time.monotonic()
            return command
        except asyncio.TimeoutError:
            return {"type": "noop", "ts": time.time()}

    async def wake(self, reason: str, requested_client_id: str | None = None) -> int:
        command = {
            "type": "launch_browser",
            "reason": reason,
            "requested_client_id": requested_client_id,
            "ts": time.time(),
        }
        active = self._active_agents()
        for agent in active:
            while not agent.queue.empty():
                try:
                    agent.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            await agent.queue.put(command)
        return len(active)
