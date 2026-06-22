<#
  Arona intelligence center - onboarding bootstrap (Windows / PowerShell).

  After you `git clone` the repo, just run:
      ./install.ps1
  It installs prerequisites, then starts the interactive walkthrough (pick who
  you are, enter the database host/user/password/name). Everything else - the
  skills, MCP servers, secrets, and global Claude config - is set up for you.

  Re-runnable. Pass extra flags through to the walkthrough if you want
  (e.g. ./install.ps1 --employee iulian --db-host ... --non-interactive).
#>
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "==> Arona onboarding (Windows)"

# --- Prerequisites: auto-install whatever is missing, with no manual steps. ---
function Test-Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
$HaveWinget = Test-Have winget

function Install-Tool($cmd, $wingetId, $label) {
  if (Test-Have $cmd) { return }
  if ($HaveWinget) {
    Write-Host "    installing $label ..."
    winget install --id $wingetId -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
  } else {
    Write-Warning "$label not found and winget is unavailable. Install $label, then re-run ./install.ps1"
  }
}

# git (you already cloned the repo, so this is just a safety net)
Install-Tool git "Git.Git" "Git"
if (-not (Test-Have git)) { throw "git not found - install git first." }

# uv - the Python runner every skill uses (official installer is most reliable)
if (-not (Test-Have uv)) {
  Write-Host "    installing uv (Python tooling)..."
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$HOME\.local\bin;$env:Path"
}

# Node.js  -> the Postgres + chrome-devtools MCP servers and the node-based skills
Install-Tool node "OpenJS.NodeJS.LTS" "Node.js"
# GitHub CLI -> gigi:publish-skill (one-command skill publishing)
Install-Tool gh "GitHub.cli" "GitHub CLI"

# Refresh PATH so freshly-installed tools resolve in THIS session.
$env:Path = ([System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
             [System.Environment]::GetEnvironmentVariable("Path","User") + ";$HOME\.local\bin")

if (-not (Test-Have uv)) {
  throw "uv was installed but isn't on PATH yet. Open a NEW terminal, then re-run ./install.ps1"
}
if (-not (Test-Have node)) {
  Write-Warning "Node.js isn't on PATH yet - the Postgres/chrome MCP servers and node skills stay offline until you open a new terminal and re-run."
}
if (-not (Test-Have claude)) {
  Write-Host "    (note: 'claude' CLI not on PATH - fine; plugins are enabled via settings.json, which Claude Code reads on start.)"
}

# Hand off to the cross-platform interactive walkthrough.
uv run --no-project (Join-Path $Repo "onboarding\onboard.py") @args
