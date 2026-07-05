"""SQL connector executor — read-only queries against an external database.

An ``sql``-kind connector stores its connection config on the auth-config
(``connection_config``: dialect/host/port/database) and per-user secrets on the
account (``third_party_credentials``: username/password). Operations are a fixed
set — ``query`` / ``list_tables`` / ``describe_table`` — selected by the
``execution`` descriptor's ``op``.

Safety: ``query`` accepts only a single read-only SELECT-family statement
(validated with sqlglot, reusing the datastore's forbidden-node/allowed-root
rules), runs in a ``READ ONLY`` transaction with a ``statement_timeout``, and
caps returned rows. Engines are cached per DSN and reused across calls.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any
from urllib.parse import quote_plus

import sqlglot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.core.log.log import get_logger
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionValidationError,
)

logger = get_logger(__name__)

# Reuse the datastore read-only policy (mutation/DDL nodes forbidden anywhere).
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
    exp.Alter, exp.TruncateTable, exp.Grant, exp.Revoke, exp.Copy, exp.Command,
)
_ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery, exp.With,
)

_DIALECT_DRIVERS = {"postgresql": "postgresql+asyncpg", "postgres": "postgresql+asyncpg"}
_DEFAULT_ROW_CAP = 1000
_DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
_ENGINE_CACHE_MAX = 32


def _ensure_read_only(sql: str) -> None:
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
    except SqlglotError as exc:
        raise OperationExecutionValidationError(f"Could not parse SQL query: {exc}") from exc
    if not statements:
        raise OperationExecutionValidationError("Empty SQL query.")
    if len(statements) > 1:
        raise OperationExecutionValidationError("Only a single SQL statement is allowed.")
    statement = statements[0]
    if not isinstance(statement, _ALLOWED_ROOTS) or statement.find(*_FORBIDDEN_NODES):
        raise OperationExecutionValidationError("Only read-only SELECT queries are allowed.")


class SqlExecutor:
    def __init__(self) -> None:
        self._engines: "OrderedDict[str, AsyncEngine]" = OrderedDict()

    async def execute(
        self,
        *,
        connector_id: str,
        operation_name: str,
        execution: dict[str, Any],
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
        connection_config: dict[str, Any] | None = None,
    ) -> Any:
        op = (execution or {}).get("op") or ""
        engine = self._engine_for(connection_config or {}, third_party_credentials or {})
        row_cap = int((connection_config or {}).get("row_cap") or _DEFAULT_ROW_CAP)
        payload = payload or {}

        if op == "query":
            sql = str(payload.get("query") or "").strip()
            if not sql:
                raise OperationExecutionValidationError("A 'query' string is required.")
            _ensure_read_only(sql)
            return await self._run_select(engine, sql, row_cap=row_cap)
        if op == "list_tables":
            schema = payload.get("schema")
            sql = (
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                + ("AND table_schema = :schema " if schema else "")
                + "ORDER BY table_schema, table_name"
            )
            return await self._run_select(
                engine, sql, row_cap=row_cap, params={"schema": schema} if schema else None
            )
        if op == "describe_table":
            table = payload.get("table")
            if not table:
                raise OperationExecutionValidationError("A 'table' name is required.")
            schema = payload.get("schema")
            sql = (
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_name = :table "
                + ("AND table_schema = :schema " if schema else "")
                + "ORDER BY ordinal_position"
            )
            params = {"table": table} | ({"schema": schema} if schema else {})
            return await self._run_select(engine, sql, row_cap=row_cap, params=params)

        raise OperationExecutionValidationError(
            f"Unsupported SQL operation '{op}' for '{operation_name}'.", details={"op": op}
        )

    # --- connection ---------------------------------------------------------

    def _engine_for(self, connection_config: dict[str, Any], creds: dict[str, Any]) -> AsyncEngine:
        dialect = str(connection_config.get("dialect") or "postgresql").lower()
        driver = _DIALECT_DRIVERS.get(dialect)
        if driver is None:
            raise OperationExecutionValidationError(
                f"Unsupported SQL dialect '{dialect}'. Supported: postgresql."
            )
        host = connection_config.get("host")
        database = connection_config.get("database")
        if not host or not database:
            raise OperationExecutionValidationError("SQL connection requires 'host' and 'database'.")
        port = connection_config.get("port") or 5432
        user = quote_plus(str(creds.get("username") or ""))
        password = quote_plus(str(creds.get("password") or ""))
        userinfo = f"{user}:{password}@" if user else ""
        dsn = f"{driver}://{userinfo}{host}:{port}/{database}"

        engine = self._engines.get(dsn)
        if engine is None:
            engine = create_async_engine(dsn, pool_size=2, max_overflow=2, pool_pre_ping=True)
            self._engines[dsn] = engine
            self._engines.move_to_end(dsn)
            while len(self._engines) > _ENGINE_CACHE_MAX:
                _old_dsn, _old_engine = self._engines.popitem(last=False)
        else:
            self._engines.move_to_end(dsn)
        return engine

    async def _run_select(
        self,
        engine: AsyncEngine,
        sql: str,
        *,
        row_cap: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(text(f"SET statement_timeout = {_DEFAULT_STATEMENT_TIMEOUT_MS}"))
                result = await conn.execute(text(sql), params or {})
                columns = list(result.keys())
                rows = result.fetchmany(row_cap + 1)
        except OperationExecutionValidationError:
            raise
        except Exception as exc:  # noqa: BLE001 - map any driver error to a clean domain error
            raise OperationExecutionInfrastructureError(
                f"SQL execution failed: {exc}",
                details={"provider": "sql", "upstream_message": str(exc)},
            ) from exc

        truncated = len(rows) > row_cap
        rows = rows[:row_cap]
        return {
            "columns": columns,
            "rows": [dict(zip(columns, _coerce_row(row))) for row in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }


def _coerce_row(row: Any) -> list[Any]:
    values = []
    for v in row:
        if isinstance(v, (str, int, float, bool)) or v is None:
            values.append(v)
        else:
            values.append(str(v))
    return values
