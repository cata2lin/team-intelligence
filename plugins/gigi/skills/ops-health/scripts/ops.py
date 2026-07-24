# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko>=3.4"]
# ///
"""ops.py — rulează uneltele de monitorizare/operare de pe VPS prin SSH (parola din KB, niciodată printată).

Împachetează suprafața construită 2026-07 (data_health, reconcile_sources, deploy_parity, deploy.sh,
backup_profitdb) ca UN punct de intrare discoverabil. Tot ce rulează e read-only SAU sigur (dry-run
implicit la deploy). Vezi SKILL.md.

  ops.py health                 # prospețime date + heartbeat cronuri (data_health.py)
  ops.py reconcile [--months N] # divergențe engine↔AWBprint↔warehouse (reconcile_sources.py)
  ops.py parity                 # cod git(origin/main) ↔ fișiere flat VPS (deploy_parity check)
  ops.py deploy [--apply]       # deploy git-driven (dry-run fără --apply)
  ops.py backup                 # backup consistent profitability.db acum
  ops.py cron                   # lista cronurilor + prospețimea logurilor
"""
import os, sys, subprocess


def _kb_path():
    """kb.py din structura de plugin (relativ la acest script), cu fallback pe marketplace."""
    here = os.path.dirname(os.path.abspath(__file__))
    # scripts/ -> ops-health -> skills -> gigi -> plugins -> core/scripts/kb.py
    cand = os.path.normpath(os.path.join(here, "..", "..", "..", "..", "core", "scripts", "kb.py"))
    if os.path.exists(cand):
        return cand
    for p in (os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
              "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py"):
        if os.path.exists(p):
            return p
    sys.exit("kb.py negăsit (nu pot lua credențialele SSH)")


KB = _kb_path()


def sec(k):
    return subprocess.run(["/bin/zsh", "-lc", f"uv run '{KB}' secret-get {k}"],
                          capture_output=True, text=True).stdout.strip()


def run_vps(cmd, timeout=600):
    import paramiko
    host, user, pw = sec("PROFIT_SSH_HOST"), sec("PROFIT_SSH_USER"), sec("PROFIT_SSH_PASS")
    if not (host and user and pw):
        sys.exit("lipsesc PROFIT_SSH_HOST/USER/PASS din KB")
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=pw, timeout=25)
    chan = c.get_transport().open_session(); chan.settimeout(timeout)
    chan.exec_command(cmd)
    # stream stdout+stderr împreună
    buf = b""
    while True:
        if chan.recv_ready():
            data = chan.recv(4096); buf += data; sys.stdout.write(data.decode("utf-8", "replace")); sys.stdout.flush()
        elif chan.recv_stderr_ready():
            data = chan.recv_stderr(4096); sys.stdout.write(data.decode("utf-8", "replace")); sys.stdout.flush()
        elif chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
            break
    rc = chan.recv_exit_status(); c.close()
    return rc


ENVPFX = "cd /root/Scripturi && set -a && . .env 2>/dev/null; . /root/ad-spend/run.env 2>/dev/null; set +a && "
PY = "/root/Scripturi/.venv/bin/python"

CMDS = {
    "health":    ENVPFX + f"{PY} data_health.py",
    "reconcile": ENVPFX + f"{PY} reconcile_sources.py",
    "parity":    ENVPFX + f"{PY} deploy_parity.py check --all",
    "deploy":    "bash /root/Scripturi/deploy.sh",
    "backup":    ENVPFX + f"{PY} backup_profitdb.py",
    "cron":      "crontab -l 2>/dev/null | grep -vE '^#|^$'",
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        sys.exit("uz: ops.py {%s} [args]" % "|".join(CMDS))
    cmd = CMDS[sys.argv[1]]
    extra = sys.argv[2:]
    if sys.argv[1] == "reconcile" and "--months" in extra:
        i = extra.index("--months"); cmd += f" --months {extra[i+1]}"
    if sys.argv[1] == "deploy" and "--apply" in extra:
        cmd += " --apply"
    sys.exit(run_vps(cmd))


if __name__ == "__main__":
    main()
