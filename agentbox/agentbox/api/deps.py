from __future__ import annotations

from starlette.requests import HTTPConnection

from agentbox.providers import SandboxProvider
from agentbox.state import AgentBoxStateStore


def sandbox_provider(connection: HTTPConnection) -> SandboxProvider:
    return connection.app.state.sandbox_provider


def state_store(connection: HTTPConnection) -> AgentBoxStateStore:
    return connection.app.state.store
