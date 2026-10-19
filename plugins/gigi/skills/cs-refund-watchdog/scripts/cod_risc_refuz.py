"""Citește coada modelului de refuz COD de pe serverul de scripturi (rulează ACOLO, trimis pe stdin).

Doar citire: data/cod_refusal_queue.csv (scorată zilnic 06:40 de cod_refusal_run.sh score),
data mtime pt cod_refusal_model.txt și ultimul AUC din logs/cod_refusal.log. Nu scrie nimic.
Args: --comanda X | --top N, opțional --magazin <prefix comandă / fragment store_uid>.
"""
import argparse, csv, datetime, os, re, sys

BASE = "/root/Scripturi"
Q = os.path.join(BASE, "data", "cod_refusal_queue.csv")
M = os.path.join(BASE, "data", "cod_refusal_model.txt")
LOG = os.path.join(BASE, "logs", "cod_refusal.log")

ap = argparse.ArgumentParser()
ap.add_argument("--comanda", default="")
ap.add_argument("--top", type=int, default=0)
ap.add_argument("--magazin", default="")
a = ap.parse_args()


def _mt(p):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "lipsă"


def _auc():
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            hits = [l.strip() for l in f if l.lstrip().startswith("AUC =")]
        return hits[-2:]
    except OSError:
        return []


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _motive(r):
    """Context din coloanele cozii (modelul nu salvează contribuțiile pe feature)."""
    m = []
    po, pr = int(_f(r["cust_prior_orders"])), int(_f(r["cust_prior_refused"]))
    if pr:
        m.append("client cu %d refuzuri din %d comenzi anterioare (telefon)" % (pr, po))
    elif po:
        m.append("client cu %d comenzi anterioare, 0 refuzuri" % po)
    else:
        m.append("client nou (fără istoric pe telefon)")
    v = _f(r["total_price"])
    if v >= 300:
        m.append("valoare mare: %.2f %s" % (v, r["currency"]))
    return "; ".join(m)


def _prefix(o):
    mm = re.match(r"[A-Za-z]+", o or "")
    return mm.group(0).upper() if mm else ""


rows = []
with open(Q, newline="", encoding="utf-8", errors="replace") as f:
    for r in csv.DictReader(f):
        r["_risk"] = _f(r.get("risk"))
        rows.append(r)

# aceeași comandă apare pe 2 store_uid (sursa xConnector + shopify-*): păstrez scorul maxim
best = {}
for r in rows:
    k = r["order_number"]
    if k not in best or r["_risk"] > best[k]["_risk"]:
        best[k] = r
uniq = list(best.values())

if a.magazin:
    mg = a.magazin.strip().lower()
    uniq = [r for r in uniq if _prefix(r["order_number"]).lower() == mg or mg in (r["store_uid"] or "").lower()]

print("Modelul de refuz COD (LightGBM, VPS) — coadă scorată %s, model antrenat %s, %d comenzi unice (%d rânduri)."
      % (_mt(Q), _mt(M), len(best), len(rows)))
for l in _auc():
    print("  holdout:", l)
print("Status: coada = comenzi ÎN AȘTEPTARE în AWBprint la momentul scorării (waiting_for_courier/fulfilled/"
      "not_fulfilled/on_hold…), NU statusul de acum. Înainte de apel → oh_comanda (Order Hub = sursa de adevăr).")
print("Motive = context din istoricul clientului (modelul nu salvează contribuțiile pe feature).\n")


def _line(i, r):
    return ("%s%-12s risc %5.1f%%  %8.2f %-3s  %s, %s  %s  %s\n      motive: %s"
            % ("%3d. " % i if i else "", r["order_number"], r["_risk"] * 100, _f(r["total_price"]), r["currency"],
               r["city"], r["province"], r["cname"], r["phone"], _motive(r)))


if a.comanda:
    c = a.comanda.strip().upper().lstrip("#")
    hit = [r for r in rows if (r["order_number"] or "").upper().lstrip("#") == c]
    if not hit:
        print("Comanda %s NU e în coada de azi (nu e COD în așteptare la 06:40, sau e deja plecată/anulată/plătită)." % c)
        sys.exit(0)
    hit.sort(key=lambda r: -r["_risk"])
    print(_line(0, hit[0]))
    if len(hit) > 1:
        print("  (apare de %d ori, pe store_uid: %s)" % (len(hit), ", ".join(r["store_uid"][:40] for r in hit)))
    rank = sorted(best.values(), key=lambda r: -r["_risk"])
    pos = next(i for i, r in enumerate(rank, 1) if r["order_number"] == hit[0]["order_number"])
    print("  loc %d din %d după risc" % (pos, len(rank)))
else:
    n = a.top if a.top > 0 else 20
    uniq.sort(key=lambda r: -r["_risk"])
    print("Top %d cele mai riscante%s:" % (min(n, len(uniq)), (" (magazin %s)" % a.magazin) if a.magazin else ""))
    for i, r in enumerate(uniq[:n], 1):
        print(_line(i, r))
