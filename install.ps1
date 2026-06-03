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
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git not found - install git first." }
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
  Write-Warning "Node.js/npx not found - needed for the Postgres MCP servers. Install Node.js (https://nodejs.org), then re-run."
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  Write-Host "    (note: 'claude' CLI not on PATH - fine; plugins are enabled via settings.json, which Claude Code reads on start.)"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "    installing uv (Python tooling)..."
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$HOME\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv was installed but isn't on PATH yet. Open a NEW terminal, then re-run ./install.ps1"
}

# Hand off to the cross-platform interactive walkthrough.
uv run --no-project (Join-Path $Repo "onboarding\onboard.py") @args
