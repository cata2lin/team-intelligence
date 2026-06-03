# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Launch an npx-based MCP server cross-platform (macOS + Windows).

Usage: npx_mcp_launch.py <package@version> [server args...]

Resolves npx and runs it, inheriting stdio so the MCP stdio transport stays
intact -- true exec on POSIX, a child process on Windows (where npx is a .cmd
that cannot be exec'd). This avoids the Windows-only `cmd /c npx` wrapper that
would otherwise be needed, so one config works on every OS.
"""
import os
import shutil
import subprocess
import sys


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: npx_mcp_launch.py <package> [args...]\n")
        sys.exit(2)
    npx = shutil.which("npx")
    if not npx:
        sys.stderr.write("[npx_mcp_launch] npx not found on PATH; install Node.js.\n")
        sys.exit(127)
    argv = [npx, "-y"] + sys.argv[1:]
    try:
        if os.name == "posix":
            os.execvp(npx, argv)
        else:
            sys.exit(subprocess.run(argv).returncode)
    except OSError as exc:
        sys.stderr.write(f"[npx_mcp_launch] failed to launch npx: {exc}\n")
        sys.exit(127)


if __name__ == "__main__":
    main()
