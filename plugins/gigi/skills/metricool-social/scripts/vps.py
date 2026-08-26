# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko>=3.4"]
# ///
"""VPS helper: run commands / put files over SSH.

Autentificarea sta in helper-ul PARTAJAT core/scripts/arona_ssh.py: CHEIE intai
(PROFIT_SSH_KEY / ~/.ssh/id_*), apoi ssh-agent, parola doar ca ultim resort — VPS-ul de profit
accepta doar `publickey`. Niciun secret nu se printeaza.
"""
import sys, os
from pathlib import Path


# core/scripts in orice layout de instalare (clona repo, marketplace, plugin-cache
# core/<commit>/scripts). GARDA: iteram parents, fara index fix si fara cale hardcodata
# spre Downloads (calea veche exista doar pe un singur Mac).
def _core_scripts(need="arona_ssh.py"):
    h = Path(__file__).resolve()
    c = [Path(os.environ["ARONA_CORE_SCRIPTS"])] if os.environ.get("ARONA_CORE_SCRIPTS") else []
    for up in h.parents:
        c += [up / "core" / "scripts", up / "plugins" / "core" / "scripts"] + \
             (sorted((up / "core").glob("*/scripts")) if (up / "core").is_dir() else [])
    ok = [x for x in c if (x / need).exists()]
    return next((x for x in ok if x.parent.name in h.parts), ok[0] if ok else None)


_CS = _core_scripts()
if _CS is None:
    sys.exit("core/scripts/arona_ssh.py negasit — actualizeaza plugin-urile echipei "
             "sau seteaza ARONA_CORE_SCRIPTS=/cale/spre/plugins/core/scripts")
sys.path.insert(0, str(_CS))
import arona_ssh


def client():
    try:
        return arona_ssh.connect()
    except arona_ssh.SSHAuthError as e:
        sys.exit(str(e))
def run(cmd):
    c=client(); _,out,err=c.exec_command(cmd,timeout=120)
    o=out.read().decode(); e=err.read().decode(); c.close()
    return o,e
def put(local,remote):
    c=client(); sf=c.open_sftp(); sf.put(local,remote); sf.close(); c.close()
def get(remote,local):
    c=client(); sf=c.open_sftp(); sf.get(remote,local); sf.close(); c.close()
if __name__=="__main__":
    if sys.argv[1]=="run":
        o,e=run(sys.argv[2]); print(o); print(e,file=sys.stderr)
    elif sys.argv[1]=="put":
        put(sys.argv[2],sys.argv[3]); print("put ok",sys.argv[3])
    elif sys.argv[1]=="get":
        get(sys.argv[2],sys.argv[3]); print("get ok",sys.argv[3])
