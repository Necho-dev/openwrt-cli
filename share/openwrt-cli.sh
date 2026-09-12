#!/usr/bin/env bash
# One-liner entry: curl -fsSL https://raw.githubusercontent.com/Necho-dev/openwrt-cli/main/share/openwrt-cli.sh | bash
# From a clone this execs ../install.sh; when piped it fetches install.sh once.

set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/Necho-dev/openwrt-cli/main/install.sh"

src="${BASH_SOURCE[0]:-}"
if [ -n "$src" ] && [ -f "$src" ] && [ -r "$src" ]; then
  root="$(cd "$(dirname "$src")/.." && pwd)"
  if [ -f "${root}/install.sh" ]; then
    exec bash "${root}/install.sh" "$@"
  fi
fi

payload="$(curl -fsSL "$REPO_RAW")" || {
  echo "failed to download ${REPO_RAW}" >&2
  exit 1
}
exec bash -c "$payload" -- "$@"
