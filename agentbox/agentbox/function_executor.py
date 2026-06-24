from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import importlib.util
import inspect
import io
import json
import os
import re
import site
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Literal
from uuid import UUID
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


FUNCTION_FILE_NAME = "function.py"
MANIFEST_NAME = ".lemma-function-cache.json"

# A function may declare pip dependencies in its `#python_packages:` header. The
# values are passed to `pip install`, so each must match a PEP 508-ish spec
# (name + optional [extras] + optional version specifier) — never a flag, URL,
# path, or anything with a space/shell metacharacter.
MAX_PYTHON_PACKAGES = 30
MAX_PACKAGE_SPEC_LENGTH = 128
PACKAGE_INSTALL_TIMEOUT_SECONDS = 180
_PACKAGE_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"          # distribution name
    r"(\[[A-Za-z0-9._,-]+\])?"               # optional extras, e.g. [socks,security]
    r"([<>=!~]=?[A-Za-z0-9._*+!,<>=~-]*)?$"  # optional version specifier(s)
)


def is_valid_python_package(spec: str) -> bool:
    return (
        bool(spec)
        and len(spec) <= MAX_PACKAGE_SPEC_LENGTH
        and _PACKAGE_SPEC_RE.match(spec) is not None
    )


def parse_python_packages(code: str) -> list[str]:
    """Extract the deduped `#python_packages:` requirements from a function's code.

    Entries are whitespace-separated; a leading/trailing comma is tolerated (so
    `pandas, numpy` works) while commas *inside* a token are preserved (version
    ranges / multi-extras like `numpy>=1.0,<2.0` or `requests[socks,security]`).
    """
    headers: dict[str, str] = {}
    for line in code.splitlines()[:8]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#") or ":" not in stripped:
            break
        key, value = stripped[1:].split(":", 1)
        headers[key.strip()] = value.strip()
    raw = headers.get("python_packages", "")
    packages: list[str] = []
    for token in raw.split():
        spec = token.strip().strip(",")
        if spec and spec not in packages:
            packages.append(spec)
    return packages


class RuntimeErrorInfo(BaseModel):
    name: str
    message: str
    traceback: list[str] = Field(default_factory=list)
    retryable: bool = False


class FunctionExecuteRequest(BaseModel):
    run_id: UUID
    input_data: dict[str, Any] = Field(default_factory=dict)
    async_job: bool = False
    timeout_seconds: int = Field(default=120, ge=1, le=3600)


class FunctionLogEntry(BaseModel):
    timestamp: str
    stream: Literal["stdout", "stderr", "system"]
    message: str


class FunctionInvokeResponse(BaseModel):
    status: Literal["completed", "failed", "cancelled", "timeout"]
    output_data: dict[str, Any] | None = None
    error: RuntimeErrorInfo | None = None
    logs: list[FunctionLogEntry] = Field(default_factory=list)
    code_hash: str
    duration_ms: int


class FunctionJobAcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    run_id: UUID
    job_id: str


class FunctionJobStatusResponse(BaseModel):
    run_id: UUID
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled", "timeout"]
    output_data: dict[str, Any] | None = None
    error: RuntimeErrorInfo | None = None
    code_hash: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None


class FunctionLogsResponse(BaseModel):
    run_id: UUID
    logs: list[FunctionLogEntry] = Field(default_factory=list)


class FunctionSchemaRequest(BaseModel):
    code_hash: str | None = None


class FunctionSchemaResponse(BaseModel):
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    config_schema: dict[str, Any] | None = None
    code_hash: str


class VerifiedToken(BaseModel):
    user_id: UUID
    email: str | None = None
    pod_id: UUID | None = None
    organization_id: UUID | None = None
    function_id: UUID | None = None
    function_name: str | None = None
    scopes: list[str] = Field(default_factory=list)


class FunctionMetadata(BaseModel):
    id: UUID
    name: str
    pod_id: UUID
    type: str = "API"
    code: str
    code_hash: str | None = None
    config: dict[str, Any] | None = None


class FunctionExecutionContext(BaseModel):
    run_id: UUID
    function_id: UUID
    function_name: str
    pod_id: UUID
    organization_id: UUID | None = None
    user_id: UUID
    user_email: str | None = None
    lemma_token: str
    lemma_base_url: str
    config: Any = None
    workspace_root: str = "/workspace"

    model_config = {"arbitrary_types_allowed": True}


@dataclass
class StoredJob:
    run_id: UUID
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled", "timeout"] = "queued"
    logs: list[FunctionLogEntry] = field(default_factory=list)
    output_data: dict[str, Any] | None = None
    error: RuntimeErrorInfo | None = None
    code_hash: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def function_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def parse_code_headers(code: str) -> tuple[str, str, str, str | None]:
    headers: dict[str, str] = {}
    for line in code.splitlines()[:8]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#") or ":" not in stripped:
            break
        key, value = stripped[1:].split(":", 1)
        headers[key.strip()] = value.strip()
    input_model = headers.get("input_type_name")
    output_model = headers.get("output_type_name")
    function_name = headers.get("function_name")
    config_model = headers.get("config_type_name")
    missing = [
        key
        for key, value in {
            "input_type_name": input_model,
            "output_type_name": output_model,
            "function_name": function_name,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(f"Missing function code header(s): {', '.join(missing)}")
    return input_model or "", output_model or "", function_name or "", config_model


def log_entry(stream: Literal["stdout", "stderr", "system"], message: str) -> FunctionLogEntry:
    return FunctionLogEntry(timestamp=utc_timestamp(), stream=stream, message=message)


@contextlib.contextmanager
def patched_environ(values: dict[str, str]):
    original = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class LemmaFunctionApiClient:
    def __init__(self, *, base_url: str, token: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def verify_token(self) -> VerifiedToken:
        payload = self._get_json("/auth/verify-token")
        return VerifiedToken.model_validate(payload)

    def get_function(self, pod_id: UUID, function_name: str) -> FunctionMetadata:
        quoted_name = urlparse.quote(function_name, safe="")
        payload = self._get_json(f"/pods/{pod_id}/functions/{quoted_name}")
        return FunctionMetadata.model_validate(payload)

    def _get_json(self, path: str) -> dict[str, Any]:
        request = urlrequest.Request(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urlrequest.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise HTTPException(status_code=exc.code, detail=body or exc.reason) from exc
        except urlerror.URLError as exc:
            raise HTTPException(status_code=502, detail=f"Lemma API request failed: {exc}") from exc
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Lemma API returned non-object JSON")
        return payload


class FunctionExecutor:
    def __init__(
        self,
        *,
        workspace_root: str = "/workspace",
        lemma_base_url: str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.lemma_base_url = lemma_base_url or os.environ.get("LEMMA_BASE_URL", "http://localhost:8000")
        self.jobs: dict[UUID, StoredJob] = {}
        self.invocation_lock = asyncio.Lock()
        # pip specs already installed in this container, so repeat runs don't reinstall.
        self._ensured_packages: set[str] = set()

    def api_client(self, token: str) -> LemmaFunctionApiClient:
        return LemmaFunctionApiClient(base_url=self.lemma_base_url, token=token)

    async def execute(
        self,
        *,
        pod_id: UUID,
        function_name: str,
        request: FunctionExecuteRequest,
        token: str,
    ) -> FunctionInvokeResponse | FunctionJobAcceptedResponse:
        if request.async_job:
            job = StoredJob(run_id=request.run_id, job_id=f"function:{request.run_id}")
            self.jobs[request.run_id] = job
            asyncio.create_task(
                self._run_job(
                    job,
                    pod_id=pod_id,
                    function_name=function_name,
                    request=request,
                    token=token,
                )
            )
            return FunctionJobAcceptedResponse(run_id=request.run_id, job_id=job.job_id)
        return await self._execute_sync(
            pod_id=pod_id,
            function_name=function_name,
            request=request,
            token=token,
        )

    async def schemas(
        self,
        *,
        pod_id: UUID,
        function_name: str,
        request: FunctionSchemaRequest,
        token: str,
    ) -> FunctionSchemaResponse:
        verified, metadata = self._authorize_and_fetch(
            pod_id=pod_id,
            function_name=function_name,
            token=token,
        )
        del verified
        if request.code_hash and metadata.code_hash and request.code_hash != metadata.code_hash:
            raise HTTPException(status_code=409, detail="Function code hash mismatch")
        cache_dir, manifest = self.ensure_cached(metadata)
        module = self.load_module(cache_dir, manifest["code_hash"])
        runtime = manifest["runtime"]
        input_model = getattr(module, runtime["input_model"])
        output_model = getattr(module, runtime["output_model"])
        config_model = (
            getattr(module, runtime["config_model"])
            if runtime.get("config_model")
            else None
        )
        return FunctionSchemaResponse(
            input_schema=input_model.model_json_schema(),
            output_schema=output_model.model_json_schema(),
            config_schema=config_model.model_json_schema() if config_model else None,
            code_hash=manifest["code_hash"],
        )

    def job_status(self, run_id: UUID) -> FunctionJobStatusResponse:
        job = self._get_job(run_id)
        return FunctionJobStatusResponse(
            run_id=job.run_id,
            job_id=job.job_id,
            status=job.status,
            output_data=job.output_data,
            error=job.error,
            code_hash=job.code_hash,
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_ms=job.duration_ms,
        )

    def job_logs(self, run_id: UUID) -> FunctionLogsResponse:
        job = self._get_job(run_id)
        return FunctionLogsResponse(run_id=run_id, logs=job.logs)

    def delete_job(self, run_id: UUID) -> bool:
        return self.jobs.pop(run_id, None) is not None

    def _get_job(self, run_id: UUID) -> StoredJob:
        job = self.jobs.get(run_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Function job not found")
        return job

    async def _run_job(
        self,
        job: StoredJob,
        *,
        pod_id: UUID,
        function_name: str,
        request: FunctionExecuteRequest,
        token: str,
    ) -> None:
        job.status = "running"
        job.started_at = utc_timestamp()
        result = await self._execute_sync(
            pod_id=pod_id,
            function_name=function_name,
            request=request,
            token=token,
        )
        job.logs = result.logs
        job.output_data = result.output_data
        job.error = result.error
        job.code_hash = result.code_hash
        job.duration_ms = result.duration_ms
        job.completed_at = utc_timestamp()
        job.status = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "timeout": "timeout",
        }[result.status]

    async def _execute_sync(
        self,
        *,
        pod_id: UUID,
        function_name: str,
        request: FunctionExecuteRequest,
        token: str,
    ) -> FunctionInvokeResponse:
        started = time.monotonic()
        logs: list[FunctionLogEntry] = []
        try:
            verified, metadata = self._authorize_and_fetch(
                pod_id=pod_id,
                function_name=function_name,
                token=token,
            )
            cache_dir, manifest = self.ensure_cached(metadata)
            module = self.load_module(cache_dir, manifest["code_hash"])
            output = await asyncio.wait_for(
                self.invoke_module(
                    module,
                    manifest=manifest,
                    metadata=metadata,
                    verified=verified,
                    request=request,
                    token=token,
                    logs=logs,
                ),
                timeout=request.timeout_seconds,
            )
            return FunctionInvokeResponse(
                status="completed",
                output_data=output,
                logs=logs,
                code_hash=manifest["code_hash"],
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except asyncio.TimeoutError:
            return FunctionInvokeResponse(
                status="timeout",
                error=RuntimeErrorInfo(name="TimeoutError", message="Function timed out"),
                logs=logs,
                code_hash="",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            logs.append(log_entry("system", str(exc)))
            return FunctionInvokeResponse(
                status="failed",
                error=RuntimeErrorInfo(
                    name=type(exc).__name__,
                    message=str(exc),
                    traceback=traceback.format_exc().splitlines(),
                ),
                logs=logs,
                code_hash="",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    def _authorize_and_fetch(
        self,
        *,
        pod_id: UUID,
        function_name: str,
        token: str,
    ) -> tuple[VerifiedToken, FunctionMetadata]:
        client = self.api_client(token)
        verified = client.verify_token()
        if verified.pod_id is not None and verified.pod_id != pod_id:
            raise HTTPException(status_code=403, detail="Token is not delegated to this pod")
        if verified.function_name is not None and verified.function_name != function_name:
            raise HTTPException(status_code=403, detail="Token is not delegated to this function")
        metadata = client.get_function(pod_id, function_name)
        if metadata.pod_id != pod_id or metadata.name != function_name:
            raise HTTPException(status_code=409, detail="Function metadata does not match request path")
        if verified.function_id is not None and verified.function_id != metadata.id:
            raise HTTPException(status_code=403, detail="Token is not delegated to this function")
        return verified, metadata

    def cache_dir(self, metadata: FunctionMetadata, code_hash: str) -> Path:
        return (
            self.workspace_root
            / "pods"
            / str(metadata.pod_id)
            / "functions"
            / metadata.name
            / code_hash
        )

    def ensure_packages(self, packages: list[str]) -> None:
        """Install the function's declared pip dependencies into the user site.

        Runs as the non-root runtime user, so `pip install --user` lands in
        ~/.local (on this interpreter's sys.path) and is importable by the loaded
        function module. Idempotent per container via ``_ensured_packages``; raises
        a clear error on an invalid spec or a failed/slow install so the run fails
        with a readable message instead of an opaque ImportError.
        """
        pending: list[str] = []
        for spec in packages:
            if not is_valid_python_package(spec):
                raise RuntimeError(f"Invalid python package specifier: {spec!r}")
            if spec not in self._ensured_packages:
                pending.append(spec)
        if not pending:
            return
        if len(pending) > MAX_PYTHON_PACKAGES:
            raise RuntimeError(
                f"Too many python packages declared ({len(pending)} > {MAX_PYTHON_PACKAGES})."
            )
        try:
            proc = subprocess.run(
                [
                    sys.executable, "-m", "pip", "install", "--user",
                    "--no-input", "--disable-pip-version-check", "-q", *pending,
                ],
                capture_output=True,
                text=True,
                timeout=PACKAGE_INSTALL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Installing python package(s) {pending} timed out after "
                f"{PACKAGE_INSTALL_TIMEOUT_SECONDS}s."
            ) from exc
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
            raise RuntimeError(
                f"Failed to install python package(s) {pending}: {tail}"
            )
        self._ensured_packages.update(pending)
        # `pip install --user` writes to the user site, but site.py only puts that
        # dir on sys.path at startup *if it already existed*. In a long-running
        # executor whose first install just created it, the dir is missing from
        # sys.path — so add it (addsitedir also processes any .pth files) before the
        # new module is imported.
        user_site = site.getusersitepackages()
        if user_site and os.path.isdir(user_site) and user_site not in sys.path:
            site.addsitedir(user_site)
        importlib.invalidate_caches()

    def ensure_cached(self, metadata: FunctionMetadata) -> tuple[Path, dict[str, Any]]:
        code_hash = metadata.code_hash or function_code_hash(metadata.code)
        # Ensure declared dependencies before any module load (execute AND schema
        # extraction both call ensure_cached, so a top-level `import <dep>` works in
        # both). Idempotent, so it's safe to run on cache hits too.
        packages = parse_python_packages(metadata.code)
        if packages:
            self.ensure_packages(packages)
        cache_dir = self.cache_dir(metadata, code_hash)
        manifest_path = cache_dir / MANIFEST_NAME
        function_path = cache_dir / FUNCTION_FILE_NAME
        if manifest_path.exists() and function_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                if manifest.get("code_hash") == code_hash:
                    return cache_dir, manifest
            except json.JSONDecodeError:
                pass

        cache_dir.mkdir(parents=True, exist_ok=True)
        input_model, output_model, entrypoint, config_model = parse_code_headers(metadata.code)
        function_path.write_text(metadata.code)
        manifest = {
            "code_hash": code_hash,
            "function": metadata.model_dump(mode="json", exclude={"code"}),
            "python_packages": packages,
            "runtime": {
                "input_model": input_model,
                "output_model": output_model,
                "function_name": entrypoint,
                "config_model": config_model,
            },
        }
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
        return cache_dir, manifest

    def load_module(self, cache_dir: Path, code_hash: str) -> ModuleType:
        source_path = cache_dir / FUNCTION_FILE_NAME
        module_name = f"_lemma_function_executor_{code_hash}"
        spec = importlib.util.spec_from_file_location(module_name, source_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load function source at {source_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    async def invoke_module(
        self,
        module: ModuleType,
        *,
        manifest: dict[str, Any],
        metadata: FunctionMetadata,
        verified: VerifiedToken,
        request: FunctionExecuteRequest,
        token: str,
        logs: list[FunctionLogEntry],
    ) -> dict[str, Any]:
        runtime = manifest["runtime"]
        input_model = getattr(module, runtime["input_model"])
        output_model = getattr(module, runtime["output_model"])
        function = getattr(module, runtime["function_name"])
        config_model = (
            getattr(module, runtime["config_model"])
            if runtime.get("config_model")
            else None
        )
        config = metadata.config
        if config_model is not None and metadata.config is not None:
            config = config_model(**metadata.config)
        data = input_model(**request.input_data)
        ctx = FunctionExecutionContext(
            run_id=request.run_id,
            function_id=metadata.id,
            function_name=metadata.name,
            pod_id=metadata.pod_id,
            organization_id=verified.organization_id,
            user_id=verified.user_id,
            user_email=verified.email,
            lemma_token=token,
            lemma_base_url=self.lemma_base_url,
            config=config,
            workspace_root=str(self.workspace_root),
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        invocation_env = {
            "LEMMA_TOKEN": token,
            "LEMMA_BASE_URL": self.lemma_base_url,
            "LEMMA_USER_ID": str(verified.user_id),
            "LEMMA_POD_ID": str(metadata.pod_id),
        }
        if verified.organization_id is not None:
            invocation_env["LEMMA_ORG_ID"] = str(verified.organization_id)
        if verified.email:
            invocation_env["LEMMA_USER_EMAIL"] = verified.email
        async with self.invocation_lock:
            with (
                patched_environ(invocation_env),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = function(ctx, data)
                if inspect.isawaitable(result):
                    result = await result
        if stdout.getvalue():
            logs.append(log_entry("stdout", stdout.getvalue()))
        if stderr.getvalue():
            logs.append(log_entry("stderr", stderr.getvalue()))
        if hasattr(result, "model_dump"):
            output = result.model_dump()
        elif isinstance(result, dict):
            output = result
        else:
            output = output_model.model_validate(result).model_dump()
        output_model(**output)
        return output


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return token


def build_app(executor: FunctionExecutor | None = None) -> FastAPI:
    app = FastAPI(title="AgentBox Function Executor", version="0.1.0")
    app.state.executor = executor or FunctionExecutor()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readiness")
    async def readiness() -> dict[str, str]:
        # Returns 200 only once this app is bound and serving, i.e. it can
        # accept an execute request. The executor is constructed eagerly in
        # build_app() with no async warm-up, so a served response is the
        # readiness signal callers probe before POSTing /execute (avoids the
        # cold-start 502 while the app's port is still binding).
        return {"status": "ready"}

    @app.post("/pods/{pod_id}/functions/{function_name}/execute")
    async def execute_function(
        pod_id: UUID,
        function_name: str,
        request: FunctionExecuteRequest,
        authorization: str | None = Header(default=None),
    ):
        return await app.state.executor.execute(
            pod_id=pod_id,
            function_name=function_name,
            request=request,
            token=bearer_token(authorization),
        )

    @app.post(
        "/pods/{pod_id}/functions/{function_name}/schemas",
        response_model=FunctionSchemaResponse,
    )
    async def extract_schemas(
        pod_id: UUID,
        function_name: str,
        request: FunctionSchemaRequest,
        authorization: str | None = Header(default=None),
    ) -> FunctionSchemaResponse:
        return await app.state.executor.schemas(
            pod_id=pod_id,
            function_name=function_name,
            request=request,
            token=bearer_token(authorization),
        )

    @app.get("/runs/{run_id}", response_model=FunctionJobStatusResponse)
    async def get_run(run_id: UUID) -> FunctionJobStatusResponse:
        return app.state.executor.job_status(run_id)

    @app.get("/runs/{run_id}/logs", response_model=FunctionLogsResponse)
    async def get_run_logs(run_id: UUID) -> FunctionLogsResponse:
        return app.state.executor.job_logs(run_id)

    @app.delete("/runs/{run_id}")
    async def delete_run(run_id: UUID) -> dict[str, bool | str]:
        return {"run_id": str(run_id), "deleted": app.state.executor.delete_job(run_id)}

    return app


app = build_app()
