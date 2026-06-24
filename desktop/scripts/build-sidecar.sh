#!/usr/bin/env bash
# Build the compiled lemma-supervisor sidecar (PyInstaller, single file) from
# the lemma-stack package. The sidecar is self-contained: it runs
# `lemma-stack supervise`, which pulls the released images and brings the
# stack up — no runtime checkout or download needed.
#
# Output: desktop/binaries/lemma-supervisor-<target-triple>, which
# tauri.dist.conf.json picks up via externalBin.
set -euo pipefail

cd "$(dirname "$0")/../.."

TRIPLE="${LEMMA_SIDECAR_TRIPLE:-aarch64-apple-darwin}"
OUT_DIR="desktop/binaries"
WORK_DIR="$(mktemp -d /tmp/lemma-sidecar.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

mkdir -p "$OUT_DIR"
# Build inside lemma-stack's environment so its deps (typer/rich/tomlkit) and
# package data are discoverable.
( cd lemma-stack && uv run --with pyinstaller pyinstaller \
    --onefile --noconfirm \
    --name lemma-supervisor \
    --collect-data lemma_stack \
    --distpath "$OLDPWD/$OUT_DIR" \
    --workpath "$WORK_DIR/build" \
    --specpath "$WORK_DIR" \
    lemma_stack/sidecar_main.py )

mv "$OUT_DIR/lemma-supervisor" "$OUT_DIR/lemma-supervisor-$TRIPLE"
echo "sidecar: $OUT_DIR/lemma-supervisor-$TRIPLE"
"$OUT_DIR/lemma-supervisor-$TRIPLE" --help >/dev/null && echo "sidecar: smoke ok"
