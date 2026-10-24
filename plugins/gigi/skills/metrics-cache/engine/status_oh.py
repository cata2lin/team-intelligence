"""Statusul (livrat/refuzat) per comandă din Order Hub, pentru motorul de profit — pasul S1 din planul de
consolidare, în UMBRĂ. Pereche cu core/transport_oh.py (scripturi-12).

De ce (22-oct-2026): motorul lua livrat/refuzat din `profit_orders.status_category` (tracking AWBprint +
Shopify), care rămâne în urmă/îngheață (intl din 27-sep; comenzile EST restaurate azi sunt „In curs"/
„Netrimisa" fără tracking re-rulat). Order Hub derivă starea din COLETE (`GET /api/cs/livrabilitate`:
livrat dacă oricare colet e livrat; refuzat dacă are colet refuzat și niciunul livrat) — mai proaspăt și
mai complet. Măsurat iul-oct: OH are +417k lei livrate peste engine (din care EST +254k, ~98% octombrie).

UMBRĂ: `compara_umbra` scrie comparația pe (lună, prefix) în `profit_status_umbra` (SQLite, motorul) și NU
schimbă nimic din ce întoarce motorul. Comutarea = `profit_settings.status_sursa='oh'` (comutator în BAZĂ,
nu în env — principiul 10 din arhitectura-țintă), după ce comparația e citită de owner. Cascada veche rămâne plasă.
"""
import json, os, sqlite3, subprocess, urllib.parse, urllib.request
from calendar import monthrange
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

OH_BAZA = os.environ.get("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")
KB = "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
UV = "/root/.local/bin/uv"
LIV, REFZ, INCURS = "Livrata", "Refuzata", "In curs de livrare"
_token_cache: Optional[str] = None

DDL = """CREATE TABLE IF NOT EXISTS profit_status_umbra (
  month TEXT, prefix TEXT, computed_at TEXT,
  liv_engine INTEGER, refz_engine INTEGER, liv_oh INTEGER, refz_oh INTEGER,
  incurs_engine INTEGER, incurs_oh INTEGER, matched INTEGER, unmatched INTEGER,
  contrib_liv_engine REAL, contrib_liv_oh REAL, delta_lei REAL,
  sursa_activa TEXT, PRIMARY KEY (month, prefix))"""


def token() -> Optional[str]:
    """Cheia de citire OH (`cs.citire`): din mediu, altfel din seif (KB), o dată pe proces."""
    global _token_cache
    if _token_cache:
        return _token_cache
    t = (os.environ.get("OH_PROFIT_CITIRE_TOKEN") or os.environ.get("OH_CS_SB_TOKEN") or "").strip()
    if not t:
        try:
            t = subprocess.run([UV, "run", KB, "secret-get", "OH_CS_SB_TOKEN"],
                               capture_output=True, text=True, timeout=60).stdout.strip()
        except Exception:
            t = ""
    _token_cache = t or None
    return _token_cache


def sursa_activa(conn: sqlite3.Connection) -> str:
    """`awbprint` (implicit: cascada de azi) sau `oh` (comutat). Comutatorul stă în BAZĂ (profit_settings),
    nu în env — ca să fie o decizie, nu un drift (principiul 10)."""
    try:
        row = conn.execute("SELECT value FROM profit_settings WHERE key='status_sursa'").fetchone()
        return "oh" if (row and str(row[0]).strip().lower() == "oh") else "awbprint"
    except sqlite3.Error:
        return "awbprint"


def pe_comenzi(month: str) -> Dict[str, Tuple[str, Optional[float], str]]:
    """{order_name: (stare, transport_fara_tva, ultim_status)} din OH pentru o lună (zile București).
    Orice eroare = {} (umbra goală, motorul neatins)."""
    t = token()
    if not t:
        return {}
    url = "%s/api/cs/livrabilitate?%s" % (OH_BAZA, urllib.parse.urlencode({"luna": month}))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + t, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}
    ci = {c: i for i, c in enumerate(d.get("coloane") or [])}
    out: Dict[str, Tuple[str, Optional[float], str]] = {}
    for row in d.get("randuri") or []:
        name = str(row[ci["comanda"]]).strip()
        tr = row[ci["transport_fara_tva"]]
        try:
            tr = float(tr) if tr not in (None, "None", "") else None
        except Exception:
            tr = None
        out[name] = (row[ci["stare"]], tr, row[ci["ultim_status"]] or "")
    return out


def compara_umbra(conn: sqlite3.Connection, months: List[str], vat_for_prefix, rate_to_ron,
                  flat_transport_exvat: float = 10.74) -> int:
    """Scrie comparația vechi(engine)↔nou(OH) pe (lună, prefix) în profit_status_umbra. Întoarce nr. rânduri.
    Delta lei = Σ(venit_exTVA − COGS − transport) pe livratele OH − pe livratele engine, DOAR pe comenzile
    comune (marketingul e fix pe brand → se anulează). READ-ONLY pe engine; scrie DOAR în umbra."""
    conn.execute(DDL)
    activa = sursa_activa(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for month in months:
        oh = pe_comenzi(month)
        if not oh:
            continue
        stat: Dict[str, dict] = {}
        for pfx, name, rev, curc, cogs, sc in conn.execute(
                "SELECT prefix, order_name, revenue, currency, cogs, status_category "
                "FROM profit_orders WHERE month=?", (month,)):
            o = oh.get(str(name).strip())
            s = stat.setdefault(pfx, dict(le=0, re=0, ie=0, lo=0, ro=0, io=0, m=0, u=0, ce=0.0, co=0.0))
            if not o:
                s["u"] += 1
                continue
            s["m"] += 1
            transp = o[1] if o[1] is not None else flat_transport_exvat
            contrib = float(rev or 0) * rate_to_ron(month, curc) / (1.0 + vat_for_prefix(pfx)) - float(cogs or 0) - transp
            if sc == LIV: s["le"] += 1; s["ce"] += contrib
            elif sc == REFZ: s["re"] += 1
            elif sc == INCURS: s["ie"] += 1
            if o[0] == LIV: s["lo"] += 1; s["co"] += contrib
            elif o[0] == REFZ: s["ro"] += 1
            elif o[0] == INCURS: s["io"] += 1
        for pfx, s in stat.items():
            conn.execute(
                "INSERT OR REPLACE INTO profit_status_umbra (month,prefix,computed_at,liv_engine,refz_engine,"
                "liv_oh,refz_oh,incurs_engine,incurs_oh,matched,unmatched,contrib_liv_engine,contrib_liv_oh,"
                "delta_lei,sursa_activa) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (month, pfx, now, s["le"], s["re"], s["lo"], s["ro"], s["ie"], s["io"], s["m"], s["u"],
                 round(s["ce"], 2), round(s["co"], 2), round(s["co"] - s["ce"], 2), activa))
            n += 1
    conn.commit()
    return n


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/root/Scripturi")
    import profit_core as pc
    db = os.environ.get("PROFITABILITY_DB", "/root/Scripturi/data/profitability.db")
    cx = sqlite3.connect(db)
    _rates = {}
    try:
        for m, cur, r in cx.execute("SELECT month, currency, rate_to_ron FROM profit_exchange_rates"):
            _rates[(m, (cur or "").upper())] = float(r or 0)
    except sqlite3.Error:
        pass

    def rate(month, cur):
        cur = (cur or "RON").upper()
        return 1.0 if cur == "RON" else (_rates.get((month, cur)) or _rates.get((month[:7], cur)) or 1.0)

    months = sys.argv[1:] or ["2026-07", "2026-08", "2026-09", "2026-10"]
    k = compara_umbra(cx, months, pc.vat_for_prefix, rate)
    print("profit_status_umbra: %d rânduri scrise (sursa_activa=%s)" % (k, sursa_activa(cx)))
    cx.close()
