# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
dpd_awb.py — creează un AWB DPD între DOUĂ adrese ORICARE (expeditor + destinatar liberi),
NElegat de o comandă Shopify. Pt: ridicare de la un terț, retururi, expedieri one-off.
Direct prin API-ul DPD (api.dpd.ro), pe contul ARONA (creds din KB COURIER_CREDS_JSON → dpd_creds).

Deosebiri față de restul:
- `gigi:xconnector awb-make` = AWB pt o COMANDĂ Shopify (magazinul = expeditor). NU face sender liber.
- `gigi:awb-track` = DOAR urmărire.
- ăsta = AWB cu expeditor/destinatar arbitrari + preț + etichetă PDF.

Dry-run by default (calculează prețul + validează adresele). Creează REAL doar cu `--apply`.

  # dry-run (preț + adrese rezolvate):
  uv run dpd_awb.py \
    --from-name "CRSP Iasi" --from-phone 0232410399 --from-city Iasi --from-street "Victor Babes" --from-no 14 --from-zip 700465 \
    --to-name "ARONA SRL" --to-contact "Gheorghe Beschea" --to-phone 0746661159 --to-city Brasov --to-street Bazaltului --to-no 11 \
    --content Documente --weight 0.5
  # creare reală + etichetă în ~/Downloads:
  uv run dpd_awb.py ... --apply

Plătitor: implicit contul ARONA (clientId luat automat din /client/), ca THIRD_PARTY — merge și când
NICIUNA din părți nu e adresa noastră (regula DPD: „clientul tău trebuie să fie plătitor sau expeditor").
Override: --payer sender|recipient|third. COD (ramburs): --cod SUMA.
"""
import argparse, base64, json, os, subprocess, sys, urllib.request, urllib.error

for _s in (sys.stdout, sys.stderr):  # Windows cp1252 → nu crăpa pe diacritice
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = "https://api.dpd.ro/v1"
RO = 642
SERVICE_STANDARD = 2505  # DPD STANDARD (domestic)
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


def call(auth, path, extra, raw=False, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps({**auth, **extra}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        body = r.read()
        if raw:
            return r.status, body
        return r.status, json.loads(body.decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": {"message": "HTTP %s" % e.code}}
    except Exception as e:
        return "ERR", {"error": {"message": str(e)[:200]}}


def _err(d):
    """Extrage mesajul de eroare DPD (structuri diferite pe /calculate vs /shipment)."""
    if not isinstance(d, dict):
        return str(d)[:200]
    if d.get("error"):
        return (d["error"] or {}).get("message") or json.dumps(d["error"])[:200]
    for cc in d.get("calculations") or []:
        if cc.get("error"):
            return (cc["error"] or {}).get("message") or json.dumps(cc["error"])[:200]
    return None


def our_client_id(auth):
    s, d = call(auth, "/client/", {})
    if s == 200 and isinstance(d, dict) and d.get("clientId"):
        return d["clientId"]
    raise SystemExit("Nu am putut lua clientId-ul contului DPD (/client/).")


def resolve_site(auth, city, postcode=None):
    extra = {"countryId": RO, "name": city}
    if postcode:
        extra = {"countryId": RO, "postCode": str(postcode)}
    s, d = call(auth, "/location/site/", extra)
    sites = (d or {}).get("sites") or []
    if not sites and postcode:  # fallback pe nume dacă codul poștal nu prinde
        s, d = call(auth, "/location/site/", {"countryId": RO, "name": city})
        sites = (d or {}).get("sites") or []
    if not sites:
        raise SystemExit("Oraș negăsit în nomenclatorul DPD: %s %s" % (city, postcode or ""))
    up = (city or "").strip().upper()
    exact = [x for x in sites if (x.get("name") or "").upper() == up and x.get("type") == "or."]
    city_t = [x for x in sites if x.get("type") == "or."]
    return (exact or city_t or sites)[0]


def resolve_street(auth, site_id, street):
    s, d = call(auth, "/location/street/", {"countryId": RO, "siteId": site_id, "name": street})
    streets = (d or {}).get("streets") or []
    if not streets:  # încearcă doar ultimul cuvânt (ex „Prof Dr Victor Babes" → „Babes")
        s, d = call(auth, "/location/street/", {"countryId": RO, "siteId": site_id, "name": street.split()[-1]})
        streets = (d or {}).get("streets") or []
    if not streets:
        raise SystemExit("Stradă negăsită în %s: %s" % (site_id, street))
    up = (street or "").strip().upper()
    exact = [x for x in streets if up in (x.get("name") or "").upper()]
    return (exact or streets)[0]


def party(auth, name, contact, phone, email, city, street, no, zip_, private, addr_key):
    site = resolve_site(auth, city, zip_)
    st = resolve_street(auth, site["id"], street)
    addr = {"countryId": RO, "siteId": site["id"], "streetId": st["id"], "streetNo": str(no)}
    if zip_:
        addr["postCode"] = str(zip_)
    p = {"clientName": name, "contactName": contact or name, "phone1": {"number": str(phone)}}
    if email:
        p["email"] = email
    if private:
        p["privatePerson"] = True
    else:
        p["privatePerson"] = False
    p[addr_key] = addr
    p["_pretty"] = "%s [%s] %s nr. %s" % (site.get("name"), (addr.get("postCode") or site.get("postCode") or ""), st.get("name"), no)
    return p


def strip_pretty(p):
    return {k: v for k, v in p.items() if k != "_pretty"}


def build(auth, a, addr_key, service_key):
    """Construiește payload-ul (addr_key/service_key diferă între /calculate și /shipment)."""
    snd = party(auth, a.from_name, a.from_contact, a.from_phone, a.from_email,
                a.from_city, a.from_street, a.from_no, a.from_zip, a.from_private, addr_key)
    rcp = party(auth, a.to_name, a.to_contact, a.to_phone, a.to_email,
                a.to_city, a.to_street, a.to_no, a.to_zip, a.to_private, addr_key)
    svc = {service_key: ([SERVICE_STANDARD] if service_key == "serviceIds" else SERVICE_STANDARD),
           "autoAdjustPickupDate": True}
    content = {"parcelsCount": int(a.parcels), "totalWeight": float(a.weight),
               "contents": a.content, "package": a.package}
    # plătitor: implicit contul nostru (THIRD_PARTY cu clientId), ca să meargă și cu sender/recipient străini
    payer = (a.payer or "third").lower()
    if payer == "sender":
        payment = {"courierServicePayer": "SENDER"}
    elif payer == "recipient":
        payment = {"courierServicePayer": "RECIPIENT"}
    else:
        payment = {"courierServicePayer": "THIRD_PARTY", "thirdPartyClientId": our_client_id(auth)}
    if a.cod and float(a.cod) > 0:
        svc.setdefault("additionalServices", {})["cod"] = {
            "amount": float(a.cod), "processingType": "CASH", "payoutToClientId": our_client_id(auth)}
    return {"sender": strip_pretty(snd), "recipient": strip_pretty(rcp),
            "service": svc, "content": content, "payment": payment,
            "ref1": a.ref or ""}, snd, rcp


def print_price(d):
    for cc in (d.get("calculations") or [d]):
        pr = cc.get("price") or {}
        if pr:
            print("  💰 preț: %.2f %s (net %.2f + TVA %.2f)" % (pr.get("total", 0), pr.get("currency", "RON"),
                                                                pr.get("amount", 0), pr.get("vat", 0)))
            return pr.get("total")
    return None


def fetch_label(auth, awb, out_dir):
    body = {"parcels": [{"parcel": {"id": awb}}], "format": "pdf", "paperSize": "A6"}
    s, data = call(auth, "/print/", body, raw=True)
    if isinstance(data, bytes) and data[:4] == b"%PDF":
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "AWB_%s.pdf" % awb)
        open(path, "wb").write(data)
        return path
    return None


def main():
    ap = argparse.ArgumentParser(description="Creează un AWB DPD între două adrese oarecare (dry-run by default).")
    for side, lab in (("from", "EXPEDITOR"), ("to", "DESTINATAR")):
        ap.add_argument("--%s-name" % side, required=True, help="%s: nume firmă/persoană (clientName)" % lab)
        ap.add_argument("--%s-contact" % side, help="%s: persoană de contact (default = name)" % lab)
        ap.add_argument("--%s-phone" % side, required=True, help="%s: telefon" % lab)
        ap.add_argument("--%s-email" % side, help="%s: email" % lab)
        ap.add_argument("--%s-city" % side, required=True, help="%s: oraș/localitate" % lab)
        ap.add_argument("--%s-street" % side, required=True, help="%s: stradă" % lab)
        ap.add_argument("--%s-no" % side, required=True, help="%s: număr" % lab)
        ap.add_argument("--%s-zip" % side, help="%s: cod poștal (recomandat)" % lab)
        ap.add_argument("--%s-private" % side, action="store_true", help="%s: persoană fizică (default firmă)" % lab)
    ap.add_argument("--content", required=True, help="conținut colet (apare pe AWB)")
    ap.add_argument("--weight", type=float, default=0.5, help="greutate totală kg (default 0.5)")
    ap.add_argument("--parcels", type=int, default=1, help="nr colete (default 1)")
    ap.add_argument("--package", default="ENVELOPE", help="tip: ENVELOPE/BOX/PALLET/OTHER (default ENVELOPE)")
    ap.add_argument("--cod", type=float, default=0, help="ramburs (SUMA în RON); 0 = fără")
    ap.add_argument("--payer", choices=["sender", "recipient", "third"], help="plătitor (default third = contul ARONA)")
    ap.add_argument("--account", default="dpd-ro", help="cont DPD din creds (dpd-ro/dpd-jg/dpd-px)")
    ap.add_argument("--ref", help="referință liberă (ref1)")
    ap.add_argument("--out", help="folder etichetă (default ~/Downloads)")
    ap.add_argument("--apply", action="store_true", help="CREEAZĂ AWB-ul real (altfel doar calculează prețul)")
    a = ap.parse_args()

    auth = load_creds(a.account)
    # 1) DRY-RUN: calculează (schema /calculate: addressLocation + serviceIds)
    calc_body, snd, rcp = build(auth, a, "addressLocation", "serviceIds")
    print("═" * 64)
    print("  AWB DPD — %s → %s" % (a.from_name, a.to_name))
    print("  expeditor:  %s · %s" % (snd["_pretty"], a.from_phone))
    print("  destinatar: %s · %s" % (rcp["_pretty"], a.to_phone))
    print("  serviciu: DPD STANDARD · %d colet(e) · %.2f kg · %s%s · plătește: %s"
          % (a.parcels, a.weight, a.content, (" · ramburs %.2f RON" % a.cod) if a.cod else "", (a.payer or "third")))
    s, d = call(auth, "/calculate/", calc_body)
    e = _err(d)
    if e:
        print("  ⛔ eroare validare: %s" % e); raise SystemExit(1)
    print_price(d)
    if not a.apply:
        print("  → [DRY-RUN] adrese valide + preț OK. Adaugă --apply ca să CREEZI AWB-ul real.")
        return
    # 2) CREARE reală (schema /shipment: address + serviceId singular)
    ship_body, _, _ = build(auth, a, "address", "serviceId")
    s, d = call(auth, "/shipment/", ship_body)
    e = _err(d)
    if e or not (isinstance(d, dict) and d.get("id")):
        print("  ⛔ creare eșuată: %s" % (e or json.dumps(d)[:200])); raise SystemExit(1)
    awb = d["id"]
    print("  ✅ AWB creat: %s · ridicare %s" % (awb, d.get("pickupDate", "?")))
    print("     tracking: https://tracking.dpd.ro/?shipmentNumber=%s" % awb)
    path = fetch_label(auth, awb, os.path.expanduser(a.out or "~/Downloads"))
    if path:
        print("  📄 etichetă: %s" % path)
    else:
        print("  ⚠️ AWB făcut dar eticheta nu s-a putut descărca (reia cu /print/).")


if __name__ == "__main__":
    main()
