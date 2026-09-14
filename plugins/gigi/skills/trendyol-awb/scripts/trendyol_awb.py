# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko>=3.4"]
# ///
"""trendyol_awb.py — descarcă etichetele AWB Trendyol (comenzile de expediat) ca UN PDF pe Mac.

Trendyol e separat de xConnector/AWBprint: comenzile lui + etichetele stau în app-ul web
scripts.arona.ro (`api/trendyol_awb.py`, DB `data/trendyol_orders.db`). Butonul „Descarcă AWB Trendyol"
din dashboard = ruta `/api/trendyol-awb/download`. Skill-ul ăsta face EXACT ce face butonul, dar din CLI:
rulează logica IN-PROCES pe VPS (import `api.trendyol_awb`, ocolind auth-ul dashboard-ului) și trage PDF-ul local.

  trendyol_awb.py list                      # ce comenzi sunt de descărcat (Picking/Created/Invoiced, nedescărcate)
  trendyol_awb.py download [--dir DIR]      # descarcă + îmbină PDF + marchează downloaded + trage pe Mac (implicit ~/Downloads)

⚠️ `download` MARCHEAZĂ comenzile `downloaded=1` (ies din coadă) — exact ca butonul. `list` nu atinge nimic.
"""
import argparse, io, json, os, subprocess, sys


def _kb_path():
    here = os.path.dirname(os.path.abspath(__file__))
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
    # apel direct (fara `/bin/zsh -lc`): statiile depozitului sunt pe Windows, unde zsh nu exista
    return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


# core/scripts in orice layout de instalare (clona repo, marketplace, plugin-cache
# core/<commit>/scripts). GARDA: iteram parents (fara index fix => fara IndexError) si
# preferam core-ul din ACELASI commit ca skill-ul.
def _core_scripts(need="arona_ssh.py"):
    from pathlib import Path
    h = Path(__file__).resolve()
    c = [Path(os.environ["ARONA_CORE_SCRIPTS"])] if os.environ.get("ARONA_CORE_SCRIPTS") else []
    for up in h.parents:
        c += [up / "core" / "scripts", up / "plugins" / "core" / "scripts"] + \
             (sorted((up / "core").glob("*/scripts")) if (up / "core").is_dir() else [])
    ok = [x for x in c if (x / need).exists()]
    return next((x for x in ok if x.parent.name in h.parts), ok[0] if ok else None)


def _ssh_connect():
    """Helper SSH PARTAJAT (core/scripts/arona_ssh.py): CHEIE intai, apoi ssh-agent, parola
    doar ca ultim resort — VPS-ul accepta doar `publickey`."""
    cs = _core_scripts()
    if cs is None:
        sys.exit("core/scripts/arona_ssh.py negasit — actualizeaza plugin-urile echipei "
                 "sau seteaza ARONA_CORE_SCRIPTS=/cale/spre/plugins/core/scripts")
    if str(cs) not in sys.path:
        sys.path.insert(0, str(cs))
    import arona_ssh
    try:
        return arona_ssh.connect()
    except arona_ssh.SSHAuthError as e:
        sys.exit(str(e))


def _ssh():
    return _ssh_connect()


def _run_remote_py(code, timeout=300):
    """Rulează cod Python IN-PROCES pe VPS (venv-ul app-ului), cu env-ul app-ului. Întoarce stdout."""
    c = _ssh()
    cmd = "cd /root/Scripturi && (/root/Scripturi/.venv/bin/python - || python3 -)"
    stdin, out, err = c.exec_command(cmd, timeout=timeout)
    stdin.write(code); stdin.channel.shutdown_write()
    o = out.read().decode(errors="replace"); e = err.read().decode(errors="replace")
    c.close()
    return o, e


# Cod remote care încarcă coada pending și (opțional) descarcă. Emite o linie JSON: RESULT={...}
REMOTE = r'''
import json, asyncio, shutil, os, core.config
import api.trendyol_awb as t
MODE = "%MODE%"
pkgs = t._load_packages_from_db()
pend = [p for p in pkgs if not p.get("downloaded") and p.get("status") in t._OPEN_STATUSES]
if MODE == "list":
    out = [{"order": p.get("orderNumber"), "pkg": p.get("packageId"), "ctn": p.get("cargoTrackingNumber"),
            "status": p.get("status"), "brand": p.get("brand"), "qty": p.get("totalQty")} for p in pend]
    print("RESULT=" + json.dumps({"ok": True, "pending": len(pend), "orders": out}))
else:
    if not pend:
        print("RESULT=" + json.dumps({"ok": True, "pending": 0, "note": "coada goala"})); raise SystemExit
    res = asyncio.run(t.trendyol_awb_download({"packages": pend}))
    if res.get("ok"):
        src = t.LABELS_DIR / res["filename"]
        shutil.copy(str(src), "/tmp/trendyol_awb_latest.pdf")
        res["size"] = os.path.getsize("/tmp/trendyol_awb_latest.pdf")
    print("RESULT=" + json.dumps(res, default=str))
'''


def _parse(o):
    for line in o.splitlines():
        if line.startswith("RESULT="):
            return json.loads(line[len("RESULT="):])
    return None


def cmd_list(a):
    o, e = _run_remote_py(REMOTE.replace("%MODE%", "list"))
    r = _parse(o)
    if not r:
        sys.exit("nu am putut citi coada:\n" + (e or o)[-500:])
    print(f"📦 Trendyol — {r['pending']} comenzi de descărcat (Picking/Created/Invoiced, nedescărcate):")
    for x in r["orders"]:
        print(f"  #{x['order']}  {x['brand'] or '?':<16} ctn={x['ctn']}  {x['status']}  qty={x.get('qty')}")
    if not r["orders"]:
        print("  (nimic — toate etichetele sunt deja descărcate)")


def cmd_download(a):
    dest_dir = os.path.expanduser(a.dir)
    os.makedirs(dest_dir, exist_ok=True)
    print("descarc etichetele Trendyol (in-proces pe VPS)…")
    o, e = _run_remote_py(REMOTE.replace("%MODE%", "download"))
    r = _parse(o)
    if not r:
        sys.exit("descărcare eșuată:\n" + (e or o)[-800:])
    if not r.get("ok"):
        sys.exit("Trendyol a răspuns: " + str(r.get("error")))
    if r.get("pending") == 0:
        print("✓ Coada e goală — nimic de descărcat."); return
    # trage PDF-ul prin SFTP
    fn = r["filename"]
    local = os.path.join(dest_dir, fn)
    c = _ssh()
    sftp = c.open_sftp(); sftp.get("/tmp/trendyol_awb_latest.pdf", local); sftp.close(); c.close()
    print(f"✓ {r['count']} etichete îmbinate → {local}")
    if r.get("skipped"):
        print(f"  ({r['skipped']} sărite — deja expediate: {', '.join(r.get('skippedDetails', [])[:5])})")
    if r.get("errors"):
        print(f"  ⚠️ erori: {r['errors'][:3]}")


def main():
    p = argparse.ArgumentParser(description="descarcă etichetele AWB Trendyol ca PDF")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    d = sub.add_parser("download"); d.add_argument("--dir", default="~/Downloads"); d.set_defaults(func=cmd_download)
    a = p.parse_args(); a.func(a)


if __name__ == "__main__":
    main()
