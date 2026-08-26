"""
arona_ssh.py — the ONE shared SSH helper for team skills (VPS-ul de profitabilitate).

Fratele lui `arona_pg.py`: acolo stau conexiunile Postgres + secretele, aici sta SINGURA
implementare de SSH. Inainte, 19 fisiere aveau fiecare propriul `_vps_run`/`_ssh`/`run_vps`
copiat, toate cu `connect(host, username=user, password=pwd)` — iar VPS-ul accepta de mult
DOAR `publickey`. Pe masinile cu `~/.ssh/id_ed25519` mergea din ACCIDENT (paramiko cauta
implicit chei inainte sa incerce parola); pe orice statie fara cheie ramanea doar parola
si toate skill-urile cadeau cu `BadAuthenticationType: allowed types: ['publickey']`.

Ordinea de autentificare de aici e explicita: CHEIE -> ssh-agent -> parola (ultimul resort).

Drop-in din orice skill (bootstrap-ul de gasire a lui core/scripts e in docstring-ul lui
arona_pg.py — acolo e reteta canonica, cu garda pe parents):

    import arona_ssh
    p = arona_ssh.vps_run("hostname")          # .stdout / .stderr / .returncode
    cl = arona_ssh.connect()                   # paramiko.SSHClient (pentru sftp etc.)

Config (env-first, apoi KB — niciodata printat):
    PROFIT_SSH_HOST   (default 84.46.242.181)
    PROFIT_SSH_USER   (default root)
    PROFIT_SSH_KEY    cale catre cheia privata; accepta si mai multe, separate prin ':'
                      (alias acceptat: ARONA_SSH_KEY)
    PROFIT_SSH_PASS   parola — DOAR ca ultim resort, si doar daca serverul o accepta

Reguli de proiectare:
- Lipsa parolei NU mai e fatala. Fatal e doar "nicio metoda n-a mers", si atunci mesajul
  spune CE s-a incercat si CE accepta serverul — nu un traceback paramiko brut.
- Nu se printeaza niciodata valoarea unui secret (nici parola, nici continutul cheii).
"""
import os
from pathlib import Path
from types import SimpleNamespace

DEFAULT_HOST = "84.46.242.181"
DEFAULT_USER = "root"
# cheile implicite, in ordinea in care le cauta si OpenSSH
DEFAULT_KEYS = ("id_ed25519", "id_ecdsa", "id_rsa")


class SSHAuthError(RuntimeError):
    """Autentificarea SSH a esuat pe toate metodele. Mesajul e ACTIONABIL (ce s-a incercat)."""


def _secret(key: str) -> str:
    """Env-first, apoi KB (prin arona_pg, care e langa noi). Nu printeaza valoarea."""
    v = os.environ.get(key)
    if v:
        return v.strip()
    try:
        import sys
        d = str(Path(__file__).resolve().parent)
        if d not in sys.path:
            sys.path.insert(0, d)
        import arona_pg
        return arona_pg.secret(key)
    except Exception:
        return ""


def key_candidates():
    """Chei private de incercat, in ordine: cele din env, apoi ~/.ssh/id_*. Doar cele care exista."""
    out = []
    for var in ("PROFIT_SSH_KEY", "ARONA_SSH_KEY"):
        raw = os.environ.get(var) or ""
        for part in raw.split(":"):
            p = part.strip()
            if p and Path(p).expanduser().is_file():
                out.append(str(Path(p).expanduser()))
    home = Path(os.environ.get("HOME") or Path.home())
    for name in DEFAULT_KEYS:
        p = home / ".ssh" / name
        if p.is_file():
            out.append(str(p))
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p); uniq.append(p)
    return uniq


def _agent_keys():
    try:
        import paramiko
        return list(paramiko.Agent().get_keys())
    except Exception:
        return []


def _short(e):
    return "%s: %s" % (type(e).__name__, str(e)[:160])


def connect(host=None, user=None, timeout=30):
    """Deschide o sesiune SSH. CHEIE intai (env + ~/.ssh + ssh-agent), parola ultimul resort.
    Ridica SSHAuthError cu un mesaj care spune ce s-a incercat si ce accepta serverul."""
    import paramiko

    host = host or _secret("PROFIT_SSH_HOST") or DEFAULT_HOST
    user = user or _secret("PROFIT_SSH_USER") or DEFAULT_USER
    keys = key_candidates()
    agent = _agent_keys()
    tried, allowed = [], None

    cl = paramiko.SSHClient()
    cl.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 1) chei (explicite din env + implicite din ~/.ssh) + ssh-agent, intr-o singura runda
    if keys or agent:
        try:
            cl.connect(host, username=user, key_filename=keys or None,
                       look_for_keys=True, allow_agent=True, timeout=timeout)
            return cl
        except paramiko.PasswordRequiredException as e:
            tried.append("chei [%s]: protejate cu passphrase — ruleaza `ssh-add <cheie>` "
                         "sau seteaza PROFIT_SSH_KEY spre o cheie fara passphrase (%s)"
                         % (", ".join(keys) or "ssh-agent", _short(e)))
        except Exception as e:
            allowed = allowed or getattr(e, "allowed_types", None)
            tried.append("chei [%s] + ssh-agent (%d identitati) -> %s"
                         % (", ".join(keys) or "niciuna", len(agent), _short(e)))
    else:
        tried.append("chei: niciuna gasita (PROFIT_SSH_KEY nesetat, ~/.ssh/{%s} lipsesc) "
                     "si ssh-agent fara identitati" % "|".join(DEFAULT_KEYS))

    # 2) parola — ultimul resort; pe VPS-ul de profit serverul o refuza din start
    pwd = _secret("PROFIT_SSH_PASS")
    if pwd:
        try:
            cl.connect(host, username=user, password=pwd,
                       look_for_keys=False, allow_agent=False, timeout=timeout)
            return cl
        except Exception as e:
            allowed = allowed or getattr(e, "allowed_types", None)
            tried.append("parola (PROFIT_SSH_PASS) -> %s" % _short(e))
    else:
        tried.append("parola: PROFIT_SSH_PASS nesetat (env/KB)")

    msg = ["SSH %s@%s a esuat. Metode incercate, in ordine:" % (user, host)]
    msg += ["  %d. %s" % (i + 1, t) for i, t in enumerate(tried)]
    if allowed:
        msg.append("Serverul accepta DOAR: %s." % list(allowed))
    msg.append("Fix: adauga cheia ta publica in ~%s/.ssh/authorized_keys pe %s, "
               "sau seteaza PROFIT_SSH_KEY=/cale/spre/cheia_privata." % (user, host))
    raise SSHAuthError("\n".join(msg))


def vps_run(remote_cmd, timeout=180, host=None, user=None, connect_timeout=30):
    """Ruleaza o comanda pe VPS. Intoarce SimpleNamespace(stdout, stderr, returncode) —
    aceeasi forma pe care o avea `_vps_run`-ul copiat in fiecare skill."""
    cl = connect(host=host, user=user, timeout=connect_timeout)
    try:
        _i, _o, _e = cl.exec_command(remote_cmd, timeout=timeout)
        out = _o.read().decode("utf-8", "replace")
        err = _e.read().decode("utf-8", "replace")
        rc = _o.channel.recv_exit_status()
    finally:
        cl.close()
    return SimpleNamespace(stdout=out, stderr=err, returncode=rc)


def put_text(remote_path, text, host=None, user=None):
    """Scrie un fisier text pe VPS (SFTP). Folosit de skill-urile care trimit un script remote."""
    cl = connect(host=host, user=user)
    try:
        sftp = cl.open_sftp()
        with sftp.open(remote_path, "w") as f:
            f.write(text)
        sftp.close()
    finally:
        cl.close()
