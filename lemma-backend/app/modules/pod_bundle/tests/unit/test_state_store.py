"""State store semantics against an in-memory fake of RedisJsonCache."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.modules.pod_bundle.domain.state import (
    BundleSource,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.infrastructure.state_store import PodBundleStateStore
from app.modules.pod_bundle.infrastructure import state_store as state_store_module


class FakeJsonCache:
    """Duck-typed stand-in for RedisJsonCache (get_json/set_json/delete)."""

    def __init__(self):
        self.data: dict[str, object] = {}
        self.ttl_writes: list[str] = []

    async def get_json(self, suffix: str):
        return self.data.get(suffix)

    async def set_json(self, suffix: str, value, *, ttl_seconds=None):
        # RedisJsonCache always (re)sets EX on write — every save refreshes
        # the TTL, which is the "6h past last activity" behavior.
        self.data[suffix] = value
        self.ttl_writes.append(suffix)

    async def delete(self, suffix: str):
        self.data.pop(suffix, None)

    async def close(self):
        pass


def _state() -> ImportState:
    return ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind="URL"),
    )


@pytest.fixture
def cache() -> FakeJsonCache:
    return FakeJsonCache()


@pytest.fixture
def store(cache: FakeJsonCache) -> PodBundleStateStore:
    return PodBundleStateStore(cache=cache)


async def test_save_and_get_round_trip(store: PodBundleStateStore):
    state = _state()
    await store.save_import(state)

    loaded = await store.get_import(state.import_id)
    assert loaded is not None
    assert loaded.import_id == state.import_id
    assert loaded.status == ImportStatus.QUEUED


async def test_missing_key_returns_none(store: PodBundleStateStore):
    assert await store.get_import(uuid4()) is None


async def test_every_save_bumps_seq_and_refreshes_ttl(
    store: PodBundleStateStore, cache: FakeJsonCache
):
    state = _state()
    await store.save_import(state)
    assert state.seq == 1

    state.status = ImportStatus.PLANNING
    await store.save_import(state)
    assert state.seq == 2

    loaded = await store.get_import(state.import_id)
    assert loaded.seq == 2
    # Two writes = two TTL refreshes (RedisJsonCache sets EX on every set).
    assert len(cache.ttl_writes) == 2


async def test_delete_removes_document(store: PodBundleStateStore):
    state = _state()
    await store.save_import(state)
    await store.delete_import(state.import_id)
    assert await store.get_import(state.import_id) is None


async def test_kinds_are_namespaced(store: PodBundleStateStore, cache: FakeJsonCache):
    state = _state()
    await store.save_import(state)
    # An export lookup with the same UUID must not see the import document.
    assert await store.get_export(state.import_id) is None
    assert f"import:{state.import_id}" in cache.data


class _Session:
    def __init__(self, model=None):
        self.model = model

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, model_type, import_id):
        del model_type, import_id
        return self.model


class _PersistSession(_Session):
    def begin(self):
        return _Session()

    async def scalar(self, statement):
        del statement
        return self.model

    async def execute(self, statement):
        del statement


async def test_durable_get_prefers_postgres_snapshot(monkeypatch, store):
    state = _state()
    session = _Session(SimpleNamespace(snapshot=state.model_dump(mode="json")))
    monkeypatch.setattr(state_store_module, "async_session_maker", lambda: session)
    store._durable = True

    loaded = await store.get_import(state.import_id)

    assert loaded == state


async def test_durable_get_contains_invalid_legacy_cache(monkeypatch, store):
    class _FailingCache(FakeJsonCache):
        async def get_json(self, suffix: str):
            del suffix
            raise RedisError("redis unavailable")

    durable = PodBundleStateStore(cache=_FailingCache())
    durable._durable = True
    monkeypatch.setattr(
        state_store_module, "async_session_maker", lambda: _Session(None)
    )

    assert await durable.get_import(uuid4()) is None


async def test_durable_get_imports_legacy_redis_snapshot(monkeypatch, store, cache):
    state = _state()
    cache.data[f"import:{state.import_id}"] = state.model_dump(mode="json")
    store._durable = True
    monkeypatch.setattr(
        state_store_module, "async_session_maker", lambda: _Session(None)
    )
    persist = AsyncMock(return_value=True)
    monkeypatch.setattr(store, "_persist_import", persist)

    loaded = await store.get_import(state.import_id)

    assert loaded == state
    persist.assert_awaited_once_with(loaded)


async def test_durable_save_does_not_mirror_rejected_terminal_regression(
    monkeypatch, store, cache
):
    state = _state()
    store._durable = True
    monkeypatch.setattr(store, "_persist_import", AsyncMock(return_value=False))

    await store.save_import(state)

    assert cache.data == {}


async def test_durable_save_survives_redis_mirror_failure(monkeypatch):
    class _FailingCache(FakeJsonCache):
        async def set_json(self, suffix: str, value, *, ttl_seconds=None):
            del suffix, value, ttl_seconds
            raise RedisError("redis unavailable")

    store = PodBundleStateStore(cache=_FailingCache())
    store._durable = True
    monkeypatch.setattr(store, "_persist_import", AsyncMock(return_value=True))

    await store.save_import(_state())


async def test_persist_refuses_to_overwrite_terminal_state(monkeypatch, store):
    existing = _state()
    existing.status = ImportStatus.CANCELLED
    session = _PersistSession(
        SimpleNamespace(snapshot=existing.model_dump(mode="json"))
    )
    monkeypatch.setattr(state_store_module, "async_session_maker", lambda: session)

    assert await store._persist_import(_state()) is False


async def test_persist_preserves_cancellation_and_merges_committed_steps(
    monkeypatch, store
):
    existing = _state()
    existing.status = ImportStatus.CANCELLING
    existing.cancel_requested_at = datetime.now(timezone.utc)
    existing.committed_steps = [2]
    incoming = existing.model_copy(deep=True)
    incoming.status = ImportStatus.APPLYING
    incoming.cancel_requested_at = None
    incoming.committed_steps = [1]
    session = _PersistSession(
        SimpleNamespace(snapshot=existing.model_dump(mode="json"))
    )
    monkeypatch.setattr(state_store_module, "async_session_maker", lambda: session)

    assert await store._persist_import(incoming) is True
    assert incoming.status is ImportStatus.CANCELLING
    assert incoming.cancel_requested_at == existing.cancel_requested_at
    assert incoming.committed_steps == [1, 2]
