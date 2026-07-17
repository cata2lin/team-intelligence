#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nomenclator_health.py — monitor de sănătate/regresie pt nomenclatoarele de adrese intl (CZ/PL/BG).

DE CE: bug-urile de validator (cursor-death, număr în address2) au fost prinse din NOROC, la verificare manuală.
Ăsta le prinde AUTOMAT: eșantionează comenzi REALE per țară din AWBprint (`shipping_address` JSON — sigur, NU
lovește xc.by_id care throttlează), rulează validatorul offline, raportează rescue-rate (valid+corrected), și
ALERTEAZĂ dacă rescue cade sub prag absolut SAU scade brusc față de snapshotul precedent, sau dacă `here_nogo` crește
anormal. Rulează zilnic pe VPS (cron). Exit code 1 dacă vreo alertă (cronul poate trimite mail pe non-zero).

Rulare: python3 nomenclator_health.py [--sample 300] [--json]
"""
import os, sys, json, argparse, subprocess, urllib.parse as up
from collections import Counter
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows/depozit cp1252-safe
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
if not os.path.exists(KB):
    KB = os.path.expanduser("~/team-intelligence/plugins/core/scripts/kb.py")
STATE = os.path.join(HERE, ".nomen_health.json")
NOGO_FILE = os.path.join(HERE, ".here_ro_nogo")

# țară -> magazin AWBprint + validatorul ei + prag absolut de rescue (floor) sub care e alertă
COUNTRIES = {
    "CZ": {"uid": "5a67aeb6-35fb-4fc7-b0b8-dccae38b31bf-1765442373-6TKLACMPIK",
           "mod": "cz_nomenclator", "fn": "cz_validate_and_correct", "floor": 0.86},
    "PL": {"uid": "dd18bcb8-b76b-455b-8378-237f283a88b8-1765442265-K3QTMXZLF0",
           "mod": "pl_nomenclator", "fn": "pl_validate_and_correct", "floor": 0.94},
    "BG": {"uid": "a75c9bbb-2eda-446e-841d-b4c7bd6603a0-1765442393-XVAO7LSYXB",
           "mod": "bg_nomenclator", "fn": "bg_validate_and_correct", "floor": 0.96},
}
DROP_ALERT = 0.08   # scădere de >8 puncte vs snapshotul precedent = alertă
NOGO_GROWTH = 0.30  # creștere >30% a here_nogo față de precedent = alertă


def get_secret(key):
    v = os.environ.get(key)
    if v:
        return v
    try:
        return subprocess.run(["python3", KB, "secret-get", key] if not _has_uv() else ["uv", "run", KB, "secret-get", key],
                              capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception:
        return ""
def _has_uv():
    from shutil import which
    return which("uv") is not None

def pg_conn(dsn):
    """Conexiune pg8000 (pur Python — disponibil pe VPS ca restul cronului; NU psycopg2). autocommit, doar SELECT."""
    import pg8000
    from urllib.parse import urlparse, unquote
    u = urlparse(dsn)
    last = None
    for use_ssl in (True, False):
        try:
            kw = dict(user=unquote(u.username or ""), password=unquote(u.password or ""),
                      host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"))
            if use_ssl:
                kw["ssl_context"] = True
            con = pg8000.connect(**kw); con.autocommit = True
            return con
        except Exception as e:
            last = e; continue
    raise RuntimeError("pg connect FAIL: %s" % last)

def nogo_count():
    try:
        with open(NOGO_FILE, encoding="utf-8", errors="replace") as f:
            txt = f.read().strip()
        try:
            return len(json.loads(txt))
        except Exception:
            return len([l for l in txt.splitlines() if l.strip()])
    except Exception:
        return None


def sample_country(cc, cfg, awb_cur, mcur, sample):
    import importlib
    mod = importlib.import_module(cfg["mod"]); fn = getattr(mod, cfg["fn"])
    awb_cur.execute("""SELECT shipping_address FROM orders
                       WHERE store_uid=%s AND shipping_address IS NOT NULL
                       ORDER BY synced_at DESC NULLS LAST LIMIT %s""", (cfg["uid"], sample))
    st = Counter(); n = 0
    for (sa,) in awb_cur.fetchall():
        a = sa if isinstance(sa, dict) else json.loads(sa)
        try:
            r = fn(mcur, a.get("city"), a.get("zip"), a.get("address1"), a.get("address2") or "")
        except Exception:
            r = {"status": "error"}
        st[r.get("status", "error")] += 1; n += 1
    resc = st["valid"] + st["corrected"]
    return {"n": n, "dist": dict(st), "rescue": (resc / n) if n else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    prev = {}
    if os.path.exists(STATE):
        try: prev = json.load(open(STATE))
        except Exception: prev = {}

    awb = pg_conn(get_secret("DATABASE_URL_AWBPRINT")); ac = awb.cursor()
    mc = pg_conn(get_secret("DATABASE_URL_METRICS")).cursor()

    out = {"countries": {}, "nogo": nogo_count(), "alerts": []}
    for cc, cfg in COUNTRIES.items():
        res = sample_country(cc, cfg, ac, mc, a.sample)
        out["countries"][cc] = res
        pr = (prev.get("countries") or {}).get(cc, {})
        if res["rescue"] < cfg["floor"]:
            out["alerts"].append("%s rescue %.1f%% < prag %.0f%%" % (cc, 100*res["rescue"], 100*cfg["floor"]))
        if pr.get("rescue") is not None and (pr["rescue"] - res["rescue"]) > DROP_ALERT:
            out["alerts"].append("%s rescue a SCĂZUT %.1f%%→%.1f%% (>%.0f pct)" %
                                 (cc, 100*pr["rescue"], 100*res["rescue"], 100*DROP_ALERT))
    pn = prev.get("nogo"); cn = out["nogo"]
    if pn and cn and pn > 0 and (cn - pn) / pn > NOGO_GROWTH:
        out["alerts"].append("here_nogo a crescut %d→%d (>%.0f%%)" % (pn, cn, 100*NOGO_GROWTH))

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("═" * 60)
        print("  HEALTH nomenclatoare adrese (eșantion %d comenzi reale/țară)" % a.sample)
        print("═" * 60)
        for cc, r in out["countries"].items():
            flag = "⚠️" if r["rescue"] < COUNTRIES[cc]["floor"] else "✅"
            print("  %s %s  rescue=%.1f%%  (n=%d)  %s" % (flag, cc, 100*r["rescue"], r["n"], r["dist"]))
        print("  here_nogo total: %s" % out["nogo"])
        if out["alerts"]:
            print("\n🚨 ALERTE:")
            for al in out["alerts"]:
                print("   - " + al)
        else:
            print("\n✅ fără regresii")

    try:
        json.dump(out, open(STATE, "w"), ensure_ascii=False)
    except Exception:
        pass
    awb.close()
    sys.exit(1 if out["alerts"] else 0)


if __name__ == "__main__":
    main()
