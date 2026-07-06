#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# ///
"""Identifică ADRESA (și tot ce ține) după un AWB DPD — expeditor + destinatar, colete, serviciu, preț.

Citește direct din DPD via POST /shipment/info (shipmentIds). Read-only, nu scrie nimic.
Credențiale din KB COURIER_CREDS_JSON -> dpd_creds[<account>].

  uv run scripts/dpd_info.py --awb 81317718793
  uv run scripts/dpd_info.py --awb 81317718793 --json     # brut, pt scripting
"""
import json, urllib.request, urllib.error, subprocess, argparse, sys, os

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


def party(p):
    if not p:
        return "—"
    a = p.get("address") or {}
    who = p.get("clientName") or p.get("contactName") or "?"
    contact = p.get("contactName")
    ph = ", ".join(x.get("number", "") for x in (p.get("phones") or []) if x.get("number"))
    typ = "PF" if p.get("privatePerson") else "firmă"
    line = "%s (%s)" % (who, typ)
    if contact and contact != who:
        line += " · contact: %s" % contact
    line += "\n      %s" % (a.get("fullAddressString") or "(fără adresă)")
    if ph:
        line += "\n      tel: %s" % ph
    return line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--awb", required=True, help="numărul AWB (spațiile se ignoră)")
    ap.add_argument("--account", default="dpd-ro")
    ap.add_argument("--json", action="store_true", help="întoarce răspunsul brut /shipment/info")
    a = ap.parse_args()
    awb = "".join(ch for ch in a.awb if ch.isdigit())
    auth = load_creds(a.account)

    s, d = call(auth, "/shipment/info/", {"shipmentIds": [awb]})
    sh = (d.get("shipments") or [None])[0] if isinstance(d, dict) else None
    if not sh:
        print("❌ AWB %s negăsit / fără acces (/shipment/info): %s" % (awb, json.dumps(d, ensure_ascii=False)[:300]))
        sys.exit(1)
    if a.json:
        print(json.dumps(sh, ensure_ascii=False, indent=1))
        return
    c = sh.get("content") or {}
    svc = sh.get("service") or {}
    pay = sh.get("payment") or {}
    pr = sh.get("price") or {}
    print("AWB %s" % sh.get("id", awb))
    print("  EXPEDITOR (ridicare de la):\n      %s" % party(sh.get("sender")))
    print("  DESTINATAR (livrare la):\n      %s" % party(sh.get("recipient")))
    print("  colete: %s · %s kg · %s · %s" % (c.get("parcelsCount", "?"), c.get("calculationWeight", "?"), c.get("package", "?"), c.get("contents", "")))
    print("  serviciu: %s · pickupDate %s · plătitor %s%s" % (
        svc.get("serviceId", "?"), svc.get("pickupDate", "?"), pay.get("courierServicePayer", "?"),
        (" (clientId %s)" % pay.get("thirdPartyClientId")) if pay.get("thirdPartyClientId") else ""))
    if pr:
        print("  preț: %s %s cu TVA" % (pr.get("total", "?"), pr.get("currency", "")))
    if sh.get("ref1"):
        print("  ref: %s" % sh.get("ref1"))
    print("  tracking: https://tracking.dpd.ro/?shipmentNumber=%s" % awb)


if __name__ == "__main__":
    main()
