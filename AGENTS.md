# AGENTS.md

Guide for AI agents working in the **gogett-hub** repo. This file covers the
monorepo as a whole. Per-package guidance (e.g. SDK patterns) lives in that
package's own `AGENTS.md` — see [Sub-package docs](#sub-package-docs).

This doc is agent-agnostic: the project targets Claude Code, Codex, OpenCode,
Cursor, and any tool that can run a CLI. Read it once, then verify every
command against the [Makefile](Makefile) — commands below map 1:1 to targets
there.

## Repo layout

The repo is a single tree of independent packages. **No git submodules.**

| Path | Purpose | License |
|---|---|---|
| `lemma-backend/` | FastAPI backend, migrations, dev infra compose | AGPLv3 |
| `lemma-frontend/` | Next.js operator UI | AGPLv3 |
| `lemma-cli/` | `lemma-terminal` — the `lemma` CLI / TUI | Apache-2.0 |
| `lemma-python/` | `lemma-sdk` Python SDK | Apache-2.0 |
| `lemma-typescript/` | `lemma-sdk` TypeScript SDK (Node, browser, React) | Apache-2.0 |
| `agentbox/` | Sandboxed agent workspace manager + runtime image | Apache-2.0 |
| `agentbox-client/` | Python client for the AgentBox API | Apache-2.0 |
| `lemma-stack/` | `lemma-stack` — local stack installer / manager | Apache-2.0 |
| `lemma-pod-bundle/` | Stdlib pod assets | Apache-2.0 |
| `lemma-skills/` | Built-in agent skills | Apache-2.0 |
| `desktop/` | Tauri macOS desktop shell around `lemma-stack` | AGPLv3 |
| `docs/` | Installation + deploy guides | — |
| `Makefile` | Root developer workflow (the only command entry point) | — |
| `pyright.toml` | Shared Pyright baseline for every Python package | — |

See [README.md → Repo layout](README.md#repo-layout) for the full upstream
table. This file deliberately does not duplicate it.

## Prerequisites

- **uv** — Python package manager for every Python package.
  Install: <https://docs.astral.sh/uv/>
- **Node 24** — pinned via `.nvmrc` (`nvm use`).
- **Docker** *or* **Podman** — required for dev infra (postgres, redis,
  supertokens) and AgentBox.
- **Rust toolchain** — only needed for `desktop/` (clippy + rustfmt).
  Install: <https://rustup.rs/>

`make init` checks `uv`, `docker`/`podman`, and `node` up front and fails with
the exact install command if any are missing.

## Bootstrap

```bash
make init
```

Idempotent. Runs:

1. Prerequisite check (`uv`, `docker`/`podman`, `node`).
2. `uv sync` in every Python package and `npm install` in every TS package.
3. Builds `lemma-typescript/` (`npm run build`) so `lemma-frontend/` can
   import the SDK.
4. Creates `lemma-backend/.env` and `lemma-frontend/.env.local` with local
   defaults **only if absent**. Existing files get any missing keys appended.
5. Pulls the AgentBox runtime image (~2.9 GB, one-time) if not already
   present locally.

Re-run after pulling changes that touch `Makefile`, `pyproject.toml`,
`package.json`, or any of the `.env` keys listed in `_ensure-backend-env-keys`
/ `_ensure-frontend-env-keys`.

## Develop

```bash
make dev             # infra + backend + frontend + agentbox, hot-reload
make dev RELOAD=1    # same, with uvicorn --reload on the backend
```

Dev stack runs on its own ports (frontend `3710`, backend `8710`, agentbox
`8721`, postgres `5432`, redis `6379`, supertokens `3567`) so it does not
collide with an installed `lemma-stack` (3711/8711).

| URL | Service |
|---|---|
| <http://localhost:3710> | Frontend |
| <http://localhost:4173> | Auth UI |
| <http://localhost:8710> | API (`/scalar` for docs) |
| <http://127.0.0.1:8721> | AgentBox manager |

## Stop

```bash
make stop        # stop backend / frontend / agentbox dev processes
make stop-all    # also bring down infra (postgres, redis, supertokens) via docker compose
make logs        # tail backend compose logs (Ctrl-C to exit tail only)
```

`make stop` is the daily exit. Use `make stop-all` when you want a clean
slate (e.g. after changing infra env vars, before switching branches with
divergent migrations, or before a fresh `make init`).

For the full target list (coverage, per-component splits, opt-in e2e
suites, etc.) run:

```bash
make help
```

## Verify

Run before opening a PR. The umbrella targets fan out to every package —
prefer them over per-package invocations so nothing is missed.

```bash
make test           # every component's fast/offline suite
make lint           # ruff (Python) + eslint (frontend)
make format-check   # ruff format --check (Python) + prettier --check (TS/JS) + cargo fmt --check (desktop)
make typecheck      # pyright (Python) + tsc --noEmit (frontend + TypeScript SDK)
```

| Target | Tooling per language |
|---|---|
| `make test` | pytest (backend / cli / python SDK / agentbox / agentbox-client / stack / pod-bundle), vitest (frontend, TypeScript SDK), cargo test (desktop) |
| `make lint` | `ruff check` (7 Python packages), `npm run lint` (frontend); clippy only via `make lint-desktop` (skipped if cargo/clippy absent) |
| `make format-check` | `ruff format --check`, `prettier --check`, `cargo fmt --check` (gracefully skipped if cargo/rustfmt absent) |
| `make typecheck` | `uvx pyright` (8 Python targets), `tsc --noEmit -p tsconfig.json` + `tsc --noEmit -p tsconfig.test.json` (TypeScript SDK), `npm run typecheck` (frontend, which builds the SDK first) |

Component-split targets exist for fast loops, e.g. `make test-typescript`,
`make test-backend-unit`, `make test-cli-unit`, `make typecheck-frontend`,
`make format-check-desktop`. They are all listed by `make help`.

## Database

```bash
make migrate        # cd lemma-backend && uv run alembic upgrade head
```

`make dev` runs `make migrate` automatically after bringing infra up, so you
only need this on a fresh checkout or after pulling new migrations.

## Generated / vendored folders — do not edit

These are produced by tools, vendored from elsewhere, or are scratch for the
agent runtime. Add them to `.prettierignore` / `[tool.ruff.format]` /
`[tool.pyright]` excludes as needed rather than touching the files:

- `node_modules/` — npm install output (every TS package).
- `dist/` — `lemma-typescript` build output (committed **browser bundle**
  `public/lemma-client.js` is the exception; the rest is regenerated).
- `.venv/`, `__pycache__/`, `*.py[cod]`, `*.egg-info/` — uv / Python
  bytecode.
- `.next/`, `out/`, `build/` — Next.js + Tauri build output.
- `.ruff_cache/`, `.pytest_cache/`, `htmlcov/`, `coverage/`,
  `coverage-*/`, `*.coverage*`, `lcov.info`, `*.lcov` — lint / test caches
  and reports.
- `.dev-pids`, `*.dev-*.pid` — pid files written by `make dev` /
  `make stop`.
- `lemma-frontend/public/runtime-config.js`, `next-env.d.ts` —
  Next.js-generated; regenerated on every dev / build.
- `lemma-frontend/src/openapi_client/` — generated from the backend
  OpenAPI spec.
- `.gg/` — agent runtime scratch (custom agents, commands, skills). Not
  source. Do not commit unless explicitly intended.

## Safety notes

### Env files

`.env` and `.env.*` are gitignored (see `.gitignore`). Concretely:

- `lemma-backend/.env` — written by `make init`. Holds `DATABASE_URL`,
  `REDIS_URL`, `SUPERTOKENS_CORE_URL`, `AGENTBOX_API_KEY`,
  `LEMMA_OPENAI_API_KEY`, `LEMMA_ANTHROPIC_API_KEY`, etc. **Never commit
  it.** `make init` is idempotent: it skips creation when the file already
  exists and only appends keys that are missing.
- `lemma-frontend/.env.local` — written by `make init`. Holds
  `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SITE_URL`, `NEXT_PUBLIC_AUTH_URL`.
  Anything matching `NEXT_PUBLIC_*` is **embedded in the browser bundle** —
  never put server-only secrets there.
- `lemma-backend/.env.example`, `lemma-frontend/.env.example` (if present)
  are the **only** env-shaped files that should be tracked. Add new keys
  to the example first, then run `make init` to populate the real file.

If a key is missing locally, prefer `make init` over hand-editing — it
keeps the canonical ports at the top of the Makefile and the env file in
sync.

### Lockfiles

Lockfiles are tracked and represent the resolved dependency graph for the
whole team:

- `uv.lock` per Python package (`lemma-backend/`, `lemma-cli/`,
  `lemma-python/`, `agentbox/`, `agentbox-client/`, `lemma-stack/`,
  `lemma-pod-bundle/`, plus `lemma-backend/lemma-connectors/`).
- `package-lock.json` for `lemma-typescript/` and `lemma-frontend/`.
- `Cargo.lock` under `desktop/`.

`make init` uses `uv sync --quiet` and `npm install --silent`, which
respect the lockfile. **Do not run `uv lock --upgrade` or
`npm update` casually** — if you intentionally bump a dependency, commit
the lockfile in the same change and call it out in the PR. If a tool
auto-suggests an upgrade, leave the suggestion for the human reviewer.

### Destructive actions

`make stop-all` removes docker-compose containers (data in named volumes is
preserved, but ephemeral containers are not). `rm -rf .venv`, `rm -rf
node_modules`, and force-pushes require explicit confirmation — the
human reviewer owns those, not the agent.

## Sub-package docs

Before working in a specific package, read its README and any nested
`AGENTS.md`:

- `lemma-typescript/AGENTS.md` — SDK hook patterns, registry block index,
  code style for the TypeScript SDK. **Do not modify this file from the
  monorepo agent doc.** Its scope is intentionally per-package.
- `lemma-backend/README.md` — backend layout, env keys, alembic workflow.
- `lemma-frontend/README.md` — frontend layout, Next.js conventions.
- `lemma-cli/`, `lemma-python/`, `agentbox/`, `agentbox-client/`,
  `lemma-stack/`, `lemma-pod-bundle/`, `lemma-skills/`, `desktop/` —
  per-package READMEs cover the rest.
- `docs/installation.md` — full end-user install + provider config
  (`LEMMA_DEFAULT_MODEL_TYPE`, `LEMMA_OPENAI_*`, `LEMMA_ANTHROPIC_*`,
  `COMPOSIO_API_KEY`).
- `docs/gogett-deploy.md` — the gogett-webrnds.com Cloudflare Tunnel
  deployment used by this fork.
