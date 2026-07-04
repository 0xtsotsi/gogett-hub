"""Per-folder `.lemma.env` discovery, loading precedence, and org-from-pod."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

from lemma_cli.cli_core.context import org_for, pod_lookup_error
from lemma_cli.cli_core.project_env import find_project_dir, load_project_env
from lemma_cli.cli_core.state import CliState, build_state


@pytest.fixture(autouse=True)
def _isolate_lemma_env(monkeypatch):
    """Start each test with no ambient LEMMA_* (load_project_env writes os.environ
    directly, so snapshot and fully restore around the test)."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("LEMMA_")}
    for key in list(saved):
        monkeypatch.delenv(key, raising=False)
    yield
    for key in [k for k in os.environ if k.startswith("LEMMA_")]:
        del os.environ[key]
    os.environ.update(saved)


def _repo(tmp_path: Path) -> Path:
    """A project dir that also looks like a git repo root (a discovery ceiling)."""
    root = (tmp_path / "repo").resolve()
    (root / "apps" / "web").mkdir(parents=True)
    (root / ".git").mkdir()
    return root


# --- discovery ------------------------------------------------------------


def test_finds_lemma_env_from_nested_subdir(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_POD_ID=pod_x\n")
    assert find_project_dir(root / "apps" / "web") == root


def test_discovery_stops_at_git_root(tmp_path):
    # .lemma.env sits ABOVE the git repo root -> must not be discovered from inside.
    outer = tmp_path.resolve()
    (outer / ".lemma.env").write_text("LEMMA_POD_ID=pod_outer\n")
    root = _repo(tmp_path)  # tmp_path/repo with its own .git, no .lemma.env
    assert find_project_dir(root / "apps" / "web") is None


def test_discovery_stops_at_home(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    (home / "proj").mkdir(parents=True)
    (tmp_path / ".lemma.env").write_text("LEMMA_POD_ID=above_home\n")  # above $HOME
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert find_project_dir(home / "proj") is None


def test_no_lemma_env_is_noop(tmp_path):
    root = _repo(tmp_path)
    (root / ".env").write_text("LEMMA_POD_ID=lone_env\n")  # lone .env is NOT loaded
    info = load_project_env(root / "apps" / "web")
    assert info["path"] is None
    assert info["applied"] == []
    assert "LEMMA_POD_ID" not in os.environ


# --- loading precedence ---------------------------------------------------


def test_loads_only_lemma_prefixed_keys(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text(
        "LEMMA_POD_ID=pod_x\nOPENAI_API_KEY=sk-secret\nFOO=bar\n"
    )
    info = load_project_env(root)
    assert os.environ["LEMMA_POD_ID"] == "pod_x"
    assert "OPENAI_API_KEY" not in os.environ
    assert info["applied"] == ["LEMMA_POD_ID"]


def test_local_env_overrides_committed(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_POD_ID=committed\nLEMMA_SERVER=default\n")
    (root / ".env").write_text("LEMMA_POD_ID=local_override\n")
    load_project_env(root)
    assert os.environ["LEMMA_POD_ID"] == "local_override"
    assert os.environ["LEMMA_SERVER"] == "default"  # committed value survives


def test_real_env_wins_over_files(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_POD_ID=from_file\n")
    os.environ["LEMMA_POD_ID"] = "from_shell"
    info = load_project_env(root)
    assert os.environ["LEMMA_POD_ID"] == "from_shell"
    assert "LEMMA_POD_ID" not in info["applied"]


def test_real_token_skips_file_entirely(tmp_path):
    # agentbox-style: a real token in env => project file must be fully ignored.
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\nLEMMA_POD_ID=file_pod\n")
    os.environ["LEMMA_TOKEN"] = "real-token"
    os.environ["LEMMA_POD_ID"] = "real_pod"
    info = load_project_env(root)
    assert info["applied"] == []
    assert info["skipped_reason"]
    assert os.environ["LEMMA_POD_ID"] == "real_pod"
    assert "LEMMA_SERVER" not in os.environ


def test_token_in_committed_file_flagged(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_TOKEN=tok\nLEMMA_POD_ID=pod_x\n")
    info = load_project_env(root)
    assert info["token_in_committed_file"] is True


# --- build_state integration ---------------------------------------------


def _build(tmp_path):
    """build_state against a throwaway (nonexistent) config so no real ~/.lemma."""
    return build_state(
        config_file=tmp_path / "config.json",
        server=None,
        base_url=None,
        auth_url=None,
        token=None,
        timeout=30.0,
        no_verify_ssl=False,
        output="json",
    )


def test_project_server_selected_without_token(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\nLEMMA_POD_ID=pod_x\n")
    load_project_env(root)
    state = _build(tmp_path)
    assert state.server == "local"
    assert state.server_read_only is False
    from lemma_cli.cli_core.context import selected_pod

    assert selected_pod(state) == "pod_x"


def test_flag_and_env_beat_project_server(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text("LEMMA_SERVER=local\n")
    load_project_env(root)
    # explicit --server (flag) wins
    state = build_state(
        config_file=tmp_path / "config.json",
        server="default",
        base_url=None,
        auth_url=None,
        token=None,
        timeout=30.0,
        no_verify_ssl=False,
        output="json",
    )
    assert state.server == "default"


def test_token_in_file_activates_env_server(tmp_path):
    root = _repo(tmp_path)
    (root / ".lemma.env").write_text(
        "LEMMA_TOKEN=tok\nLEMMA_BASE_URL=http://localhost:8711\nLEMMA_POD_ID=pod_x\n"
    )
    load_project_env(root)
    state = _build(tmp_path)
    assert state.server_read_only is True
    assert state.server == "env"


# --- org-from-pod + clear errors -----------------------------------------


class _FakePod:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class _FakePods:
    def __init__(self, data=None, exc=None):
        self._data = data
        self._exc = exc

    def get(self, pod_id):
        if self._exc is not None:
            raise self._exc
        return _FakePod(self._data)


class _FakeClient:
    def __init__(self, data=None, exc=None):
        self.pods = _FakePods(data, exc)


def _state_with_pod(pod="pod-1", org=None):
    return SimpleNamespace(config={"_runtime": {"pod": pod, "org": org}, "defaults": {}})


def test_org_for_uses_explicit_or_config(tmp_path):
    state = SimpleNamespace(config={"_runtime": {}, "defaults": {"org_id": "org-cfg"}})
    client = _FakeClient(data={"organization_id": "should-not-be-used"})
    assert org_for(client, state) == "org-cfg"


def test_org_for_resolves_from_pod_and_caches():
    state = _state_with_pod(pod="pod-1")
    client = _FakeClient(data={"id": "pod-1", "organization_id": "org-from-pod"})
    assert org_for(client, state) == "org-from-pod"
    # cached into runtime so later selected_org sees it
    assert state.config["_runtime"]["org"] == "org-from-pod"


def test_org_for_fails_without_org_or_pod():
    state = SimpleNamespace(config={"_runtime": {}, "defaults": {}})
    client = _FakeClient(data={})
    with pytest.raises(typer.Exit):
        org_for(client, state)


def test_pod_lookup_error_names_server_and_url():
    state = CliState(
        config_path=Path("/tmp/none.json"),
        config={"base_url": "http://localhost:8711"},
        base_url=None,
        auth_url=None,
        token=None,
        timeout=30.0,
        no_verify_ssl=False,
        output="json",
        server="local",
    )
    msg = pod_lookup_error("pod-x", state)
    assert "pod-x" in msg
    assert "local" in msg
    assert "http://localhost:8711" in msg
    assert "lemma pods list" in msg
