#!/usr/bin/env bash
set -euo pipefail
SEARCHTOOLS_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SEARCHTOOLS_REPO"
git pull --ff-only
exec ./scripts/install.sh --skip-models "$@"
