from __future__ import annotations

from typing import Any

from lemma_sdk.config import (
    get_server_config,
    normalize_server_name,
    put_server_config,
    save_config,
)
from .state import CliState, fail


def upsert_server(
    state: CliState,
    *,
    name: str,
    base_url: str | None = None,
    auth_url: str | None = None,
    token: str | None = None,
    copy_current: bool = False,
    make_active: bool = False,
) -> dict[str, Any]:
    if state.server_read_only:
        fail("The env server is read-only. Unset LEMMA_TOKEN before editing servers.")
    if state.root_config is None:
        fail("Unable to load CLI server config.")

    server_name = normalize_server_name(name)
    existing = (state.root_config.get("servers") or {}).get(server_name)
    if existing is None and copy_current:
        server = dict(state.config)
        server.pop("_runtime", None)
        server.pop("_sources", None)
    else:
        server = get_server_config(state.root_config, server_name)
    if base_url:
        server["base_url"] = base_url.rstrip("/")
    if auth_url:
        server["auth_url"] = auth_url.rstrip("/")
    if token:
        server["token"] = token
    server.setdefault("defaults", {})
    state.root_config = put_server_config(state.root_config, server_name, server)
    if make_active:
        state.server = server_name
        state.config = server
    else:
        state.root_config["active_server"] = state.server
    save_config(state.config_path, state.root_config)
    return {
        "name": server_name,
        "base_url": server.get("base_url"),
        "auth_url": server.get("auth_url"),
        "active": state.root_config.get("active_server") == server_name,
        "path": str(state.config_path),
    }
