from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.events import handlers


def _state(
    status: ImportStatus,
    *,
    committed_steps: list[int] | None = None,
) -> ImportState:
    return ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind=BundleSourceKind.URL, url="https://lemma.test/b.zip"),
        status=status,
        current_step=4,
        committed_steps=committed_steps or [],
    )


class _Store:
    def __init__(self, state: ImportState | None) -> None:
        self.state = state
        self.saved: list[ImportState] = []

    async def get_import(self, import_id):
        assert self.state is None or import_id == self.state.import_id
        return self.state

    async def save_import(self, state):
        state.touch()
        self.state = state
        self.saved.append(state)


@pytest.mark.parametrize(
    "status",
    [
        ImportStatus.CANCELLING,
        ImportStatus.CANCELLED,
        ImportStatus.PARTIALLY_CANCELLED,
    ],
)
async def test_cancellation_requested_recognizes_every_stop_state(status):
    state = _state(status)

    assert await handlers._cancellation_requested(_Store(state), state.import_id) is state


async def test_cancellation_requested_ignores_missing_and_active_states():
    import_id = uuid4()
    assert await handlers._cancellation_requested(_Store(None), import_id) is None
    active = _state(ImportStatus.APPLYING)
    assert await handlers._cancellation_requested(_Store(active), active.import_id) is None


async def test_raise_if_cancelled_is_a_control_flow_boundary():
    cancelling = _state(ImportStatus.CANCELLING)
    with pytest.raises(handlers._ImportCancellation):
        await handlers._raise_if_cancelled(_Store(cancelling), cancelling.import_id)

    active = _state(ImportStatus.APPLYING)
    await handlers._raise_if_cancelled(_Store(active), active.import_id)


@pytest.mark.parametrize(
    ("committed_steps", "expected"),
    [([], ImportStatus.CANCELLED), ([1, 3], ImportStatus.PARTIALLY_CANCELLED)],
)
async def test_finalize_cancellation_is_terminal_and_reports_committed_steps(
    monkeypatch,
    committed_steps,
    expected,
):
    state = _state(ImportStatus.CANCELLING, committed_steps=committed_steps)
    store = _Store(state)
    staging = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(handlers, "publish_bundle_event", publish)

    await handlers._finalize_import_cancellation(store, staging, state)

    assert state.status is expected
    assert state.current_step is None
    assert state.completed_at is not None
    assert store.saved == [state]
    staging.delete_archive.assert_awaited_once_with("pod-imports", state.import_id)
    publish.assert_awaited_once()
    assert publish.await_args.args[1]["status"] == expected.value


async def test_finalize_cancellation_contains_staging_cleanup_failure(monkeypatch):
    state = _state(ImportStatus.CANCELLING)
    store = _Store(state)
    staging = AsyncMock()
    staging.delete_archive.side_effect = RuntimeError("object store unavailable")
    monkeypatch.setattr(handlers, "publish_bundle_event", AsyncMock())

    await handlers._finalize_import_cancellation(store, staging, state)

    assert state.status is ImportStatus.CANCELLED
    assert store.saved == [state]


async def test_apply_job_terminalizes_preexisting_cancelling_state(monkeypatch):
    state = _state(ImportStatus.CANCELLING)
    state.plan = SimplePlan()
    store = _Store(state)
    finalize = AsyncMock()
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    staging = AsyncMock()
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(handlers, "_finalize_import_cancellation", finalize)

    await handlers.apply_pod_import(
        {
            "import_id": str(state.import_id),
            "pod_id": str(state.pod_id),
            "user_id": str(state.user_id),
        }
    )

    finalize.assert_awaited_once()


@pytest.mark.parametrize(
    ("task", "extra_context"),
    [
        (handlers.import_pod_github, {"owner": None, "repo": None}),
        (
            handlers.import_pod_url,
            {"source_kind": "pod-exports", "source_id": str(uuid4())},
        ),
    ],
)
async def test_fetch_jobs_terminalize_cancellation_control_flow(
    monkeypatch, task, extra_context
):
    state = _state(ImportStatus.QUEUED)
    store = _Store(state)
    staging = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(
        handlers,
        "_raise_if_cancelled",
        AsyncMock(side_effect=handlers._ImportCancellation),
    )
    monkeypatch.setattr(handlers, "_finalize_import_cancellation", finalize)
    context = {"import_id": str(state.import_id), **extra_context}

    await task(context)

    finalize.assert_awaited_once_with(store, staging, state)


async def test_plan_job_terminalizes_cancellation_control_flow(monkeypatch):
    state = _state(ImportStatus.QUEUED)
    store = _Store(state)
    staging = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(
        handlers,
        "_plan_from_staging",
        AsyncMock(side_effect=handlers._ImportCancellation),
    )
    monkeypatch.setattr(handlers, "_finalize_import_cancellation", finalize)

    await handlers.plan_pod_import({"import_id": str(state.import_id)})

    finalize.assert_awaited_once_with(store, staging, state)


async def test_apply_job_catches_midflight_cancellation(monkeypatch):
    state = _state(ImportStatus.APPLYING)
    state.plan = SimplePlan()
    store = _Store(state)
    staging = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(
        handlers,
        "_raise_if_cancelled",
        AsyncMock(side_effect=handlers._ImportCancellation),
    )
    monkeypatch.setattr(handlers, "_finalize_import_cancellation", finalize)

    await handlers.apply_pod_import(
        {
            "import_id": str(state.import_id),
            "pod_id": str(state.pod_id),
            "user_id": str(state.user_id),
        }
    )

    finalize.assert_awaited_once_with(store, staging, state)


class SimplePlan:
    steps: list[object] = []
