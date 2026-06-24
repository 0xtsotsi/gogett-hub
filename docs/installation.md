# Installation

There are two ways to run Lemma locally:

1. **Lemma Stack** (`lemma-stack`) — a self-contained local stack in containers. Best for trying Lemma, running it locally, or self-hosting.
2. **Developer setup** (`make dev`) — hot-reload checkout from source. Best for contributing to the platform itself.

---

## 1. Lemma Stack (one-line install)

The fastest way to get the full stack running: backend, frontend, Postgres, Redis, SuperTokens, and Kreuzberg — all in containers, all under `~/.lemma/local`.

### Prerequisites

- macOS or Linux
- `curl`
- A container runtime: **Podman** (recommended) or Docker. If neither is installed, the installer offers to install Podman for you.

### Install

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash
```

This installs [uv](https://docs.astral.sh/uv/) (if missing), installs `lemma-stack` as a uv tool, and runs `lemma-stack install`.

To pass arguments through:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash -s -- --runtime podman -y
```

### What you get

After install, the stack is running at:

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3711 |
| Backend API | http://localhost:8711 |
| API docs (Scalar) | http://localhost:8711/scalar |

Infrastructure (Postgres, Redis, SuperTokens, Kreuzberg) stays on a private container network — no host ports, no collisions with other projects.

### Configure

Configuration lives in `~/.lemma/local/config.toml`. Set API keys and backend env vars:

```bash
lemma-stack config set LEMMA_OPENAI_API_KEY fw-...
lemma-stack config set LEMMA_ANTHROPIC_API_KEY sk-ant-...
lemma-stack restart
```

### Commands

```text
lemma-stack install   [--runtime auto|docker|podman] [--channel stable|X.Y.Z]
                      [--manifest path.json] [--set KEY=VAL ...] [-y]
lemma-stack start | stop [--infra] | restart | status [--json]
lemma-stack logs <service> [-f]
lemma-stack doctor [--json]
lemma-stack config list|get|set|unset|edit|path
lemma-stack db shell|sql|url      lemma-stack redis cli
lemma-stack uninstall [--purge-data]
lemma-stack self version|info
```

### Install the CLI and SDKs

Once the stack is running, install the Lemma CLI to build and operate pods:

```bash
uv tool install lemma-terminal
```

Point the CLI at your local stack:

```bash
lemma servers select local
lemma auth login
```

Install the SDK for app code:

```bash
# TypeScript (app frontends)
npm install lemma-sdk

# Python (pod function code)
uv pip install lemma-sdk
```

### Uninstall

```bash
lemma-stack uninstall            # stop and remove containers
lemma-stack uninstall --purge-data   # also delete all data under ~/.lemma/local
```

---

## 2. Developer setup (from source)

For contributing to the platform — backend, frontend, and agentbox run on the host with hot reload; infrastructure runs in Docker/Podman.

### Prerequisites

- Python 3.14
- [uv](https://docs.astral.sh/uv/) — dependency manager
- Docker or Podman (for infrastructure services and e2e tests)
- [mkcert](https://github.com/FiloSottile/mkcert) — for local HTTPS (optional, only for full auth flow)
- Node.js 20+ and npm (for the frontend)

### Clone and run

```bash
git clone https://github.com/lemma-work/lemma-platform.git
cd lemma-platform

# Start backend, frontend, and agentbox with hot reload.
# First run installs deps, starts infra, and runs migrations.
make dev

# Authenticate the local CLI
lemma servers select local-dev
lemma auth login
```

### Developer ports

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3710 |
| API | http://localhost:8710 |
| Scalar docs | http://localhost:8710/scalar |
| Postgres | localhost:5432 |
| Redis | localhost:6379 |
| SuperTokens | http://localhost:3567 |

### Useful commands

```bash
make dev          # backend + frontend + agentbox with hot reload
make logs         # tail backend logs
make stop         # stop dev app processes
make stop-all     # also stop dev infra
make test-unit    # unit tests
make test-e2e     # e2e tests (requires Docker)
make test-all     # everything
make lint         # ruff lint
make migrate      # apply database migrations
```

The dev stack and the `lemma-stack` install stack run on different ports (3710/8710 vs 3711/8711), so both can coexist on the same machine.

---

## Next steps

- Read the [CLI overview](../lemma-cli/README.md) to build and operate pods.
- Read the [TypeScript SDK](../lemma-typescript/README.md) to build app frontends.
- Read the [Python SDK](../lemma-python/README.md) to write pod function code.
- Browse the [frontend docs](http://localhost:3711/docs) for the full platform documentation.
