#!/usr/bin/env bash
# Arona intelligence center — onboarding bootstrap (macOS / Linux).
#
# After you `git clone` the repo, just run:
#     ./install.sh
# It installs prerequisites, then starts the interactive walkthrough (pick who
# you are, enter the database host/user/password/name). Everything else — the
# skills, MCP servers, secrets, and global Claude config — is set up for you.
#
# Re-runnable. Pass extra flags through to the walkthrough if you want
# (e.g. ./install.sh --employee iulian --db-host ... --non-interactive).
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"

echo "==> Arona onboarding (macOS / Linux)"
command -v git >/dev/null || { echo "git not found — install git first."; exit 1; }
command -v npx >/dev/null || echo "! Node.js/npx not found — needed for the Postgres MCP servers. Install Node.js (https://nodejs.org), then re-run."
command -v claude >/dev/null || echo "  (note: 'claude' CLI not on PATH — that's fine; plugins are enabled via settings.json, which Claude Code reads on start.)"

if ! command -v uv >/dev/null; then
  echo "    installing uv (Python tooling)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null || {
  echo "uv was installed but isn't on PATH yet. Open a NEW terminal (or 'source ~/.profile'), then re-run ./install.sh"
  exit 1
}

# Hand off to the cross-platform interactive walkthrough.
exec uv run --no-project "$REPO/onboarding/onboard.py" "$@"
