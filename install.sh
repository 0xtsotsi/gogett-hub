#!/usr/bin/env bash
# Lemma local installer bootstrap.
#
#   curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash
#
# Installs uv (if missing), installs lemma-stack as a uv tool, and hands off
# to `lemma-stack install`, which detects/installs a container runtime
# (podman recommended), pulls the released images, and starts the stack at
# ~/.lemma/local. Pass arguments through:
#
#   ./install.sh --runtime podman -y
set -Eeuo pipefail

say() { printf '%s\n' "$*"; }
fail() {
  say "error: $*" >&2
  exit 1
}

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (https://astral.sh/uv)…"
  command -v curl >/dev/null 2>&1 || fail "curl is required; install curl and re-run"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv installed but not on PATH; open a new shell and re-run"
fi

# LEMMA_STACK_SOURCE lets developers bootstrap from a local checkout:
#   LEMMA_STACK_SOURCE=$PWD/lemma-stack ./install.sh -y
# When unset, install from the git repo (the package is not on PyPI yet).
LEMMA_STACK_SPEC="${LEMMA_STACK_SOURCE:-git+https://github.com/lemma-work/lemma-platform.git#subdirectory=lemma-stack}"

say "Installing lemma-stack…"
uv tool install --force "$LEMMA_STACK_SPEC" >/dev/null
command -v lemma-stack >/dev/null 2>&1 || export PATH="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin"):$PATH"
command -v lemma-stack >/dev/null 2>&1 || fail "lemma-stack installed but not on PATH; run: uv tool update-shell"

exec lemma-stack install "$@"
