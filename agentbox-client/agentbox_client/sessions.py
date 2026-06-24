from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

from agentbox_client.client import AgentBoxClient
from agentbox_client.generated.manager.models import (
    ExecCommandResponse,
    ExecutePythonResponse,
    ListProcessesResponse,
    RuntimeSessionHeartbeatResponse,
)


@dataclass
class AgentBoxSession:
    client: AgentBoxClient
    sandbox_id: str
    session_id: str
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = "/workspace"
    heartbeat_interval_seconds: float = 30.0
    delete_on_exit: bool = True
    _heartbeat_task: asyncio.Task[None] | None = field(default=None, init=False)

    async def __aenter__(self) -> "AgentBoxSession":
        await self.client.ensure_sandbox(self.sandbox_id)
        await self.client.create_session(
            self.sandbox_id,
            self.session_id,
            env=self.env,
            cwd=self.cwd,
        )
        if self.heartbeat_interval_seconds > 0:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        if self.delete_on_exit:
            await self.client.delete_session(self.sandbox_id, self.session_id)

    async def execute_python(
        self,
        code: str,
        *,
        timeout_seconds: int = 60,
    ) -> ExecutePythonResponse:
        return await self.client.execute_python(
            self.sandbox_id,
            self.session_id,
            code,
            timeout_seconds=timeout_seconds,
        )

    async def exec_command(
        self,
        *,
        cmd: str,
        max_output_tokens: int | None = None,
        tty: bool = False,
        workdir: str | None = None,
        yield_time_ms: int | None = None,
        timeout: int | None = 300,
    ) -> ExecCommandResponse:
        return await self.client.exec_command(
            self.sandbox_id,
            self.session_id,
            cmd=cmd,
            max_output_tokens=max_output_tokens,
            tty=tty,
            workdir=workdir,
            yield_time_ms=yield_time_ms,
            timeout=timeout,
        )

    async def write_stdin(
        self,
        *,
        process_id: str,
        chars: str | None = None,
        max_output_tokens: int | None = None,
        yield_time_ms: int | None = None,
    ) -> ExecCommandResponse:
        return await self.client.write_stdin(
            self.sandbox_id,
            self.session_id,
            process_id=process_id,
            chars=chars,
            max_output_tokens=max_output_tokens,
            yield_time_ms=yield_time_ms,
        )

    async def terminate_process(self, process_id: str) -> ExecCommandResponse:
        return await self.client.terminate_process(
            self.sandbox_id,
            self.session_id,
            process_id,
        )

    async def list_processes(self) -> ListProcessesResponse:
        return await self.client.list_processes(self.sandbox_id, self.session_id)

    async def heartbeat(self) -> RuntimeSessionHeartbeatResponse:
        return await self.client.heartbeat_session(self.sandbox_id, self.session_id)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            await self.heartbeat()

