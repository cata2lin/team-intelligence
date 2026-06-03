<#
  Arona intelligence center - one-command onboarding (Windows / PowerShell).

    ./install.ps1 -Employee catalin -NasRoot "Z:\" `
                  -KbUrl "postgresql://scraper:****@38.242.226.83/SharedClaude"

  Re-runnable: safe to run again to update/repair an install.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)] [string] $Employee,
  [Parameter(Mandatory = $true)] [string] $NasRoot,
  [string] $KbUrl = $env:KB_DATABASE_URL,                  # may be supplied via env instead
  [string] $Repo = "https://github.com/cata2lin/team-intelligence.git",
  [string] $TeamRepo = (Join-Path $HOME "team-intelligence"),
  [string] $Marketplace = "cata2lin/team-intelligence",    # owner/repo on GitHub
  [string[]] $Plugins = @("core", "iulian", "gigi", "adriana", "andreea", "anne", "catalin")
)
$ErrorActionPreference = "Stop"

if (-not $KbUrl) { throw "Provide -KbUrl (or set `$env:KB_DATABASE_URL) — the SharedClaude connection string." }
if ($Repo -match 'YOUR_ORG' -or $Marketplace -match 'YOUR_ORG') {
  throw "Edit YOUR_ORG in install.ps1 (or pass -Repo and -Marketplace) before running."
}
if ($NasRoot -match '^[A-Za-z]:$') { $NasRoot = "$NasRoot\" }   # 'Z:' -> 'Z:\' (avoid drive-relative paths)

Write-Host "==> Checking prerequisites (git, node/npx, uv)"
foreach ($cmd in @("git", "npx")) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd not found - install it first." }
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "    installing uv..."
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$HOME\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv installed but not on PATH in this session. Open a NEW terminal so uv is available, then re-run."
}

Write-Host "==> Cloning / updating the team repo at $TeamRepo"
if (Test-Path (Join-Path $TeamRepo ".git")) {
  git -C $TeamRepo pull --ff-only
} else {
  git clone $Repo $TeamRepo
}
if ($LASTEXITCODE -ne 0) { throw "git clone/pull failed ($LASTEXITCODE)" }

Write-Host "==> Installing Python deps for local dev (uv sync; runtime scripts use inline deps)"
Push-Location $TeamRepo
try { uv sync } catch { Write-Host "    (uv sync skipped - runtime scripts carry their own inline deps)" }
Pop-Location

Write-Host "==> Configuring KB_DATABASE_URL + EMPLOYEE_HANDLE + NAS_ROOT + global CLAUDE.md @import"
uv run --no-project (Join-Path $TeamRepo "onboarding\configure.py") `
  --kb-url $KbUrl --employee $Employee --nas-root $NasRoot --team-repo $TeamRepo

Write-Host "==> Registering the marketplace and enabling everyone's plugins"
try { claude plugin marketplace add $Marketplace } catch { claude plugin marketplace update ($Marketplace.Split('/')[-1]) }
foreach ($p in $Plugins) {
  try { claude plugin install "$p@team-intelligence" --scope user }
  catch {
    try { claude plugin enable "$p@team-intelligence" --scope user }
    catch { Write-Warning "could not install/enable $p@team-intelligence - run 'claude plugin install $p@team-intelligence' manually." }
  }
}

Write-Host "==> Done. RESTART Claude Code so it picks up the MCP servers + hooks + skills."
Write-Host "    Knowledge base: SharedClaude (via KB_DATABASE_URL).  Files: $NasRoot"
