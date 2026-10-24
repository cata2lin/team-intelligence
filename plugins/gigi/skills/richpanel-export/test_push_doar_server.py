# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg[binary]"]
# ///
"""`rp.py push` nu deschide AWBprint de pe laptop (ruta OH cu email bidx lipsește). Fără rețea.
  uv run test_push_doar_server.py"""
import os, socket, subprocess, sys

AICI = os.path.dirname(os.path.abspath(__file__))
esec = []
def check(n, ok, info=""):
    print(("  ok   " if ok else "  FAIL ") + n + ("" if ok else "  " + str(info)[:200]))
    if not ok: esec.append(n)

env = {k: v for k, v in os.environ.items() if k != "ARONA_PE_SERVER"}
env["DATABASE_URL_AWBPRINT"] = "postgresql://nu:parola_test@127.0.0.1:1/x"
rp = os.path.join(AICI, "rp.py")
if socket.gethostname() != "vmi2650854.contaboserver.net":
    r = subprocess.run([sys.executable, rp, "push", "--order", "EST000001"], capture_output=True, text=True, env=env)
    check("laptop: push iese cu 2 și „ruta OH lipsește”, fără conectare", r.returncode == 2
          and "ruta OH lipsește" in r.stderr and "OperationalError" not in r.stderr, r.stderr)
env["ARONA_PE_SERVER"] = "1"
r = subprocess.run([sys.executable, rp, "push", "--order", "EST000001"], capture_output=True, text=True, env=env)
check("server: încearcă AWBprint, iar eroarea nu scoate DSN-ul", "conectarea la AWBprint a eșuat" in r.stderr
      and "parola_test" not in (r.stderr + r.stdout), r.stderr)
print("TOATE TREC" if not esec else "EȘUATE: %d" % len(esec)); sys.exit(1 if esec else 0)
