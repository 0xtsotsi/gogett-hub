# Podman Runtime Staging

This directory is a staging target for the macOS arm64 Podman runtime used by
the desktop packaging flow. The actual runtime binaries are intentionally
ignored by git.

Stage a runtime before building a distributable desktop app:

```bash
cd desktop
PODMAN_BUNDLE_ROOT=/path/to/podman-runtime npm run stage:podman
```

The current tested source is the official Podman 5.8.2 macOS arm64 installer.
