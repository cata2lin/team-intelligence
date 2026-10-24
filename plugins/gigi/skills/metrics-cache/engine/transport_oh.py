"""Transportul REAL per comandă din Order Hub, pentru motorul de profit — pasul S1 din planul de consolidare, în UMBRĂ.

De ce (22-oct-2026): motorul lua transportul real din AWBprint (`orders.transport_cost`), care n-are valori din 28-aug,
deci septembrie–octombrie mergeau pe tariful flat (13 lei brut / 1,21 = 10,74 ex-TVA pe toate magazinele). Profitul
pe octombrie ieșea cu ~93k mai mare decât real (CZ +67k, Duppo BG +15k, Grandia +14k). Order Hub are costul pe fiecare
etichetă (`shipments.cost_transport`, ex-TVA: factura DPD, altfel prețul curierului la creare) și îl dă pe comandă prin
`GET /api/cs/cost-transport`.

UMBRĂ: `get_report` calculează transportul și cu OH, scrie comparația pe (lună, prefix) în `profit_transport_umbra`
(SQLite, motorul) și ÎNTOARCE în continuare cifra veche. Comutarea = `TRANSPORT_SURSA=oh` în mediu, după ce o săptămână
de comparație a fost citită de owner (cifrele văzute de oameni nu se schimbă fără OK).

Deploy: engine/transport_oh.py → /root/Scripturi/core/transport_oh.py (lângă greu.py); importat ca `core.transport_oh`.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import urllib.parse
import urllib.request
from calendar import monthrange
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("profitability.transport_oh")

OH_BAZA = os.environ.get("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")
KB = "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
UV = "/root/.local/bin/uv"
PLECAT = ("Livrata", "In curs de livrare", "Refuzata")
_token_cache: Optional[str] = None

DDL = """
CREATE TABLE IF NOT EXISTS profit_transport_umbra (
    month TEXT NOT NULL,
    prefix TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    plecate INTEGER NOT NULL,
    cu_cost_oh INTEGER NOT NULL,
    t_engine REAL NOT NULL,
    t_oh REAL NOT NULL,
    anulate_facturate REAL NOT NULL DEFAULT 0,
    sursa_activa TEXT NOT NULL,
    PRIMARY KEY (month, prefix)
)"""


def token() -> Optional[str]:
    """Cheia de citire a OH (`cs.citire`): din mediu, altfel din seif (KB), o dată pe proces."""
    global _token_cache
    if _token_cache:
        return _token_cache
    t = (os.environ.get("OH_PROFIT_CITIRE_TOKEN") or os.environ.get("OH_CS_SB_TOKEN") or "").strip()
    if not t:
        try:
            t = subprocess.run([UV, "run", KB, "secret-get", "OH_CS_SB_TOKEN"], capture_output=True, text=True,
                               timeout=60).stdout.strip()
        except Exception as e:                                   # fără seif: umbra rămâne goală, motorul merge
            log.warning("transport OH: fără cheie (%s)", e)
            t = ""
    _token_cache = t or None
    return _token_cache


def sursa_activa() -> str:
    """`awbprint` (implicit: cascada de azi) sau `oh` (comutat). Doar din mediu, ca să fie o decizie, nu un drift."""
    return "oh" if (os.environ.get("TRANSPORT_SURSA") or "").strip().lower() == "oh" else "awbprint"


def _interval(luna: str) -> Tuple[str, str]:
    y, m = (int(x) for x in luna.split("-"))
    return "%04d-%02d-01" % (y, m), "%04d-%02d-%02d" % (y, m, monthrange(y, m)[1])


def pe_comenzi(month_list: List[str]) -> Dict[str, Tuple[float, float]]:
    """{order_name: (cost_transport ex-TVA al etichetelor vii, cost etichete anulate dar facturate)} pentru lunile cerute
    (zile de București, ca în OH). Un apel pe lună, o cerere; orice eroare = {} (umbra goală, motorul neatins)."""
    t = token()
    if not t:
        return {}
    out: Dict[str, Tuple[float, float]] = {}
    for luna in month_list:
        de_la, pana_la = _interval(luna)
        url = "%s/api/cs/cost-transport?%s" % (OH_BAZA, urllib.parse.urlencode({"de_la": de_la, "pana_la": pana_la}))
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + t, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log.warning("transport OH %s: %s", luna, str(e)[:160])
            continue
        for x in d.get("randuri") or []:
            c = x.get("cost_transport")
            a = float(x.get("anulate_facturate") or 0)
            if c is None and not a:
                continue
            out[str(x.get("comanda") or "").strip()] = (float(c or 0), a)
        log.info("transport OH %s: %d comenzi cu cost", luna, len(d.get("randuri") or []))
    return out


def transport_oh_prefix(prefix_orders: List[dict], oh: Dict[str, Tuple[float, float]],
                        flat_exvat: float,
                        real_pe_comanda: Optional[Callable[[str], Optional[float]]] = None) -> Tuple[float, int, int, float]:
    """(t_oh ex-TVA, plecate, cu_cost_oh, anulate_facturate) pe un prefix: costul OH al comenzii plecate; unde OH nu-l
    are, costul REAL de dinainte (AWBprint, `real_pe_comanda`), abia apoi flat-ul (aceeași plasă ca motorul); plus
    etichetele anulate dar ridicate și facturate (OH le leagă de comandă).

    De ce cascada (22-oct-2026, prima comutare): OH are costul doar pe etichetele pe care le-a făcut el — mai 2026 0 din
    56.144 de comenzi plecate, iunie 174, iulie 18.813 din 71.591 (august încolo ~98%). Cu plasa doar pe flat, lunile
    vechi pierdeau transportul real din AWBprint (mai: −36,7k lei față de raportul de dinainte), deși comutarea trebuia
    să aducă transport REAL, nu să-l scoată."""
    t = anul = 0.0
    plecate = cu = 0
    for o in prefix_orders:
        if o.get("status_category") not in PLECAT:
            continue
        plecate += 1
        nume = str(o.get("order_name") or "").strip()
        c, a = oh.get(nume, (None, 0.0))
        if c is not None and c > 0:
            cu += 1
            t += c
        else:
            r = real_pe_comanda(nume) if real_pe_comanda else None
            t += r if (r and r > 0) else flat_exvat
        anul += a or 0.0
    return t, plecate, cu, anul


def scrie_umbra(db_path: str, month_key: str, prefix: str, plecate: int, cu_cost_oh: int,
                t_engine: float, t_oh: float, anulate: float) -> None:
    """Un rând pe (lună, prefix), rescris la fiecare raport. Eroarea nu oprește raportul."""
    try:
        con = sqlite3.connect(db_path, timeout=30)
        with con:
            con.execute(DDL)
            con.execute("INSERT OR REPLACE INTO profit_transport_umbra VALUES (?,?,?,?,?,?,?,?,?)",
                        (month_key, prefix, datetime.now(timezone.utc).isoformat(timespec="seconds"), plecate,
                         cu_cost_oh, round(t_engine, 2), round(t_oh, 2), round(anulate, 2), sursa_activa()))
        con.close()
    except Exception as e:
        log.warning("transport umbra %s/%s: %s", month_key, prefix, str(e)[:120])
