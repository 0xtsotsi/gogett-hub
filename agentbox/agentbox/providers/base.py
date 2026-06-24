from __future__ import annotations

from typing import Protocol

from agentbox.config import settings
from agentbox.schemas import (
    ExecCommandRequest,
    ExecCommandResponse,
    ExecutePythonResponse,
    ListProcessesResponse,
    RuntimeSessionRequest,
    RuntimeSessionResponse,
    SandboxEnsureRequest,
    SandboxInternalStatus,
    WriteStdinRequest,
)


class SandboxProvider(Protocol):
    async def create(
        self,
        sandbox_id: str,
        request: SandboxEnsureRequest,
    ) -> SandboxInternalStatus: ...

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus: ...

    async def delete(self, sandbox_id: str) -> bool: ...

    async def execute_code(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        timeout_seconds: int,
    ) -> ExecutePythonResponse: ...

    async def create_session(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: RuntimeSessionRequest,
    ) -> RuntimeSessionResponse: ...

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool: ...

    async def exec_session_process_command(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: ExecCommandRequest,
    ) -> ExecCommandResponse: ...

    async def write_session_process_stdin(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: WriteStdinRequest,
    ) -> ExecCommandResponse: ...

    async def terminate_session_process(
        self,
        sandbox_id: str,
        session_id: str,
        process_id: str,
    ) -> ExecCommandResponse: ...

    async def list_session_processes(
        self,
        sandbox_id: str,
        session_id: str,
    ) -> ListProcessesResponse: ...


def build_sandbox_provider() -> SandboxProvider:
    if settings.agentbox_provider == "docker":
        from agentbox.providers.docker import DockerSandboxProvider

        return DockerSandboxProvider()
    if settings.agentbox_provider == "podman":
        from agentbox.providers.podman import PodmanSandboxProvider

        return PodmanSandboxProvider()
    from agentbox.kubernetes import SandboxKubernetesClient

    return SandboxKubernetesClient()
