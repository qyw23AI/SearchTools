#!/usr/bin/env bash
set -euo pipefail
RESEARCH_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$RESEARCH_REPO/scripts/build_plugin.py" --install-profile "${ZOTERO_PROFILE:-$HOME/.zotero/zotero/default}" "$@"
