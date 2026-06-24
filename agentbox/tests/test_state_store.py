from __future__ import annotations

import sqlite3
import time

from agentbox.schemas import SandboxEnsureRequest
from agentbox.state import AgentBoxStateStore


def test_mark_sandbox_active_keeps_stale_idle_sandbox_off_the_reaper(tmp_path):
    """A heartbeat resets a sessionless sandbox's idle clock so the reaper
    leaves it alone -- this is what keeps a long-running JOB's sandbox up while
    the function runs through the function_executor app (no runtime session)."""
    store = AgentBoxStateStore(str(tmp_path / "state.db"))
    try:
        store.upsert_sandbox("sb", SandboxEnsureRequest())
        # Simulate a sessionless sandbox that has been idle long enough to reap.
        stale = time.time() - 10_000
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE sandboxes SET idle_since_at = ? WHERE sandbox_id = ?",
                (stale, "sb"),
            )
        assert [s.sandbox_id for s in store.idle_sandboxes(300)] == ["sb"]

        # Heartbeat: resets the idle clock, buying another full timeout window.
        store.mark_sandbox_active("sb")
        assert store.idle_sandboxes(300) == []
    finally:
        store.close()


def test_state_store_migrates_legacy_sandbox_disk_size_column(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE sandboxes (
                sandbox_id TEXT PRIMARY KEY,
                env_json TEXT NOT NULL,
                disk_size_gb INTEGER NOT NULL,
                idle_since_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO sandboxes (
                sandbox_id, env_json, disk_size_gb, idle_since_at, created_at, updated_at
            )
            VALUES ('existing-sandbox', '{"OLD":"yes"}', 10, NULL, 1, 1)
            """
        )
        conn.execute(
            """
            CREATE TABLE sessions (
                sandbox_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                cwd TEXT NOT NULL,
                env_keys_json TEXT NOT NULL,
                last_active_at REAL NOT NULL,
                active_operations INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (sandbox_id, session_id),
                FOREIGN KEY (sandbox_id) REFERENCES sandboxes(sandbox_id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO sessions (
                sandbox_id, session_id, cwd, env_keys_json, last_active_at,
                active_operations, created_at, updated_at
            )
            VALUES ('existing-sandbox', 'session-1', '/workspace', '["OLD"]', 1, 0, 1, 1)
            """
        )
        conn.commit()
    finally:
        conn.close()

    store = AgentBoxStateStore(str(db_path))
    try:
        existing = store.get_sandbox("existing-sandbox")
        assert existing is not None
        assert existing.env == {"OLD": "yes"}
        session = store.get_session("existing-sandbox", "session-1")
        assert session is not None
        assert session.cwd == "/workspace"

        created = store.upsert_sandbox(
            "new-sandbox",
            SandboxEnsureRequest(env={"NEW": "yes"}),
        )
        assert created.env == {"NEW": "yes"}

        columns = store._conn.execute("PRAGMA table_info(sandboxes)").fetchall()
        assert [column["name"] for column in columns] == [
            "sandbox_id",
            "env_json",
            "idle_since_at",
            "created_at",
            "updated_at",
        ]
    finally:
        store.close()
