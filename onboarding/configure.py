# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Cross-platform Claude Code configuration for the Arona intelligence center.

Idempotent. Does the two JSON/text edits that the shell installers shouldn't:
  1. Sets the per-machine env in ~/.claude/settings.json -> "env" (so the values
     reach MCP servers, hooks, and scripts in every session):
       - KB_DATABASE_URL  : the SharedClaude knowledge-base connection (bootstrap secret)
       - EMPLOYEE_HANDLE  : who this machine belongs to (iulian, catalin, ...)
       - NAS_ROOT         : this machine's NAS mount (file storage)
       - TEAM_REPO        : the local clone path
  2. Ensures the global ~/.claude/CLAUDE.md @import-s the team rules from the
     local clone, so they stay current automatically.

Writes are atomic (temp file + os.replace) so an interruption can't corrupt the
user's primary config.

Usage:
    configure.py --kb-url "<SharedClaude conn string>" --employee "<handle>" \
                 --nas-root "<path>" [--team-repo "<path>"]
"""
import argparse
import json
import os
import re
import tempfile


def claude_home() -> str:
    home = os.path.join(os.path.expanduser("~"), ".claude")
    os.makedirs(home, exist_ok=True)
    return home


def _atomic_write(path: str, text: str) -> None:
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def set_env_vars(settings_path: str, env_vars: dict) -> None:
    data = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            data = {}
    data.setdefault("env", {})
    data["env"].update(env_vars)
    _atomic_write(settings_path, json.dumps(data, indent=2) + "\n")


def set_plugins(settings_path, repo, market_name, plugins, auto_update=True):
    """Enable the team marketplace + every plugin at USER scope (all projects),
    by writing settings.json directly -- works whether or not the `claude` CLI
    is on PATH. Claude Code picks these up on start."""
    data = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            data = {}
    data.setdefault("extraKnownMarketplaces", {})
    data["extraKnownMarketplaces"][market_name] = {
        "source": {"source": "github", "repo": repo},
        "autoUpdate": auto_update,
    }
    data.setdefault("enabledPlugins", {})
    for p in plugins:
        data["enabledPlugins"][f"{p}@{market_name}"] = True
    _atomic_write(settings_path, json.dumps(data, indent=2) + "\n")


def ensure_import(claude_md_path: str, import_line: str) -> None:
    existing = ""
    if os.path.exists(claude_md_path):
        with open(claude_md_path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    if import_line in existing:
        return
    block = (
        "\n<!-- Arona team-intelligence: shared rules, auto-updated. Do not edit below. -->\n"
        + import_line
        + "\n"
    )
    _atomic_write(claude_md_path, (existing + block) if existing else block.lstrip("\n"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb-url", required=True, help="SharedClaude knowledge-base connection string")
    ap.add_argument("--employee", required=True, help="this machine's employee handle (iulian, catalin, ...)")
    ap.add_argument("--nas-root", required=True, help="mount path of the team NAS share")
    ap.add_argument(
        "--team-repo",
        default=os.path.join(os.path.expanduser("~"), "team-intelligence"),
        help="local clone of the team-intelligence repo",
    )
    args = ap.parse_args()

    nas = args.nas_root
    if re.match(r"^[A-Za-z]:$", nas):
        nas += os.sep  # 'Z:' -> 'Z:\' so os.path.join stays rooted, not drive-relative

    home = claude_home()
    settings_path = os.path.join(home, "settings.json")
    claude_md_path = os.path.join(home, "CLAUDE.md")

    set_env_vars(settings_path, {
        "KB_DATABASE_URL": args.kb_url,
        "EMPLOYEE_HANDLE": args.employee.lower().strip(),
        "NAS_ROOT": nas,
        "TEAM_REPO": args.team_repo,
    })

    # Forward slashes: stable @import across OSes and a stable idempotency check.
    shared = os.path.join(args.team_repo, "shared", "CLAUDE.team.md").replace(os.sep, "/")
    ensure_import(claude_md_path, f"@{shared}")

    print(f"[configure] NAS_ROOT + TEAM_REPO written to {settings_path}")
    print(f"[configure] @import ensured in {claude_md_path} -> {shared}")


if __name__ == "__main__":
    main()
