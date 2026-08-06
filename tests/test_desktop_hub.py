import asyncio

from app.desktop import DesktopAgentHub


def test_desktop_agent_receives_wake_command() -> None:
    async def scenario() -> None:
        hub = DesktopAgentHub()
        agent = await hub.register("PC", "Windows", "0.2.0", {})
        assert hub.online_count() == 1
        assert await hub.wake("api_request") == 1
        command = await hub.wait(agent.agent_id, timeout_seconds=1)
        assert command["type"] == "launch_browser"
        assert command["reason"] == "api_request"

    asyncio.run(scenario())
