# Lemma CLI Setup Guide

`lemma-terminal` is the command-line and terminal UI app for Lemma. It talks to
either Lemma Cloud or a local Lemma stack through named servers.

## Install

Keep a **single global install** so `lemma` always resolves to one version. Use
`uv tool` (not a project venv or `pip install` into an arbitrary environment) —
that is what puts `lemma` on your PATH once:

```bash
uv tool install lemma-terminal
```

For local development from this repository (editable, picks up source changes):

```bash
uv tool install --force --editable lemma-cli
```

> **After the SDK schema changes** (regenerating `lemma-python`), re-run the
> `--force` install so the bundled `lemma-sdk` is rebuilt. `lemma doctor` flags
> when the installed SDK has drifted from the server it is talking to — the exact
> skew that once shipped a stale message model under an unchanged version.

Check the install, versions, and health:

```bash
lemma --help
lemma --version          # CLI + SDK + API schema versions
lemma doctor             # client/server skew + duplicate-install check
lemma servers list
```

## Cloud Setup

Lemma Cloud is the default server:

- API: `https://api.lemma.work`
- Auth: `https://lemma.work/auth`

Create or refresh the cloud server:

```bash
lemma servers cloud --use
lemma auth login
```

List organizations you can access:

```bash
lemma orgs list
```

Select defaults for commands that work inside a pod:

```bash
lemma orgs select --save-default
lemma pods list
lemma pods select --save-default
```

Most pod workflows then use the selected org and pod automatically:

```bash
lemma agents list
lemma files list /pod
lemma tables list
lemma chat
```

Use `--json` when an agent or script needs raw structured output:

```bash
lemma --json pods list
```

## Servers

Servers are independent CLI states. Each server stores its API URL, auth URL,
token, and default org/pod/conversation values.

```bash
lemma servers list
lemma servers show
lemma servers select cloud
lemma servers create staging --base-url https://api.example.com --auth-url https://example.com/auth
```

## Environment Variables

Environment variables continue to work for humans, scripts, and agents:

- `LEMMA_SERVER`: active server name.
- `LEMMA_BASE_URL`: API URL override.
- `LEMMA_AUTH_URL`: auth URL override.
- `LEMMA_TOKEN`: bearer token override.
- `LEMMA_ORG_ID`: org override.
- `LEMMA_POD_ID`: pod override.
- `LEMMA_CONVERSATION_ID`: conversation override.

Command-line flags take precedence over environment variables.

## Terminal UI

Open the TUI:

```bash
lemma tui
```

The TUI shows the active server, org, pod, and agent. It includes resource
views for servers, organizations, pods, and pod-scoped resources. You can switch
server/org/pod from the resource views or with chat slash commands:

```text
/server cloud
/org <org-id-or-slug>
/pod <pod-id-or-slug>
/refresh
/quit
```

`Ctrl-C` and `q` exit the TUI.

## Local Stack Setup

Installing and managing a local Lemma stack is handled by the separate
`lemma-stack` tool, not the CLI. Install and start it with:

```bash
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash
```

`lemma-stack install` registers the stack as the CLI server named `local`
(API `http://localhost:8711`, auth `http://localhost:3711/auth`), so afterwards:

```bash
lemma servers select local
lemma auth login
```

Manage the stack with `lemma-stack start|stop|status|logs|config|uninstall`.
See `lemma-stack --help` and the `lemma-stack/` package for details.

## Common Workflow

Cloud:

```bash
lemma servers cloud --use
lemma auth login
lemma orgs select --save-default
lemma pods select --save-default
lemma tui
```

Local (after `lemma-stack install`):

```bash
lemma servers select local
lemma auth login
lemma orgs select --save-default
lemma pods select --save-default
lemma tui
```
