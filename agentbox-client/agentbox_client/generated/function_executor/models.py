from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


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

