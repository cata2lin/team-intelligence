# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""dpd_ops.py — restul operațiilor DPD (api.dpd.ro/v1) pe lângă crearea de AWB (dpd_awb.py).

Mutații + read-uri utile, din documentația oficială DPD:
  MUTAȚII (dry-run implicit; --apply execută; cancel cere și confirmare):
    cancel        /shipment/cancel      — anulează un AWB
    finalize      /shipment/finalize    — finalizează o expediere PENDING
    add-parcel    /shipment/add_parcel  — adaugă un colet la o expediere
    update-props  /shipment/update/properties — schimbă proprietăți (key=val)
    handover      /shipment/handover    — predă coletele la curier (fără pickup fizic)
  READ (fără efect, rulează direct):
    info          /shipment/info        — expeditor+destinatar+colete+serviciu+preț
    label         /print                — REIA eticheta: o pagină de FIECARE colet (vezi mai jos)
    search        /shipment/search      — caută expedieri după referință
    track         /track                — status colet
    validate-address   /validation/address   — validează o adresă (INTL înainte de AWB!)
    validate-postcode  /validation/postcode  — cod poștal valid pt o țară
    validate-phone     /validation/phone     — telefon valid
    offices       /location/office      — birouri/lockere DPD (pickup points)
    payments      /payments             — plăți/decontări COD pe interval

  uv run dpd_ops.py label --awb 81300000001            # 3 colete = 3 pagini (1/3, 2/3, 3/3)
  uv run dpd_ops.py cancel --awb 81300000002 --comment "gresit" --apply
  uv run dpd_ops.py validate-postcode --country Italy --zip 17100
  uv run dpd_ops.py offices --country RO --city Brasov
  uv run dpd_ops.py payments --from 2026-07-01 --to 2026-07-16

⚠️ ETICHETA UNEI EXPEDIERI CU MAI MULTE COLETE. /print/ tipărește exact coletele pe care i le dai.
Cu un singur id primești O pagină chiar dacă expedierea are trei — și două cutii pleacă fără
etichetă, ca 10-sep-2026. Id-urile celorlalte colete NU se ghicesc: nu sunt consecutive cu AWB-ul
(…317 → „Barcode not found") și nu apar în /track/. Stau în /shipment/info/ → content.parcels[].id,
în alt format: 11 cifre primul (= AWB-ul), 14 următoarele (81300000001 → 81300000000025, …00039).
"""
import re, os, sys, json, argparse, subprocess, urllib.request, urllib.error

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = "https://api.dpd.ro/v1"
RO = 642
CODE2NAME = {"IT": "ITALY", "DE": "GERMANY", "FR": "FRANCE", "ES": "SPAIN", "NL": "NETHERLANDS",
             "BE": "BELGIUM", "AT": "AUSTRIA", "PL": "POLAND", "CZ": "CZECH REPUBLIC", "SK": "SLOVAKIA",
             "HU": "HUNGARY", "BG": "BULGARIA", "GR": "GREECE", "PT": "PORTUGAL", "HR": "CROATIA"}
HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "..", "core", "scripts", "kb.py")


def kb_secret(key):
    v = os.environ.get(key)
    if v:
        return v
    return subprocess.run(["uv", "run", KB, "secret-get", key], capture_output=True, text=True, timeout=40).stdout.strip()


def auth_for(account):
    raw = kb_secret("COURIER_CREDS_JSON")
    dpd = ((json.loads(raw) if raw else {}).get("dpd_creds") or {}).get(account) or {}
    if not dpd.get("username"):
        raise SystemExit("Fără credențiale DPD pt contul '%s'." % account)
    return {"userName": dpd["username"], "password": dpd["password"], "language": "RO"}


def call(auth, path, extra, raw=False):
    req = urllib.request.Request(BASE + path, data=json.dumps({**auth, **extra}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        body = r.read()
        return (r.status, body) if raw else (r.status, json.loads(body.decode()))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": {"message": "HTTP %s" % e.code}}
    except Exception as e:
        return "ERR", {"error": {"message": str(e)[:200]}}


def err(d):
    if isinstance(d, dict) and d.get("error"):
        return (d["error"] or {}).get("message") or json.dumps(d["error"])[:200]
    return None


def resolve_country(auth, val):
    if val is None:
        return RO
    v = str(val).strip()
    if v.upper() in ("RO", "ROU", "ROMANIA", "ROMÂNIA", str(RO)):
        return RO
    names = [CODE2NAME[v.upper()], v] if (len(v) == 2 and v.upper() in CODE2NAME) else [v]
    for nm in names:
        s, d = call(auth, "/location/country/", {"name": nm})
        cs = (d or {}).get("countries") or []
        exact = [c for c in cs if (c.get("name") or "").upper() == nm.upper()]
        if exact:
            return exact[0]["id"]
        if cs:
            return cs[0]["id"]
    raise SystemExit("Țară negăsită: %s" % val)


def awb_ids(s):
    return [x.strip() for x in str(s).replace(",", " ").split() if x.strip()]


def show(d):
    print(json.dumps(d, ensure_ascii=False, indent=1)[:3000])


# ── MUTAȚII ─────────────────────────────────────────────────────────
def cmd_cancel(auth, a):
    for awb in awb_ids(a.awb):
        if not a.apply:
            print("  [DRY] aș anula AWB %s (comment: %s). Adaugă --apply." % (awb, a.comment or "-"))
            continue
        s, d = call(auth, "/shipment/cancel/", {"shipmentId": awb, "comment": a.comment or ""})
        e = err(d)
        print("  AWB %s → %s" % (awb, ("ANULAT ✅" if not e else "eroare: " + e)))


def cmd_finalize(auth, a):
    if not a.apply:
        print("  [DRY] aș finaliza expedierea %s. Adaugă --apply." % a.awb); return
    s, d = call(auth, "/shipment/finalize/", {"shipmentId": a.awb})
    print("  finalize %s → %s" % (a.awb, err(d) or "OK ✅"))
    show(d)


def cmd_add_parcel(auth, a):
    parcel = {"weight": float(a.weight)} if a.weight else {}
    if not a.apply:
        print("  [DRY] aș adăuga un colet la %s (%s). Adaugă --apply." % (a.awb, parcel)); return
    s, d = call(auth, "/shipment/add_parcel/", {"shipmentId": a.awb, "parcel": parcel})
    print("  add_parcel %s → %s" % (a.awb, err(d) or "OK ✅"))
    show(d)


def cmd_update_props(auth, a):
    props = {}
    for kv in (a.set or []):
        if "=" in kv:
            k, v = kv.split("=", 1)
            props[k.strip()] = v.strip()
    if not a.apply:
        print("  [DRY] aș seta pe %s: %s. Adaugă --apply." % (a.awb, props)); return
    s, d = call(auth, "/shipment/update/properties/", {"id": a.awb, "properties": props})
    print("  update-props %s → %s" % (a.awb, err(d) or "OK ✅"))
    show(d)


def cmd_handover(auth, a):
    parcels = [{"id": x} for x in awb_ids(a.awb)]
    if not a.apply:
        print("  [DRY] aș preda la curier: %s. Adaugă --apply." % [p["id"] for p in parcels]); return
    s, d = call(auth, "/shipment/handover/", {"parcels": parcels})
    print("  handover → %s" % (err(d) or "OK ✅"))
    show(d)


# ── READ ────────────────────────────────────────────────────────────
def cmd_info(auth, a):
    s, d = call(auth, "/shipment/info/", {"shipmentIds": awb_ids(a.awb)})
    show(d)


def cmd_label(auth, a):
    """Reia eticheta unei expedieri EXISTENTE — cate o pagina de fiecare colet.

    ⚠️ /print/ tipareste exact coletele pe care i le dai. Cu un singur id primesti O pagina, chiar
    daca expedierea are trei, si doua cutii pleaca fara eticheta. Id-urile celorlalte colete nu se
    ghicesc (nu-s consecutive cu AWB-ul, nu apar in /track/) — se citesc din /shipment/info/.
    """
    awb = awb_ids(a.awb)[0]
    s, d = call(auth, "/shipment/info/", {"shipmentIds": [awb]})
    sh = (d.get("shipments") or [d])[0] if isinstance(d, dict) else {}
    parcels = ((sh.get("content") or {}).get("parcels")) or []
    ids = [x["id"] for x in sorted(parcels, key=lambda x: x.get("seqNo", 0)) if x.get("id")] or [awb]
    print("  colete: %d → %s" % (len(ids), ", ".join(ids)))

    body = {"parcels": [{"parcel": {"id": i}} for i in ids],
            "format": "pdf", "paperSize": a.paper}
    s, data = call(auth, "/print/", body, raw=True)
    if not (isinstance(data, bytes) and data[:4] == b"%PDF"):
        print("  ⛔ /print/ n-a intors PDF: %s" % str(data)[:200]); raise SystemExit(1)
    out = os.path.expanduser(a.out or "~/Downloads")
    os.makedirs(out, exist_ok=True)
    cale = os.path.join(out, "AWB_%s.pdf" % awb)
    open(cale, "wb").write(data)
    pagini = len(re.findall(rb"/Type\s*/Page[^s]", data)) or 1
    print("  📄 %s · %d pagini" % (cale, pagini))
    if pagini != len(ids):
        print("  ⚠️ %d colete dar %d pagini — NU tipari pana nu se lamureste." % (len(ids), pagini))


def cmd_search(auth, a):
    s, d = call(auth, "/shipment/search/", {"ref": a.ref, "searchInRef": True})
    show(d)


def cmd_track(auth, a):
    s, d = call(auth, "/track/", {"parcels": [{"id": x} for x in awb_ids(a.awb)], "lastOperationOnly": bool(a.last)})
    show(d)


def cmd_validate_address(auth, a):
    cid = resolve_country(auth, a.country)
    addr = {"countryId": cid, "siteName": a.city, "streetName": a.street, "streetNo": str(a.no or "")}
    if a.zip:
        addr["postCode"] = str(a.zip)
    s, d = call(auth, "/validation/address/", {"address": addr})
    print("  validare adresă (%s) → %s" % (a.country, err(d) or "VALIDĂ ✅"))
    show(d)


def cmd_validate_postcode(auth, a):
    cid = resolve_country(auth, a.country)
    s, d = call(auth, "/validation/postcode/", {"countryId": cid, "postCode": str(a.zip)})
    print("  cod poștal %s (%s) → %s" % (a.zip, a.country, err(d) or "VALID ✅"))
    show(d)


def cmd_validate_phone(auth, a):
    s, d = call(auth, "/validation/phone/", {"number": str(a.phone)})
    print("  telefon %s → %s" % (a.phone, err(d) or "VALID ✅"))
    show(d)


def cmd_offices(auth, a):
    cid = resolve_country(auth, a.country)
    extra = {"countryId": cid}
    if a.city:
        extra["name"] = a.city
    s, d = call(auth, "/location/office/", extra)
    offs = (d or {}).get("offices") or []
    print("  %d birouri/lockere DPD" % len(offs))
    for o in offs[:30]:
        addr = (o.get("address") or {})
        print("   #%s %s · %s %s" % (o.get("id"), o.get("name", ""), addr.get("siteName", ""), addr.get("streetName", "")))


def cmd_payments(auth, a):
    s, d = call(auth, "/payments/", {"fromDate": a.from_, "toDate": a.to_, "includeDetails": bool(a.details)})
    show(d)


def main():
    ap = argparse.ArgumentParser(description="Operații DPD (mutații + validări) pe lângă dpd_awb.py")
    ap.add_argument("--account", default="dpd-ro")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def awbarg(p, help="AWB (unul sau mai multe, separate prin virgulă/spațiu)"):
        p.add_argument("--awb", required=True, help=help)

    c = sub.add_parser("cancel", help="anulează AWB"); awbarg(c); c.add_argument("--comment"); c.add_argument("--apply", action="store_true")
    f = sub.add_parser("finalize", help="finalizează expediere PENDING"); awbarg(f); f.add_argument("--apply", action="store_true")
    ap_ = sub.add_parser("add-parcel", help="adaugă colet"); awbarg(ap_); ap_.add_argument("--weight", type=float); ap_.add_argument("--apply", action="store_true")
    up = sub.add_parser("update-props", help="schimbă proprietăți (key=val)"); awbarg(up); up.add_argument("--set", action="append", help="key=val (repetabil)"); up.add_argument("--apply", action="store_true")
    h = sub.add_parser("handover", help="predă coletele la curier"); awbarg(h); h.add_argument("--apply", action="store_true")

    i = sub.add_parser("info", help="detalii expediere"); awbarg(i)
    lb = sub.add_parser("label", help="reia eticheta (o pagină de fiecare colet)"); awbarg(lb); lb.add_argument("--out", help="director (default ~/Downloads)"); lb.add_argument("--paper", default="A6", help="A6 (default) / A4")
    se = sub.add_parser("search", help="caută după referință"); se.add_argument("--ref", required=True)
    t = sub.add_parser("track", help="status colet"); awbarg(t); t.add_argument("--last", action="store_true")
    va = sub.add_parser("validate-address", help="validează adresă"); va.add_argument("--country", default="RO"); va.add_argument("--city", required=True); va.add_argument("--street", required=True); va.add_argument("--no"); va.add_argument("--zip")
    vp = sub.add_parser("validate-postcode", help="validează cod poștal"); vp.add_argument("--country", default="RO"); vp.add_argument("--zip", required=True)
    vph = sub.add_parser("validate-phone", help="validează telefon"); vph.add_argument("--phone", required=True)
    of = sub.add_parser("offices", help="birouri/lockere DPD"); of.add_argument("--country", default="RO"); of.add_argument("--city")
    pm = sub.add_parser("payments", help="plăți/decontări COD"); pm.add_argument("--from", dest="from_", required=True); pm.add_argument("--to", dest="to_", required=True); pm.add_argument("--details", action="store_true")

    a = ap.parse_args()
    auth = auth_for(a.account)
    {"cancel": cmd_cancel, "finalize": cmd_finalize, "add-parcel": cmd_add_parcel,
     "update-props": cmd_update_props, "handover": cmd_handover, "info": cmd_info,
     "label": cmd_label,
     "search": cmd_search, "track": cmd_track, "validate-address": cmd_validate_address,
     "validate-postcode": cmd_validate_postcode, "validate-phone": cmd_validate_phone,
     "offices": cmd_offices, "payments": cmd_payments}[a.cmd](auth, a)


if __name__ == "__main__":
    main()
