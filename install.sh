#!/usr/bin/env bash
# Arona intelligence center — one-command onboarding (macOS / Linux).
#
#   ./install.sh --employee catalin --nas-root "/Volumes/team" \
#                --kb-url "postgresql://scraper:****@38.242.226.83/SharedClaude"
#
# Re-runnable: safe to run again to update/repair an install.
set -euo pipefail

EMPLOYEE=""
NAS_ROOT=""
KB_URL="${KB_DATABASE_URL:-}"                       # may be supplied via env instead of flag
REPO_URL="https://github.com/cata2lin/team-intelligence.git"
TEAM_REPO="$HOME/team-intelligence"
MARKETPLACE="cata2lin/team-intelligence"            # owner/repo on GitHub
PLUGINS=("core" "iulian" "gigi" "adriana" "andreea" "anne" "catalin")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --employee) EMPLOYEE="$2"; shift 2;;
    --nas-root) NAS_ROOT="$2"; shift 2;;
    --kb-url) KB_URL="$2"; shift 2;;
    --repo) REPO_URL="$2"; shift 2;;
    --team-repo) TEAM_REPO="$2"; shift 2;;
    --marketplace) MARKETPLACE="$2"; shift 2;;
    *) echo "unknown arg: $1"; exit 2;;
  esac
done

[[ -z "$EMPLOYEE" ]] && { echo "ERROR: --employee is required (iulian|gigi|adriana|andreea|anne|catalin)"; exit 2; }
[[ -z "$NAS_ROOT" ]] && { echo "ERROR: --nas-root is required (the NAS mount path)"; exit 2; }
[[ -z "$KB_URL"   ]] && { echo "ERROR: --kb-url (or \$KB_DATABASE_URL) is required (SharedClaude connection)"; exit 2; }
case "$REPO_URL$MARKETPLACE" in
  *YOUR_ORG*) echo "ERROR: edit YOUR_ORG in install.sh (or pass --repo and --marketplace) before running."; exit 2;;
esac

echo "==> Checking prerequisites (git, node/npx, uv)"
command -v git >/dev/null || { echo "git not found — install git first"; exit 1; }
command -v npx >/dev/null || { echo "npx (Node.js) not found — install Node.js first"; exit 1; }
if ! command -v uv >/dev/null; then
  echo "    installing uv…"; curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null || {
  echo "uv was installed to \$HOME/.local/bin but is not on PATH yet."
  echo "Open a NEW terminal (or 'source ~/.profile'), then re-run this script."
  exit 1
}

echo "==> Cloning / updating the team repo at $TEAM_REPO"
if [[ -d "$TEAM_REPO/.git" ]]; then
  git -C "$TEAM_REPO" pull --ff-only
else
  git clone "$REPO_URL" "$TEAM_REPO"
fi

echo "==> Installing Python deps for local dev (uv sync; runtime scripts use inline deps)"
( cd "$TEAM_REPO" && uv sync ) || echo "    (uv sync skipped — runtime scripts carry their own inline deps)"

echo "==> Configuring KB_DATABASE_URL + EMPLOYEE_HANDLE + NAS_ROOT + global CLAUDE.md @import"
uv run --no-project "$TEAM_REPO/onboarding/configure.py" \
  --kb-url "$KB_URL" --employee "$EMPLOYEE" --nas-root "$NAS_ROOT" --team-repo "$TEAM_REPO"

echo "==> Registering the marketplace and enabling everyone's plugins"
claude plugin marketplace add "$MARKETPLACE" || claude plugin marketplace update "$(basename "$MARKETPLACE")"
for p in "${PLUGINS[@]}"; do
  claude plugin install "$p@team-intelligence" --scope user \
    || claude plugin enable "$p@team-intelligence" --scope user \
    || echo "WARN: could not install/enable $p@team-intelligence — run 'claude plugin install $p@team-intelligence' manually."
done

echo "==> Done. RESTART Claude Code so it picks up the MCP servers + hooks + skills."
echo "    Knowledge base: SharedClaude (via KB_DATABASE_URL).  Files: $NAS_ROOT"
