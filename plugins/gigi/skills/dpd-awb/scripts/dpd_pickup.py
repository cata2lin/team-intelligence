#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# ///
"""Comandă de RIDICARE (pickup) DPD pentru un AWB DEJA CREAT — direct prin API-ul DPD.

Când e nevoie: DOAR pentru AWB-urile făcute DIRECT prin DPD (ex. gigi:dpd-awb sau API direct).
Comenzile Shopify prin gigi:xconnector primesc pickup AUTOMAT — pentru alea NU rula asta.

Cum merge (adresa NU se ghicește):
  1. citește expeditorul REAL din AWB via POST /shipment/info,
  2. trimite POST /pickup cu pickupScope=EXPLICIT_SHIPMENT_ID_LIST → curierul ridică de la expeditorul din AWB.

Credențiale din KB COURIER_CREDS_JSON -> dpd_creds[<account>] (aceleași ca gigi:awb-track / gigi:dpd-awb).

  # DRY (arată expeditorul + intervalul, NU trimite):
  uv run scripts/dpd_pickup.py --awb 81317718793
  # TRIMITE comanda de ridicare:
  uv run scripts/dpd_pickup.py --awb 81317718793 --apply

Flags: --account dpd-ro|dpd-jg|dpd-px · --ready-in MIN (disponibil de la now+MIN, def 30;
DPD cere ora de disponibilitate STRICT în viitor) · --end HH:MM (ultima oră de vizită, def 18:00;
DPD o poate scurta la cut-off-ul zonei). --autoAdjust e pornit (mută pe ziua următoare dacă a trecut cut-off-ul).
"""
import json, urllib.request, urllib.error, subprocess, datetime, argparse, sys, os
from zoneinfo import ZoneInfo

BASE = "https://api.dpd.ro/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "..", "core", "scripts", "kb.py")


def kb_secret(key):
    v = os.environ.get(key)
    if v:
        return v
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", key], capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception:
        return ""


def load_creds(account):
    raw = kb_secret("COURIER_CREDS_JSON")
    d = json.loads(raw) if raw else {}
    dpd = (d.get("dpd_creds") or {}).get(account) or {}
    if not dpd.get("username"):
        raise SystemExit("Fără credențiale DPD pt contul '%s' (COURIER_CREDS_JSON.dpd_creds)." % account)
    return {"userName": dpd["username"], "password": dpd["password"], "language": "RO"}


def call(auth, path, extra, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps({**auth, **extra}).encode(), headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


# la scrierea adresei păstrăm DOAR câmpurile acceptate (aruncăm x/y/fullAddressString/… read-only din /shipment/info)
ADDR_KEEP = ("countryId", "siteId", "streetId", "streetNo", "streetName", "postCode",
             "siteName", "addressNote", "complexId", "blockNo", "entranceNo", "floorNo", "apartmentNo")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--awb", required=True, help="numărul AWB deja creat (spațiile se ignoră)")
    ap.add_argument("--apply", action="store_true", help="TRIMITE comanda de ridicare (fără flag = dry-run)")
    ap.add_argument("--account", default="dpd-ro")
    ap.add_argument("--ready-in", type=int, default=30, help="disponibil de la now + N min (def 30; DPD cere viitor)")
    ap.add_argument("--end", default="18:00", help="ultima oră la care poate veni curierul (def 18:00)")
    a = ap.parse_args()
    awb = "".join(ch for ch in a.awb if ch.isdigit())
    auth = load_creds(a.account)

    s, d = call(auth, "/shipment/info/", {"shipmentIds": [awb]})
    sh = (d.get("shipments") or [None])[0] if isinstance(d, dict) else None
    if not sh or not sh.get("sender"):
        print("❌ AWB %s: nu am putut citi expeditorul (/shipment/info): %s" % (awb, json.dumps(d, ensure_ascii=False)[:300]))
        sys.exit(1)
    snd = sh["sender"]
    addr = {k: v for k, v in (snd.get("address") or {}).items() if k in ADDR_KEEP}
    phone = ((snd.get("phones") or [{}])[0]).get("number", "")
    name = snd.get("clientName") or snd.get("contactName") or "Expeditor"
    npar = (sh.get("content") or {}).get("parcelsCount", "?")

    # Ora ROMANIEI, nu a serverului.
    #
    # Era `datetime.now()` (ora masinii) formatata cu un offset HARDCODAT `+0300`. Pe un server
    # care nu e pe fusul Romaniei, cele doua nu se potrivesc si ora ceruta iese in TRECUT — DPD
    # raspunde `readinessTime.readiness_expired` si curierul nu e chemat, desi AWB-ul s-a facut.
    # Masurat 10-sep-2026: VPS pe Europe/Berlin (+0200), Romania pe +0300 → ora ceruta era cu 30
    # de minute in urma, exact cat era marja. Doua etichete emise, zero curieri chemati.
    #
    # `%z` pe un datetime cu fus scrie singur +0300 vara si +0200 iarna, deci nu se strica nici
    # la schimbarea orei din ultima duminica din octombrie.
    now = datetime.datetime.now(ZoneInfo("Europe/Bucharest"))
    ready = (now + datetime.timedelta(minutes=a.ready_in)).strftime("%Y-%m-%dT%H:%M:%S%z")
    print("AWB %s · %s colet(e) · ridicare de la: %s" % (awb, npar, (snd.get("address") or {}).get("fullAddressString", "?")))
    print("  contact: %s · %s | disponibil de la %s → până la %s" % (name, phone or "—", ready, a.end))
    if not a.apply:
        print("\n→ DRY-RUN. Adaugă --apply ca să trimiți comanda de ridicare.\n")
        return

    body = {"pickupDateTime": ready, "autoAdjustPickupDate": True, "visitEndTime": a.end,
            "pickupScope": "EXPLICIT_SHIPMENT_ID_LIST", "explicitShipmentIdList": [awb],
            "contactName": name, "phoneNumber": {"number": phone},
            "recipient": {"privatePerson": bool(snd.get("privatePerson")), "clientName": name,
                          "phone1": {"number": phone}, "address": addr}}
    s, resp = call(auth, "/pickup/", body)
    err = resp.get("error") if isinstance(resp, dict) else None
    if err:
        print("❌ pickup eșuat: %s (%s)" % (err.get("message"), err.get("context")))
        sys.exit(1)
    o = (resp.get("orders") or [{}])[0]
    print("\n✅ COMANDĂ DE RIDICARE creată.")
    print("   order id: %s | AWB: %s | interval: %s → %s" % (
        o.get("id"), ",".join(o.get("shipmentIds") or [awb]), o.get("pickupPeriodFrom"), o.get("pickupPeriodTo")))


if __name__ == "__main__":
    main()
