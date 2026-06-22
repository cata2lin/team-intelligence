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

have() { command -v "$1" >/dev/null 2>&1; }

# Pick an available package manager for auto-install (brew on macOS; apt/dnf on Linux).
PM=""
if   have brew;    then PM="brew"
elif have apt-get; then PM="apt"
elif have dnf;     then PM="dnf"
fi

# ensure <cmd> <brew_pkg> <linux_pkg> <label> — install the tool if it's missing.
ensure() {
  have "$1" && return 0
  local cmd="$1" brewpkg="$2" linpkg="$3" label="$4"
  case "$PM" in
    brew) echo "    installing $label…"; brew install $brewpkg || true ;;
    apt)  echo "    installing $label…"; sudo apt-get update -y && sudo apt-get install -y $linpkg || true ;;
    dnf)  echo "    installing $label…"; sudo dnf install -y $linpkg || true ;;
    *)    echo "! $label not found and no brew/apt/dnf available — install $label manually, then re-run." ;;
  esac
}

ensure git git git "Git"
have git || { echo "git not found — install git first."; exit 1; }

# uv — the Python runner every skill uses (official installer is most reliable).
if ! have uv; then
  echo "    installing uv (Python tooling)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Node.js → the Postgres + chrome-devtools MCP servers and node-based skills.
ensure node node "nodejs npm" "Node.js"
# GitHub CLI → gigi:publish-skill (brew/dnf have it; on bare apt it may need GitHub's repo).
ensure gh gh gh "GitHub CLI"

export PATH="$HOME/.local/bin:$PATH"
have uv  || { echo "uv installed but not on PATH yet. Open a NEW terminal (or 'source ~/.profile'), then re-run ./install.sh"; exit 1; }
have npx || echo "! Node.js/npx still missing — the Postgres/chrome MCP servers and node skills stay offline until it's installed."
have claude || echo "  (note: 'claude' CLI not on PATH — that's fine; plugins are enabled via settings.json, which Claude Code reads on start.)"

# Hand off to the cross-platform interactive walkthrough.
exec uv run --no-project "$REPO/onboarding/onboard.py" "$@"
