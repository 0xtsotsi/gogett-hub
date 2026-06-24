from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from urllib import error, request

from fastapi import HTTPException, status
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from pydantic import ValidationError

from agentbox.apps import SANDBOX_APPS
from agentbox.config import settings
from agentbox.sandbox_ids import sandbox_pod_name
from agentbox.schemas import (
    ExecCommandResponse,
    ExecCommandRequest,
    ExecutePythonResponse,
    ListProcessesResponse,
    RuntimeSessionRequest,
    RuntimeSessionResponse,
    SandboxEnsureRequest,
    SandboxInternalAppStatus,
    SandboxInternalStatus,
    WriteStdinRequest,
)
from agentbox.to_thread import run_sync

logger = logging.getLogger(__name__)

_MAX_RUNTIME_ERROR_BODY_LENGTH = 2000


def _truncate_runtime_error_body(value: str) -> str:
    if len(value) <= _MAX_RUNTIME_ERROR_BODY_LENGTH:
        return value
    return f"{value[:_MAX_RUNTIME_ERROR_BODY_LENGTH]}... [truncated]"


def _decode_runtime_error_body(raw: bytes) -> object:
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _truncate_runtime_error_body(text)


def _bounded_runtime_body(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        return _truncate_runtime_error_body(value)
    try:
        text = json.dumps(value, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return _truncate_runtime_error_body(str(value))
    if len(text) <= _MAX_RUNTIME_ERROR_BODY_LENGTH:
        return value
    return {
        "truncated": True,
        "preview": _truncate_runtime_error_body(text),
    }


def _runtime_failure_detail(
    operation: str,
    message: str,
    *,
    runtime_status: int | None = None,
    runtime_body: object | None = None,
) -> dict[str, object]:
    detail: dict[str, object] = {
        "message": f"Sandbox runtime {operation} failed",
        "error": _truncate_runtime_error_body(message),
    }
    if runtime_status is not None:
        detail["runtime_status"] = runtime_status
    if runtime_body is not None:
        detail["runtime_body"] = _bounded_runtime_body(runtime_body)
    return detail


def _invalid_runtime_response(
    operation: str,
    payload: object,
    exc: Exception,
) -> HTTPException:
    detail = _runtime_failure_detail(
        operation,
        f"runtime returned an invalid response: {exc}",
        runtime_body=payload,
    )
    logger.warning(
        "Sandbox runtime %s returned invalid response: %s; body=%r",
        operation,
        exc,
        detail.get("runtime_body"),
    )
    return HTTPException(
        status_code=502,
        detail=detail,
    )


def _request_runtime_json(
    req: request.Request,
    *,
    timeout: int | float,
    operation: str,
) -> dict:
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        try:
            runtime_body = _decode_runtime_error_body(exc.read())
        finally:
            exc.close()
        detail = _runtime_failure_detail(
            operation,
            f"runtime returned HTTP {exc.code}",
            runtime_status=exc.code,
            runtime_body=runtime_body,
        )
        logger.warning(
            "Sandbox runtime %s returned HTTP %s for %s; body=%r",
            operation,
            exc.code,
            req.full_url,
            detail.get("runtime_body"),
        )
        raise HTTPException(
            status_code=502,
            detail=detail,
        ) from exc
    except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        detail = _runtime_failure_detail(operation, str(exc))
        logger.warning(
            "Sandbox runtime %s transport failure for %s: %s",
            operation,
            req.full_url,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=detail,
        ) from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        runtime_body = _decode_runtime_error_body(raw)
        detail = _runtime_failure_detail(
            operation,
            "runtime returned malformed JSON",
            runtime_body=runtime_body,
        )
        logger.warning(
            "Sandbox runtime %s returned malformed JSON for %s: %s; body=%r",
            operation,
            req.full_url,
            exc,
            detail.get("runtime_body"),
        )
        raise HTTPException(
            status_code=502,
            detail=detail,
        ) from exc
    if not isinstance(payload, dict):
        detail = _runtime_failure_detail(
            operation,
            "runtime returned a non-object JSON response",
            runtime_body=payload,
        )
        logger.warning(
            "Sandbox runtime %s returned non-object JSON for %s; body=%r",
            operation,
            req.full_url,
            detail.get("runtime_body"),
        )
        raise HTTPException(
            status_code=502,
            detail=detail,
        )
    return payload


class SandboxKubernetesClient:
    def __init__(self) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.core_v1 = client.CoreV1Api()

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        pod_name = sandbox_pod_name(sandbox_id)
        try:
            pod = await run_sync(
                self.core_v1.read_namespaced_pod,
                name=pod_name,
                namespace=settings.agentbox_namespace,
            )
        except ApiException as exc:
            if exc.status == 404:
                raise HTTPException(status_code=404, detail="Sandbox not found") from exc
            raise

        return self._status_from_pod(sandbox_id, pod)

    async def create(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxInternalStatus:
        pod_name = sandbox_pod_name(sandbox_id)

        try:
            existing = await run_sync(
                self.core_v1.read_namespaced_pod,
                name=pod_name,
                namespace=settings.agentbox_namespace,
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
            existing = None

        if existing is not None:
            status_obj = self._status_from_pod(sandbox_id, existing)
            if status_obj.ready:
                return status_obj

            phase = existing.status.phase if existing.status else None
            if phase in {"Pending", "Running"}:
                # Pod is still coming up (or running but not yet passing its
                # readiness probe); wait for it rather than recreating.
                return await self.wait_until_running(sandbox_id)

            # Terminal pod: the runtime pod uses restart_policy=Never, so a
            # Failed/Succeeded/Unknown pod can never become ready again. Recreate
            # it from scratch — `ensure` stays idempotent and self-healing, and
            # the sandbox's persistent record in the state store is left intact.
            logger.warning(
                "Recreating sandbox %s: existing pod %s is in terminal phase %s",
                sandbox_id,
                pod_name,
                phase,
            )
            await self.delete(sandbox_id)
            await self.wait_until_deleted(sandbox_id)

        pod = client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=settings.agentbox_namespace,
                labels={
                    "app.kubernetes.io/name": "agentbox-sandbox",
                    "agentbox.work/sandbox-id": sandbox_id,
                },
            ),
            spec=client.V1PodSpec(
                runtime_class_name=settings.agentbox_runtime_class_name,
                restart_policy="Never",
                node_selector={
                    "pool": settings.agentbox_node_selector_pool,
                },
                tolerations=[
                    client.V1Toleration(
                        key="workload",
                        operator="Equal",
                        value="sandbox",
                        effect="NoSchedule",
                    )
                ],
                containers=[
                    client.V1Container(
                        name="sandbox",
                        image=settings.agentbox_runtime_image,
                        image_pull_policy=settings.agentbox_sandbox_image_pull_policy,
                        ports=[
                            client.V1ContainerPort(
                                container_port=app.port,
                                name=app.name.replace("_", "-")[:15],
                            )
                            for app in SANDBOX_APPS.values()
                        ],
                        readiness_probe=client.V1Probe(
                            http_get=client.V1HTTPGetAction(
                                path="/health",
                                port=settings.agentbox_runtime_port,
                            ),
                            period_seconds=1,
                            timeout_seconds=1,
                            failure_threshold=120,
                        ),
                        env=[
                            client.V1EnvVar(name=name, value=value)
                            for name, value in sorted(request.env.items())
                        ],
                        resources=client.V1ResourceRequirements(
                            requests={
                                "cpu": settings.agentbox_sandbox_cpu_request,
                                "memory": settings.agentbox_sandbox_memory_request,
                                "ephemeral-storage": settings.agentbox_sandbox_ephemeral_request,
                            },
                            limits={
                                "cpu": settings.agentbox_sandbox_cpu_limit,
                                "memory": settings.agentbox_sandbox_memory_limit,
                                "ephemeral-storage": settings.agentbox_sandbox_ephemeral_limit,
                            },
                        ),
                        security_context=client.V1SecurityContext(
                            allow_privilege_escalation=False,
                            capabilities=client.V1Capabilities(drop=["ALL"]),
                            run_as_non_root=True,
                        ),
                    )
                ],
                security_context=client.V1PodSecurityContext(
                    run_as_non_root=True,
                    seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                ),
            ),
        )

        try:
            created = await run_sync(
                self.core_v1.create_namespaced_pod,
                namespace=settings.agentbox_namespace,
                body=pod,
            )
        except ApiException as exc:
            if exc.status == 409:
                status_obj = await self.get_status(sandbox_id)
                if not status_obj.ready:
                    return await self.wait_until_running(sandbox_id)
                return status_obj
            raise
        del created
        return await self.wait_until_running(sandbox_id)

    async def wait_until_running(self, sandbox_id: str) -> SandboxInternalStatus:
        deadline = time.monotonic() + settings.agentbox_sandbox_ready_timeout_seconds
        last_status = None

        while time.monotonic() < deadline:
            status_obj = await self.get_status(sandbox_id)
            last_status = status_obj
            if status_obj.ready:
                return status_obj
            await asyncio.sleep(1)

        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "message": "Sandbox did not become ready before timeout",
                "last_status": last_status.model_dump() if last_status else None,
            },
        )

    async def wait_until_deleted(self, sandbox_id: str) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                await self.get_status(sandbox_id)
            except HTTPException as exc:
                if exc.status_code == 404:
                    return
                raise
            await asyncio.sleep(0.5)
        raise HTTPException(status_code=504, detail="Sandbox deletion did not complete")

    async def delete(self, sandbox_id: str) -> bool:
        pod_name = sandbox_pod_name(sandbox_id)
        try:
            await run_sync(
                self.core_v1.delete_namespaced_pod,
                name=pod_name,
                namespace=settings.agentbox_namespace,
                grace_period_seconds=0,
            )
            return True
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise

    async def execute_code(
        self,
        sandbox_id: str,
        session_id: str,
        code: str,
        timeout_seconds: int,
    ) -> ExecutePythonResponse:
        status_obj = await self.get_status(sandbox_id)
        if not status_obj.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        if not status_obj.pod_ip:
            raise HTTPException(status_code=409, detail="Sandbox has no pod IP")

        def _execute() -> ExecutePythonResponse:
            path = f"/sessions/{session_id}/execute"
            body = json.dumps({"code": code}).encode("utf-8")
            req = request.Request(
                f"http://{status_obj.pod_ip}:{settings.agentbox_runtime_port}{path}",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            payload = _request_runtime_json(
                req,
                timeout=timeout_seconds,
                operation="code execution request",
            )
            exit_code = 0 if payload.get("ok") else 1
            return ExecutePythonResponse(
                sandbox_id=sandbox_id,
                session_id=session_id,
                stdout=payload.get("stdout") or "",
                stderr=payload.get("stderr") or "",
                result=payload.get("result"),
                error_name=payload.get("error_name"),
                exit_code=exit_code,
                status="completed" if exit_code == 0 else "error",
            )

        return await run_sync(_execute)

    async def create_session(
        self, sandbox_id: str, session_id: str, request_obj: RuntimeSessionRequest
    ) -> RuntimeSessionResponse:
        status_obj = await self.get_status(sandbox_id)
        if not status_obj.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        if not status_obj.pod_ip:
            raise HTTPException(status_code=409, detail="Sandbox has no pod IP")

        def _create() -> RuntimeSessionResponse:
            body = json.dumps(
                {"env": request_obj.env, "cwd": request_obj.cwd}
            ).encode("utf-8")
            req = request.Request(
                f"http://{status_obj.pod_ip}:{settings.agentbox_runtime_port}/sessions/{session_id}",
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            payload = _request_runtime_json(
                req,
                timeout=settings.agentbox_sandbox_ready_timeout_seconds,
                operation="session create request",
            )
            try:
                return RuntimeSessionResponse(sandbox_id=sandbox_id, **payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "session create request",
                    payload,
                    exc,
                ) from exc

        return await run_sync(_create)

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        status_obj = await self.get_status(sandbox_id)
        if not status_obj.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        if not status_obj.pod_ip:
            raise HTTPException(status_code=409, detail="Sandbox has no pod IP")

        def _delete() -> bool:
            req = request.Request(
                f"http://{status_obj.pod_ip}:{settings.agentbox_runtime_port}/sessions/{session_id}",
                method="DELETE",
            )
            payload = _request_runtime_json(
                req,
                timeout=30,
                operation="session delete request",
            )
            return bool(payload.get("deleted"))

        return await run_sync(_delete)

    async def exec_session_process_command(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: ExecCommandRequest,
    ) -> ExecCommandResponse:
        status_obj = await self.get_status(sandbox_id)
        if not status_obj.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        if not status_obj.pod_ip:
            raise HTTPException(status_code=409, detail="Sandbox has no pod IP")

        def _execute() -> dict:
            body = request_obj.model_dump(exclude_none=True).copy()
            payload = json.dumps(body).encode("utf-8")
            timeout = request_obj.timeout or 300
            if request_obj.yield_time_ms is not None:
                timeout = max(timeout, int(request_obj.yield_time_ms / 1000) + 30)
            req = request.Request(
                f"http://{status_obj.pod_ip}:{settings.agentbox_runtime_port}/sessions/{session_id}/exec-command",
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            payload = _request_runtime_json(
                req,
                timeout=timeout + 5,
                operation="process command request",
            )
            try:
                return ExecCommandResponse(**payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "process command request",
                    payload,
                    exc,
                ) from exc

        return await run_sync(_execute)

    async def write_session_process_stdin(
        self,
        sandbox_id: str,
        session_id: str,
        request_obj: WriteStdinRequest,
    ) -> ExecCommandResponse:
        status_obj = await self.get_status(sandbox_id)
        if not status_obj.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        if not status_obj.pod_ip:
            raise HTTPException(status_code=409, detail="Sandbox has no pod IP")

        def _execute() -> dict:
            body = json.dumps(request_obj.model_dump(exclude_none=True)).encode("utf-8")
            wait_ms = request_obj.yield_time_ms or 0
            req = request.Request(
                f"http://{status_obj.pod_ip}:{settings.agentbox_runtime_port}/sessions/{session_id}/write-stdin",
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            payload = _request_runtime_json(
                req,
                timeout=int(wait_ms / 1000) + 35,
                operation="process stdin request",
            )
            try:
                return ExecCommandResponse(**payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "process stdin request",
                    payload,
                    exc,
                ) from exc

        return await run_sync(_execute)

    async def terminate_session_process(
        self,
        sandbox_id: str,
        session_id: str,
        process_id: str,
    ) -> ExecCommandResponse:
        status_obj = await self.get_status(sandbox_id)
        if not status_obj.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        if not status_obj.pod_ip:
            raise HTTPException(status_code=409, detail="Sandbox has no pod IP")

        def _delete() -> dict:
            req = request.Request(
                f"http://{status_obj.pod_ip}:{settings.agentbox_runtime_port}/sessions/{session_id}/processes/{process_id}",
                method="DELETE",
            )
            payload = _request_runtime_json(
                req,
                timeout=30,
                operation="process terminate request",
            )
            try:
                return ExecCommandResponse(**payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "process terminate request",
                    payload,
                    exc,
                ) from exc

        return await run_sync(_delete)

    async def list_session_processes(
        self,
        sandbox_id: str,
        session_id: str,
    ) -> ListProcessesResponse:
        status_obj = await self.get_status(sandbox_id)
        if not status_obj.ready:
            raise HTTPException(status_code=409, detail="Sandbox is not running")
        if not status_obj.pod_ip:
            raise HTTPException(status_code=409, detail="Sandbox has no pod IP")

        def _list() -> ListProcessesResponse:
            req = request.Request(
                f"http://{status_obj.pod_ip}:{settings.agentbox_runtime_port}/sessions/{session_id}/processes",
                method="GET",
            )
            payload = _request_runtime_json(
                req,
                timeout=30,
                operation="process list request",
            )
            try:
                return ListProcessesResponse(**payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "process list request",
                    payload,
                    exc,
                ) from exc

        return await run_sync(_list)

    def _status_from_pod(self, sandbox_id: str, pod: client.V1Pod) -> SandboxInternalStatus:
        ready = False
        for status_obj in pod.status.container_statuses or []:
            if status_obj.name == "sandbox" and status_obj.ready:
                ready = True
                break

        return SandboxInternalStatus(
            id=sandbox_id,
            status=self._lifecycle_status(pod.status.phase, ready),
            ready=ready,
            pod_ip=pod.status.pod_ip,
            runtime_url=f"http://{pod.status.pod_ip}:{settings.agentbox_runtime_port}"
            if pod.status.pod_ip
            else None,
            apps=self._app_statuses_from_pod_ip(pod.status.pod_ip),
        )

    def _lifecycle_status(self, phase: str | None, ready: bool) -> str:
        if ready:
            return "RUNNING"
        if phase in {"Pending"}:
            return "CREATING"
        if phase in {"Succeeded"}:
            return "STOPPED"
        return "ERROR"

    def _app_statuses_from_pod_ip(
        self,
        pod_ip: str | None,
    ) -> dict[str, SandboxInternalAppStatus]:
        return {
            app.name: SandboxInternalAppStatus(
                name=app.name,
                public_slug=app.public_slug,
                port=app.port,
                ready=bool(pod_ip),
                private_url=f"http://{pod_ip}:{app.port}" if pod_ip else None,
            )
            for app in SANDBOX_APPS.values()
        }
