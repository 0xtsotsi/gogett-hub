"""Per-folder Lemma context via a project ``.lemma.env`` file.

A user-system feature: on a laptop (humans and local coding agents like Claude
Code / Codex) the CLI auto-discovers a ``.lemma.env`` walking up from the cwd and
loads its ``LEMMA_*`` vars into the environment *before* state is resolved, so a
project folder can bind to a specific pod/server without mutating the global
``~/.lemma/config.json`` or exporting env vars by hand.

Loading is intentionally minimal — the CLI already resolves pod/org/server from
``LEMMA_*`` env vars, so this only has to populate them. Real process env always
wins (a value already in ``os.environ`` is never overwritten), which keeps this
cleanly separated from env-only contexts such as agentbox.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .dotenv import read_env_file

LEMMA_ENV_NAME = ".lemma.env"
LOCAL_ENV_NAME = ".env"
LEMMA_PREFIX = "LEMMA_"


def find_project_dir(start: Path | None = None) -> Path | None:
    """Nearest ancestor of ``start`` (default cwd) that contains ``.lemma.env``.

    Discovery is anchored on ``.lemma.env`` (a lone ``.env`` is never auto-loaded)
    and walks up to a ceiling — the git repo root if inside one, else ``$HOME`` —
    both inclusive, so a stray file above the project can't bind every folder.
    """
    try:
        current = (start or Path.cwd()).resolve()
    except OSError:
        return None
    home = Path.home().resolve()
    while True:
        if (current / LEMMA_ENV_NAME).is_file():
            return current
        # Ceilings (current already checked above): stop at the git repo root and
        # at $HOME so discovery never climbs into unrelated parents.
        if (current / ".git").exists():
            return None
        if current == home or current.parent == current:
            return None
        current = current.parent


def load_project_env(start: Path | None = None) -> dict[str, Any]:
    """Discover the project ``.lemma.env`` (+ sibling ``.env`` override) and apply
    its ``LEMMA_*`` keys to ``os.environ``.

    Precedence, achieved by load order: real process env > ``.env`` (local,
    gitignored) > ``.lemma.env`` (committed). Only ``LEMMA_*`` keys are applied, so
    unrelated secrets in a ``.env`` are never pulled into the CLI process. Returns a
    summary ``{path, local_path, applied, token_in_committed_file}`` for ``config
    show`` (``path`` is None when no ``.lemma.env`` was found — a no-op).
    """
    info: dict[str, Any] = {
        "path": None,
        "local_path": None,
        "applied": [],
        "token_in_committed_file": False,
        "skipped_reason": None,
    }
    project_dir = find_project_dir(start)
    if project_dir is None:
        return info

    committed_path = project_dir / LEMMA_ENV_NAME
    local_path = project_dir / LOCAL_ENV_NAME
    committed = read_env_file(committed_path)
    local = read_env_file(local_path)

    info["path"] = str(committed_path)
    if local_path.is_file():
        info["local_path"] = str(local_path)
    info["token_in_committed_file"] = "LEMMA_TOKEN" in committed

    # A real LEMMA_TOKEN already in the environment means an env-driven context
    # (e.g. agentbox, which injects token + pod and has no config.json). The
    # project file must never interfere there — skip it entirely so a committed
    # .lemma.env can't redirect the server or shadow the injected identity.
    if os.environ.get("LEMMA_TOKEN"):
        info["skipped_reason"] = "real LEMMA_TOKEN present (env-driven context)"
        return info

    merged: dict[str, str] = {}
    merged.update(committed)
    merged.update(local)  # local (.env) overrides committed (.lemma.env)
    for key, value in merged.items():
        if not key.startswith(LEMMA_PREFIX):
            continue
        if key in os.environ:  # real process env always wins
            continue
        os.environ[key] = value
        info["applied"].append(key)
    return info
