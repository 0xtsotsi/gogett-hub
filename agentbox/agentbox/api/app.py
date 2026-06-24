from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from kubernetes.client.rest import ApiException

from agentbox.config import settings
from agentbox.providers import build_sandbox_provider
from agentbox.state import AgentBoxStateStore

from .apps import router as apps_router
from .lifecycle import cleanup_loop
from .sandboxes import router as sandboxes_router
from .sessions import router as sessions_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    provider = build_sandbox_provider()
    store = AgentBoxStateStore(settings.agentbox_state_db_path)
    app.state.sandbox_provider = provider
    app.state.store = store
    app.state.sandbox_app_ready_cache = set()
    app.state.cleanup_task = asyncio.create_task(cleanup_loop(provider, store))
    try:
        yield
    finally:
        app.state.cleanup_task.cancel()
        try:
            await app.state.cleanup_task
        except asyncio.CancelledError:
            pass
        store.close()


app = FastAPI(title="AgentBox Manager", version="0.1.0", lifespan=lifespan)


@app.exception_handler(ApiException)
async def kubernetes_api_exception_handler(
    request: Request, exc: ApiException
) -> JSONResponse:
    del request
    exc_status = int(exc.status or 0)
    status_code = exc_status if 400 <= exc_status < 600 else 502
    detail: dict[str, object] = {
        "message": exc.reason or "Kubernetes API request failed",
        "status": exc.status,
    }
    if exc.body:
        try:
            body = json.loads(exc.body)
        except json.JSONDecodeError:
            detail["body"] = exc.body
        else:
            detail["message"] = body.get("message") or detail["message"]
            detail["reason"] = body.get("reason")
            detail["details"] = body.get("details")
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(sandboxes_router)
app.include_router(sessions_router)
app.include_router(apps_router)
