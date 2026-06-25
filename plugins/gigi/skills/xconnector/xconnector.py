# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000"]
# ///
"""
xconnector.py — punte READ-ONLY spre xConnector (curierat) pt fluxul ARONA.

Ce poate AZI (prin cheia API, durabil):
  • address-issues — comenzile NEPORNITE (fără AWB) cu adresă WRONG/UNKNOWN la xConnector,
    cu adresa curentă + ce zice validatorul (candidat + scor) + verdict auto/manual.
    = semnal de 'confirmă/corectează adresa ÎNAINTE de AWB" (prevenție refuzuri), pereche cu gigi:cs-address-guard.
  • summary — câte comenzi pe fiecare status, câte fără AWB, per magazin.

API DE SCRIERE — EXPUS din 2026-06-24 (docs: https://xconnector.app/api-docs.html ; spec: /api-spec.yaml).
Creare AWB / dispatch / facturi NU mai sunt dashboard-only. Endpoint-uri sync: POST /api/actions/
create-shipping-label, cancel-shipping-label, dispatch-order, estimate-shipping-price, create-invoice
(+ create-invoice-payment/cancel-invoice/revert-invoice), locker-notification; POST /api/v1/picking-lists/
add-order; GET /api/orders/by-tracking-number. BLOCAJ real: toate /api/actions/* + ai-correct-address cer
rolul ROLE_AUTOMATION pe merchant + permisiuni per-cheie (API_CREATE_SHIPPING_LABEL etc.) — fără ele = 403.
Pe GT (ix5bxc-hr) ROLE_AUTOMATION e încă DE ACTIVAT de vendor; până atunci AWB-ul rămâne pe Shopify Flow.

LECȚIE VALIDARE (descoperit 2026-06-24): addressStatus WRONG/UNKNOWN SUPRA-flaghează. Validarea e
asincronă/în batch — comenzi stau WRONG/UNKNOWN ore→1 zi, apoi un sweep al xConnector le trece pe VALID fără
editare (~16% se auto-vindecă). WRONG NU e predictor de eșec la livrare (pe un eșantion, 6/8 colete cu adresă
WRONG s-au livrat). → nu trata un flag proaspăt ca problemă reală: rulează `correct --min-age-hours N` (sare
comenzile mai noi de N ore, lasă sweep-ul lor să ruleze) și `recheck` (vezi care s-au auto-validat) înainte
de a deranja CS-ul.

Auth: cheia API xConnector per magazin. Sursă (în ordine): secret KB `XCONNECTOR_SHOPS` (JSON
[{shopDomain,apiKey}]), altfel `~/.aac/input.json`. NICIODATĂ printată.

  uv run xconnector.py summary
  uv run xconnector.py address-issues [--shop ix5bxc-hr.myshopify.com] [--days 60] [--json]
  uv run xconnector.py recheck [--order GT123,GT456] [--days 30]   # care s-au auto-validat (VALID/PERFECT)
Read-only pe xConnector (recheck/issues/summary nu scriu nimic; `correct` scrie corecții de adresă cu --apply).
"""
import os, sys, json, re, time, hashlib, argparse, subprocess, urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
XBASE = "https://xconnector.app"
VBASE = "https://address-validator.xconnector.app"


def load_shops():
    """[{shopDomain, apiKey}] din KB (XCONNECTOR_SHOPS) sau ~/.aac/input.json. Secret — nu se printează."""
    raw = os.environ.get("XCONNECTOR_SHOPS")
    if not raw:
        try:
            raw = subprocess.run(["uv", "run", KB, "secret-get", "XCONNECTOR_SHOPS"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            raw = ""
    if raw and raw.startswith("["):
        try:
            return json.loads(raw)
        except Exception:
            pass
    p = os.path.expanduser("~/.aac/input.json")
    if os.path.exists(p):
        return json.load(open(p)).get("shops", [])
    return []


def http(method, url, headers, body=None, timeout=45):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return "ERR", str(e)[:160]


class XC:
    def __init__(self, apikey):
        self.h = {"Authorization": "Bearer " + apikey, "Content-Type": "application/json"}
        self.vtok = None

    def get(self, path, q=""):
        s, b = http("GET", XBASE + path + (("?" + q) if q else ""), self.h)
        try:
            return s, json.loads(b)
        except Exception:
            return s, b

    def orders(self, dfrom, dto):
        """toate comenzile în fereastră (paginat), cu addressStatus + documents."""
        out, seen = [], set()
        for page in range(0, 12):
            s, d = self.get("/api/orders", "fromOrderDate=%s&toOrderDate=%s&page=%d&size=200" % (dfrom, dto, page))
            if s != 200:
                break
            arr = d if isinstance(d, list) else (d.get("content") or d.get("orders") or [])
            if not arr:
                break
            for o in arr:
                oid = o.get("orderId")
                if oid not in seen:
                    seen.add(oid); out.append(o)
            if len(arr) < 200:
                break
        return out

    def by_id(self, oid):
        s, d = self.get("/api/orders/by-id", "orderId=%s" % oid)
        return d if s == 200 and isinstance(d, dict) else {}

    def post(self, path, body):
        """POST /api/actions/* sau alt endpoint de scriere. Întoarce (status, json|text)."""
        s, b = http("POST", XBASE + path, self.h, body)
        try:
            return s, json.loads(b)
        except Exception:
            return s, b

    def list_connectors(self):
        s, d = self.get("/api/merchant/connectors")
        return d if s == 200 and isinstance(d, list) else []

    def vtoken(self):
        if not self.vtok:
            s, d = http("POST", XBASE + "/api/token", self.h)
            try:
                self.vtok = json.loads(d).get("accessToken")
            except Exception:
                self.vtok = None
        return self.vtok

    def match(self, addr):
        h = {"Content-Type": "application/json"}
        t = self.vtoken()
        if t:
            h["Authorization"] = "Bearer " + t
        s, b = http("POST", VBASE + "/match-address", h, addr)
        try:
            return json.loads(b)
        except Exception:
            return []


# ── Shopify Admin (declanșează Shopify Flow → acțiunea xConnector create/cancel AWB) ──
# Mecanism: comenzile noi stau pe FULFILLMENT HOLD (Flow Order-created->Hold). Noi eliberăm
# hold-ul DOAR la comenzile sigure → Flow Fulfillment-hold-released -> xConnector Create AWB.
SHOPIFY_API = "2026-04"


def _stores_csv_tokens():
    """[{prefix, shopDomain, adminToken}] din SHOPIFY_STORES_CSV (canonic, TOATE magazinele; col prefix/shop/token).
    Sursă: env SHOPIFY_STORES_CSV (path sau text) sau KB. NUB = OAuth-rotation (token static mort, merge pe VPS).
    Pe VPS fără uv/KB → întoarce [] grațios (cron-ul folosește env SHOPIFY_ADMIN_TOKENS)."""
    import csv, io
    raw = os.environ.get("SHOPIFY_STORES_CSV") or ""
    if raw and "\n" not in raw and os.path.exists(raw):
        try:
            raw = open(raw, encoding="utf-8-sig").read()
        except Exception:
            raw = ""
    if not raw or "\n" not in raw:
        try:
            raw = subprocess.run(["uv", "run", KB, "secret-get", "SHOPIFY_STORES_CSV"],
                                 capture_output=True, text=True, timeout=40).stdout
        except Exception:
            raw = ""
    out = []
    try:
        for row in csv.DictReader(io.StringIO(raw)):
            pref = (row.get("prefix") or "").strip().lstrip("﻿").upper()
            shop = (row.get("shop") or "").strip().replace("https://", "").strip("/")
            tok = (row.get("token") or "").strip()
            if pref and shop and tok:
                out.append({"prefix": pref, "shopDomain": shop, "adminToken": tok})
    except Exception:
        pass
    return out


def load_shopify_tokens():
    """[{prefix, shopDomain, adminToken}] pt TOATE magazinele: bază din SHOPIFY_STORES_CSV (canonic),
    suprascris de SHOPIFY_ADMIN_TOKENS (env/KB) pt override-uri/tokenuri proaspete. NU se printează."""
    by_dom = {t["shopDomain"]: t for t in _stores_csv_tokens()}
    raw = os.environ.get("SHOPIFY_ADMIN_TOKENS")
    if not raw:
        try:
            raw = subprocess.run(["uv", "run", KB, "secret-get", "SHOPIFY_ADMIN_TOKENS"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            raw = ""
    try:
        for t in (json.loads(raw) if raw.startswith("[") else []):
            if t.get("shopDomain") and t.get("adminToken"):
                by_dom[t["shopDomain"]] = t
    except Exception:
        pass
    return list(by_dom.values())


def shopify_gql(shop, token, query):
    url = "https://%s/admin/api/%s/graphql.json" % (shop, SHOPIFY_API)
    h = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    s, b = http("POST", url, h, {"query": query})
    try:
        return json.loads(b)
    except Exception:
        return {"_status": s, "_raw": b[:200]}


def find_order(shop, token, name):
    """nodul comenzii + fulfillmentOrders (id+status), după orderName (ex GT44004)."""
    q = ('query{ orders(first:1, query:"name:%s"){ edges{ node{ id name displayFulfillmentStatus '
         'fulfillmentOrders(first:10){ edges{ node{ id status } } } } } } }') % name.replace('"', "")
    d = shopify_gql(shop, token, q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    return edges[0]["node"] if edges else None


def shopify_order_tags(name, toks):
    """tagurile comenzii Shopify (lower-case), după orderName (ex GT43675). [] dacă n-o găsesc."""
    pm = re.match(r"^([A-Za-z]+)", name or "")
    t = toks.get(pm.group(1).upper()) if pm else None
    if not t:
        return []
    q = 'query{ orders(first:1, query:"name:%s"){ edges{ node{ tags } } } }' % (name or "").replace('"', "")
    d = shopify_gql(t["shopDomain"], t["adminToken"], q)
    edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
    return [str(x).lower() for x in (edges[0]["node"].get("tags") or [])] if edges else []


def cmd_awb(a):
    """create = ELIBERează hold-ul (→ Flow hold-released -> xConnector Create AWB);
    hold = pune fulfillment-ul în hold; cancel = info (fără trigger de tag)."""
    action = a.cmd.split("-")[1]  # create | cancel | hold
    toks = {t["prefix"]: t for t in load_shopify_tokens()}
    pm = re.match(r"^([A-Za-z]+)", a.order)
    pref = pm.group(1).upper() if pm else ""
    sh = toks.get(pref)
    if not sh:
        print("Niciun token Shopify pt prefixul '%s' (am: %s). Adaugă în KB SHOPIFY_ADMIN_TOKENS." % (pref, list(toks))); return
    shop, token = sh["shopDomain"], sh["adminToken"]
    node = find_order(shop, token, a.order)
    if not node:
        print("Comanda %s negăsită în Shopify (%s)." % (a.order, shop)); return
    fos = [e["node"] for e in ((node.get("fulfillmentOrders") or {}).get("edges") or [])]
    print("Comandă %s | fulfillment: %s | fulfillmentOrders: %s" % (
        a.order, node.get("displayFulfillmentStatus"), [(f["id"].split("/")[-1], f["status"]) for f in fos]))

    def mut(fo_id, name_):
        body = ('fulfillmentHold:{reason:OTHER, reasonNotes:"xc-review"}, ' if name_ == "fulfillmentOrderHold" else "")
        sub = ("fulfillmentOrder{status} " if name_ == "fulfillmentOrderReleaseHold" else "")
        m = 'mutation{ %s(%sid:"%s"){ %suserErrors{field message} } }' % (name_, body, fo_id, sub)
        d = shopify_gql(shop, token, m)
        return (((d.get("data") or {}).get(name_) or {}).get("userErrors")) or d.get("errors")

    if action == "cancel":
        print("  Anulare AWB: setează un Flow Order-cancelled -> Cancel-shipping-label, sau anulează din dashboard xConnector.")
        print("  (nu există trigger pe tag pt cancel; hold-release e doar pt create.)"); return

    if action == "hold":
        tgt = [f for f in fos if f["status"] == "OPEN"]
        if not tgt:
            print("  Nimic OPEN de pus în hold (status: %s)." % [f["status"] for f in fos]); return
        if not a.apply:
            print("  DRY-RUN: aș pune în hold %d fulfillmentOrder(s)." % len(tgt)); return
        ok = sum(0 if mut(f["id"], "fulfillmentOrderHold") else 1 for f in tgt)
        print("  ✅ %d pus în hold." % ok); return

    # create = eliberează hold-ul → Flow 'hold released" → Create AWB
    held = [f for f in fos if f["status"] == "ON_HOLD"]
    if not held:
        print("  Comanda NU e în hold → Flow-ul hold-released nu se declanșează.")
        print("  → pune-o întâi în hold (Flow Order-created->Hold la comenzi noi, sau `awb-hold --order %s --apply`)." % a.order); return
    if not a.apply:
        print("  DRY-RUN: aș ELIBERA hold-ul pe %d fulfillmentOrder(s) → Flow → Create AWB." % len(held)); return
    ok = sum(0 if mut(f["id"], "fulfillmentOrderReleaseHold") else 1 for f in held)
    print("  ✅ hold eliberat pe %d → Flow hold-released -> xConnector creează AWB." % ok)


def release_hold(shop, token, name):
    """eliberează hold-ul pe fulfillment-order-ele ON_HOLD ale comenzii. (nr eliberate, nr held)"""
    node = find_order(shop, token, name)
    if not node:
        return 0, 0
    fos = [e["node"] for e in ((node.get("fulfillmentOrders") or {}).get("edges") or [])]
    held = [f for f in fos if f["status"] == "ON_HOLD"]
    rel = 0
    for f in held:
        m = 'mutation{ fulfillmentOrderReleaseHold(id:"%s"){ userErrors{message} } }' % f["id"]
        d = shopify_gql(shop, token, m)
        e = (((d.get("data") or {}).get("fulfillmentOrderReleaseHold") or {}).get("userErrors")) or d.get("errors")
        if not e:
            rel += 1
    return rel, len(held)


def cmd_awb_auto(a):
    """POARTA auto-AWB: validez adresa la xConnector și eliberez hold-ul (→ Flow → Create AWB)
    DOAR la comenzile fără AWB cu adresă VALIDĂ. WRONG/UNKNOWN rămân în hold (CS / auto-correct)."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    shops = load_shops()
    toks = {t["prefix"]: t for t in load_shopify_tokens()}
    for sh in shops:
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        noawb = [o for o in xc.orders(dfrom, dto) if not has_awb(o)]
        valid = [o for o in noawb if o.get("addressStatus") == "VALID"]
        bad = [o for o in noawb if o.get("addressStatus") in ("WRONG", "UNKNOWN")]
        print("═" * 70)
        print("  %s — %d fără AWB | %d adresă VALIDĂ | %d adresă proastă (rămân în hold)"
              % (sh["shopDomain"], len(noawb), len(valid), len(bad)))
        rel = 0
        for o in valid:
            name = o.get("orderName")
            pm = re.match(r"^([A-Za-z]+)", name or "")
            st = toks.get(pm.group(1).upper() if pm else "")
            if not st:
                continue
            if not a.apply:
                node = find_order(st["shopDomain"], st["adminToken"], name)
                fos = [e["node"] for e in ((node or {}).get("fulfillmentOrders", {}).get("edges") or [])] if node else []
                if any(f["status"] == "ON_HOLD" for f in fos):
                    print("  [dry] aș elibera %s (adresă validă, în hold) → AWB" % name)
                continue
            r, _ = release_hold(st["shopDomain"], st["adminToken"], name)
            rel += r
        if a.apply:
            print("  → ELIBERAT %d comenzi cu adresă validă → Flow creează AWB." % rel)
        # corecția pe cele invalide (cu --correct): repară conservator → cele reparate se eliberează
        if a.correct and bad:
            cor = manual = relc = 0
            print("  — corecție pe %d adrese proaste (conservator)%s:" % (len(bad), "" if a.apply else " [DRY-RUN]"))
            for o in bad:
                st, applied, detail = correct_address(xc, o, sh["shopDomain"], apply=a.apply)
                name = o.get("orderName")
                if st in ("would-correct", "corrected"):
                    cor += 1
                    print("    %s %s → %s" % (name, "✅ corectat" if st == "corrected" else "[ar corecta]", detail))
                    if a.apply and st == "corrected":
                        pm = re.match(r"^([A-Za-z]+)", name or "")
                        stk = toks.get(pm.group(1).upper() if pm else "")
                        if stk:
                            r, _ = release_hold(stk["shopDomain"], stk["adminToken"], name); relc += r
                else:
                    manual += 1
            print("    → %d corectabile (%d eliberate după corecție) | %d → CS manual" % (cor, relc, manual))
        elif bad:
            print("  → %d cu adresă proastă RĂMÂN ÎN HOLD (rulează cu --correct, sau CS): %s"
                  % (len(bad), ", ".join(o.get("orderName") for o in bad[:10])))


def has_awb(o):
    return any((d.get("documentType") == "SHIPPING_LABEL") for d in (o.get("documents") or []) if isinstance(d, dict))


def skip_shop(sh, a):
    """True dacă magazinul trebuie SĂRIT: nu e cel cerut (--shop) sau e în lista de excludere (--exclude).
    --exclude e pt magazinele pe care validatorul de adrese RO nu le acoperă (ex Bonhaus CZ/PL/BG)."""
    dom = sh.get("shopDomain")
    if getattr(a, "shop", None) and dom != a.shop:
        return True
    excl = {x.strip() for x in (getattr(a, "exclude", "") or "").split(",") if x.strip()}
    return dom in excl


def order_age_hours(xc, oid):
    """Vârsta comenzii în ore, din cel mai vechi eveniment de validare (addressValidationHistory).
    None dacă nu există istoric/timestamp. Folosit de `correct --min-age-hours` ca să sară comenzile
    proaspete (validarea xConnector e async/batch — multe se auto-validează în câteva ore)."""
    import datetime
    d = xc.by_id(oid)
    ts = [h.get("timestamp") for h in (d.get("addressValidationHistory") or [])
          if isinstance(h, dict) and h.get("timestamp")]
    if not ts:
        return None
    try:
        t0 = datetime.datetime.fromisoformat(min(ts).replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - t0).total_seconds() / 3600.0
    except Exception:
        return None


def fscore(m, k):
    v = m.get(k) or {}
    return (v.get("value"), v.get("score")) if isinstance(v, dict) else (v, None)


def verdict(matchers):
    """conservator (ca aac): UN singur candidat cu toate core-urile ≥0.95 → auto; altfel manual."""
    ms = matchers if isinstance(matchers, list) else (matchers.get("matchers") or matchers.get("matches") or [])
    if not ms:
        return "fără candidați → manual", None
    strong = [m for m in ms if all((fscore(m, f)[1] or 0) >= 0.95 for f in ("zipCode", "county", "city", "streetName"))]
    top = ms[0]
    sug = "%s, %s %s (%s)" % (fscore(top, "streetName")[0], fscore(top, "city")[0], fscore(top, "zipCode")[0], fscore(top, "county")[0])
    if len(strong) == 1:
        return "✅ auto-corectabil (candidat unic ≥0.95)", sug
    if len(strong) > 1:
        return "⚠️ %d candidați tari → manual" % len(strong), sug
    return "⚠️ niciun candidat ≥0.95 → manual", sug


def _digest(obj, n):
    s = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _fold(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "")) if not unicodedata.combining(c)).lower().strip()


def zip_confirm(xc, zipc):
    if not zipc:
        return None
    t = xc.vtoken()
    h = {"Authorization": "Bearer " + t} if t else {}
    s, b = http("GET", VBASE + "/zip-code?countryId=1&zipCode=" + urllib.parse.quote(str(zipc)), h)
    try:
        d = json.loads(b)
        return d if (s == 200 and d) else None
    except Exception:
        return None


def correct_address(xc, o, shop_domain, apply=False):
    """CONSERVATOR (după aac): corectează adresa DOAR dacă există UN candidat cu toate core ≥0.95
    + zip confirmat la /zip-code + numărul casei păstrat. Întoarce (status, applied|None, detalii).
    status: would-correct | corrected | manual | error:<code>."""
    oid = o["orderId"]
    d = xc.by_id(oid)
    ad = d.get("shippingAddress") or {}
    ms = xc.match({"country": "Romania", "zipCode": ad.get("zip") or "", "county": ad.get("province") or "",
                   "city": ad.get("city") or "", "address1": ad.get("address1") or "", "address2": ad.get("address2") or ""})
    msl = ms if isinstance(ms, list) else (ms.get("matchers") or ms.get("matches") or [])
    # zip/oraș/județ ≥0.95 + stradă ≥0.90 (relaxat — există plasă de siguranță DPD/client la preluare).
    # UN singur candidat (fără competitor) = nu riscăm o adresă validă-dar-greșită.
    strong = [m for m in msl if all((fscore(m, f)[1] or 0) >= 0.95 for f in ("zipCode", "county", "city"))
              and (fscore(m, "streetName")[1] or 0) >= 0.90]
    if len(strong) != 1:
        return "manual", None, "%d candidați (zip/oraș/județ≥0.95, stradă≥0.90)" % len(strong)
    m = strong[0]
    czip = str(fscore(m, "zipCode")[0] or "")
    ccity = fscore(m, "city")[0] or ad.get("city") or ""
    ccounty = fscore(m, "county")[0] or ad.get("province") or ""
    tok = m.get("tokenizedAddress") or {}
    orig_nums = re.findall(r"\b(\d+[A-Za-z]?)\b", ad.get("address1") or "")
    num = (tok.get("streetNumber") or "").strip()
    if not num and len(orig_nums) == 1:
        num = orig_nums[0]
    if not num or (orig_nums and num not in orig_nums):
        return "manual", None, "număr casă nesigur"
    if not zip_confirm(xc, czip):
        return "manual", None, "zip neconfirmat"
    # construiește adresa: păstrează TOT, înlocuiește core; strada canonică doar dacă diferă după folding
    stype = (tok.get("streetType") or "").strip()
    # numele CANONIC al străzii (valoarea matcher-ului), nu forma tokenizată a clientului (aac HARD RULE 5)
    sname = (fscore(m, "streetName")[0] or tok.get("streetName") or "").strip()
    new_a1 = ad.get("address1")
    if _fold(stype + " " + sname) != _fold(ad.get("address1") or ""):
        new_a1 = ("%s %s Nr. %s" % (stype.title(), sname.title(), num)).strip()
    applied = dict(ad)
    applied["country"] = "Romania"
    if _fold(ccounty) != _fold(ad.get("province") or ""):
        applied["province"] = ccounty.title()
    if _fold(ccity) != _fold(ad.get("city") or ""):
        applied["city"] = ccity.title()
    applied["zip"] = czip
    applied["address1"] = new_a1
    detail = "%s, %s %s (%s)" % (new_a1, applied.get("city"), czip, applied.get("province"))
    if not apply:
        return "would-correct", applied, detail
    body = {"orderId": oid,
            "idempotencyKey": "aac-%s-%s-%s-%s" % (_digest(shop_domain, 8), oid,
                              _digest({k: _fold(str(v)) for k, v in ad.items()}, 12), _digest(applied, 12)),
            "appliedShippingAddress": applied,
            "expectedAddressHash": d.get("addressHash"), "expectedStatusHash": d.get("statusHash"),
            "expectedEvidenceHash": d.get("evidenceHash"), "agentClaimedConfidence": 0.96,
            "agentRationale": "Single canonical candidate, all core fields >=0.95, zip confirmed, house number preserved.",
            "modelName": "gigi-xconnector", "mcpClientId": "gigi-xconnector"}
    s, b = http("POST", XBASE + "/api/orders/ai-correct-address", xc.h, body)
    return ("corrected" if s == 200 else "error:%s" % s), applied, detail


def cmd_correct(a):
    """CRON (model order-created): comenzile fără AWB cu adresă WRONG/UNKNOWN →
    tag 'duplicata' = skip · corectabilă = aac ai-correct-address (cu --apply) · grea = triaj CS.
    Fără --apply = dry-run (arată ce ar face). Corecția face adresa VALID → gata de AWB (bulk dashboard)."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    shops = load_shops()
    toks = {t["prefix"]: t for t in load_shopify_tokens()}
    for sh in shops:
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        bad = [o for o in xc.orders(dfrom, dto) if not has_awb(o) and o.get("addressStatus") in ("WRONG", "UNKNOWN")]
        corrected = dup = cs = fresh = 0
        min_age = getattr(a, "min_age_hours", 0) or 0
        cs_rows = []
        print("═" * 74)
        print("  %s — %d fără AWB cu adresă WRONG/UNKNOWN (%dz)%s%s"
              % (sh["shopDomain"], len(bad), a.days, "" if a.apply else "  [DRY-RUN]",
                 "  [min-age %dh]" % min_age if min_age else ""))
        for o in bad:
            name = o.get("orderName")
            if min_age:
                age = order_age_hours(xc, o.get("orderId"))
                if age is not None and age < min_age:
                    fresh += 1
                    print("  %s  🕒 proaspăt (%.0fh < %dh) → las sweep-ul xConnector să ruleze, skip" % (name, age, min_age))
                    continue
            st, applied, detail = correct_address(xc, o, sh["shopDomain"], apply=False)
            if st == "would-correct":
                if "duplicata" in shopify_order_tags(name, toks):
                    dup += 1
                    print("  %s  ⏭  duplicata → skip" % name)
                    continue
                if a.apply:
                    st2, _, det2 = correct_address(xc, o, sh["shopDomain"], apply=True)
                    if st2 == "corrected":
                        corrected += 1
                        print("  %s  ✅ corectat → %s  (VALID, gata de AWB)" % (name, det2))
                    else:
                        cs += 1
                        print("  %s  ⚠ apply %s" % (name, st2))
                else:
                    corrected += 1
                    print("  %s  [ar corecta] → %s" % (name, detail))
            else:
                cs += 1
                cs_rows.append((name, o.get("addressStatus"), detail or ""))
        print("  → %s%d corectate · %d duplicata skip%s · %d → CS"
              % ("APLICAT: " if a.apply else "ar corecta: ", corrected, dup,
                 " · %d proaspete (skip)" % fresh if min_age else "", cs))
        if cs_rows:
            print("  Triaj CS (adrese grele — contact client):")
            for nm, status, why in cs_rows[:40]:
                print("    %-9s %-8s %s" % (nm, status, why))


def cmd_summary(shops, a):
    for sh in shops:
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        os_ = xc.orders(a.dfrom, a.dto)
        noawb = [o for o in os_ if not has_awb(o)]
        from collections import Counter
        st = Counter(o.get("addressStatus") for o in noawb)
        print("═" * 60)
        print("  %s — %d comenzi (fereastră %s→%s)" % (sh["shopDomain"], len(os_), a.dfrom, a.dto))
        print("  FĂRĂ AWB (nepornite): %d  |  status: %s" % (len(noawb), dict(st)))
        bad = sum(v for k, v in st.items() if k in ("WRONG", "UNKNOWN"))
        print("  → de confirmat/corectat înainte de AWB: %d (WRONG+UNKNOWN, fără AWB)" % bad)


def cmd_issues(shops, a):
    rows = []
    for sh in shops:
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        os_ = xc.orders(a.dfrom, a.dto)
        bad = [o for o in os_ if not has_awb(o) and o.get("addressStatus") in ("WRONG", "UNKNOWN")]
        if not a.json:
            print("═" * 78)
            print("  %s — %d comenzi nepornite cu adresă problemă (de confirmat înainte de AWB)" % (sh["shopDomain"], len(bad)))
            print("═" * 78)
        for o in bad:
            d = xc.by_id(o.get("orderId"))
            ad = d.get("shippingAddress") or {}
            cur = "%s, %s %s (%s)" % (ad.get("address1", ""), ad.get("city", ""), ad.get("zip", ""), ad.get("province", ""))
            ms = xc.match({"country": "Romania", "zipCode": ad.get("zip") or "", "county": ad.get("province") or "",
                           "city": ad.get("city") or "", "address1": ad.get("address1") or "", "address2": ad.get("address2") or ""})
            verd, sug = verdict(ms)
            rows.append({"shop": sh["shopDomain"], "order": o.get("orderName"), "orderId": o.get("orderId"),
                         "status": o.get("addressStatus"), "current": cur, "suggestion": sug, "verdict": verd})
            if not a.json:
                print("  #%-8s [%s]  %s" % (o.get("orderName"), o.get("addressStatus"), cur))
                print("       validator: %s" % (sug or "—"))
                print("       %s" % verd)
            time.sleep(0.2)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif rows:
        auto = sum(1 for r in rows if r["verdict"].startswith("✅"))
        print("\n  TOTAL: %d de confirmat | %d auto-corectabile | %d manual" % (len(rows), auto, len(rows) - auto))
        print("  (corecția propriu-zisă: skill-ul xConnector aac `/agentic-address-correction`, dry-run→--apply)")


def cmd_recheck(a):
    """READ: re-verifică addressStatus CURENT — care s-au auto-validat (VALID/PERFECT) vs încă WRONG/UNKNOWN.
    Validarea xConnector e async/batch, deci multe comenzi flagate se vindecă singure în câteva ore.
    Cu --order GT1,GT2 verifică lista dată; fără, ia coada curentă fără AWB cu adresă WRONG/UNKNOWN."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    names = [s.strip().lstrip("#") for s in (a.order or "").split(",") if s.strip()]
    for sh in load_shops():
        if skip_shop(sh, a):
            continue
        xc = XC(sh["apiKey"])
        idx = {o.get("orderName"): o for o in xc.orders(dfrom, dto)}
        if names:
            targets = [idx.get(n, {"orderName": n, "addressStatus": "?(în afara ferestrei)"}) for n in names]
        else:
            targets = [o for o in idx.values() if not has_awb(o) and o.get("addressStatus") in ("WRONG", "UNKNOWN")]
        healed = stuck = 0
        print("═" * 60)
        print("  %s — recheck %d comenzi (%dz)" % (sh["shopDomain"], len(targets), a.days))
        for o in targets:
            st = o.get("addressStatus")
            good = st in ("VALID", "PERFECT")
            awb = has_awb(o)
            if good:
                healed += 1
            elif st in ("WRONG", "UNKNOWN"):
                stuck += 1
            print("  %-9s %s%s" % (o.get("orderName"), ("✅ " if good else "… ") + str(st),
                                   "  (are AWB)" if awb else ""))
        print("  → %d auto-validate (VALID/PERFECT) · %d încă WRONG/UNKNOWN" % (healed, stuck))


# ── AWB direct prin API (/api/actions/*) — necesită ROLE_AUTOMATION + permisiuni write pe cheie ──
# Scriu bani/stare → DRY-RUN by default; POST real DOAR cu --apply. orderId trimis = Shopify order ID
# (câmpul `orderId` din /api/orders, NU merchantOrderId).
BILLING_TYPES = {"SMART_BILL", "SMARTBILL", "SMART_BILL_RO", "FACTURIS", "OBLIO", "FGO"}


def resolve_order(name, a, days=60):
    """Întoarce (shop, xc, order_obj) pt orderName. Caută în --shop dacă dat, altfel în TOATE magazinele."""
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    for sh in load_shops():
        if a.shop and sh["shopDomain"] != a.shop:
            continue
        xc = XC(sh["apiKey"])
        for o in xc.orders(dfrom, dto):
            if o.get("orderName") == name:
                return sh, xc, o
    return None, None, None


def awb_doc(o):
    for d in (o.get("documents") or []):
        if isinstance(d, dict) and d.get("documentType") == "SHIPPING_LABEL":
            return d
    return None


def doc_tracking(doc):
    """AWB number: câmpul direct dacă există, altfel din param `t=` al URL-ului etichetei."""
    if not doc:
        return None
    t = doc.get("trackingNumber") or doc.get("awbNumber")
    if t:
        return t
    m = re.search(r"[?&]t=([^&]+)", doc.get("url") or "")
    return m.group(1) if m else None


def courier_connectors(xc):
    return [c for c in xc.list_connectors() if c.get("active") and (c.get("type") or "").upper() not in BILLING_TYPES]


def pick_connector(xc, a):
    """(connector|None, lista_curieri). None = ambiguu (mai mulți curieri) → cere --connector."""
    cons = courier_connectors(xc)
    if getattr(a, "connector", None):
        try:
            cid = int(a.connector)
        except (TypeError, ValueError):
            print("  --connector trebuie să fie ID numeric (vezi `connectors`)."); return None, cons
        m = [c for c in xc.list_connectors() if c.get("id") == cid]
        return (m[0] if m else {"id": cid, "name": "?", "type": "?"}), cons
    if len(cons) == 1:
        return cons[0], cons
    # default curier ARONA = DPD Romania (preferat numele exact, apoi orice DPD non-SWAP, apoi orice DPD)
    dpd = ([c for c in cons if (c.get("name") or "") == "DPD Romania"]
           or [c for c in cons if (c.get("type") or "").upper() == "DPD" and "SWAP" not in (c.get("name") or "").upper()]
           or [c for c in cons if (c.get("type") or "").upper() == "DPD"])
    if len(dpd) == 1:
        return dpd[0], cons
    return None, cons


def _ask_connector(cons):
    print("  Mai mulți curieri activi — alege cu --connector ID:")
    for c in cons:
        print("    %-7s %-14s %s" % (c.get("id"), c.get("type"), c.get("name")))


def _err_text(s, d):
    """Text de eroare lizibil din răspunsul xConnector (ApiErrorResponse / errorMessage / brut)."""
    if isinstance(d, dict):
        return d.get("errorDescription") or d.get("errorMessage") or d.get("errorCode") or json.dumps(d, ensure_ascii=False)[:200]
    return "%s %s" % (s, str(d)[:200])


def _label_result(s, d):
    if s != 200 or not isinstance(d, dict):
        print("  ❌ eroare %s: %s" % (s, d if isinstance(d, str) else (d.get("errorDescription") or d)))
        return
    if not d.get("accepted"):
        print("  ❌ respins: %s" % d.get("errorMessage", d))
        return
    for L in (d.get("shippingLabels") or []):
        if L.get("success"):
            print("  ✅ AWB %s | %s | %s RON | %s" % (L.get("trackingNumber"), L.get("carrierName"),
                                                       L.get("price"), L.get("shippingLabelUrl")))
        else:
            print("  ❌ label: %s" % L.get("errorMessage"))


def cmd_connectors(a):
    for sh in load_shops():
        if a.shop and sh["shopDomain"] != a.shop:
            continue
        xc = XC(sh["apiKey"])
        cons = xc.list_connectors()
        print("═" * 60)
        print("  %s — %d connectori" % (sh["shopDomain"], len(cons)))
        for c in cons:
            kind = "factură" if (c.get("type") or "").upper() in BILLING_TYPES else "curier"
            print("    %-7s %-7s %-14s %-24s %s" % (c.get("id"), kind, c.get("type"), c.get("name"),
                                                    "activ" if c.get("active") else "INACTIV"))


def cmd_awb_make(a, _resolved=None):
    sh, xc, o = _resolved or resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită%s." % (a.order, " în %s" % a.shop if a.shop else " (căutat în toate)")); return
    if has_awb(o):
        print("  ⚠ %s ARE deja AWB (%s) — folosește awb-regen ca să-l refaci (anulează + reface)." % (a.order, doc_tracking(awb_doc(o))))
        return
    if not o.get("orderId"):
        print("  Comanda %s nu are orderId (Shopify) în xConnector." % a.order); return
    con, cons = pick_connector(xc, a)
    if not con:
        _ask_connector(cons); return
    body = {"orderId": o.get("orderId"), "connectorId": con["id"], "parcelCount": a.parcels,
            "parcelType": a.type, "notifyCustomer": bool(a.notify)}
    print("═" * 60)
    print("  AWB make · %s (%s)" % (a.order, sh["shopDomain"]))
    print("  curier: %s [%s] · colete: %d · tip: %s · notify: %s" % (con.get("name"), con.get("id"), a.parcels, a.type, bool(a.notify)))
    if not a.apply:
        print("  DRY-RUN — aș POST /api/actions/create-shipping-label:\n    %s" % json.dumps(body)); return
    _label_result(*xc.post("/api/actions/create-shipping-label", body))


def cmd_awb_void(a, _resolved=None):
    sh, xc, o = _resolved or resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    doc = awb_doc(o)
    if not doc and not a.apply:
        print("  %s nu are AWB (SHIPPING_LABEL) de anulat." % a.order); return
    cid = getattr(a, "connector", None) or (doc or {}).get("connectorId")
    body = {"orderId": o.get("orderId")}
    if cid:
        try:
            body["connectorId"] = int(cid)
        except (TypeError, ValueError):
            print("  --connector trebuie să fie ID numeric (vezi `connectors`)."); return
    print("  AWB void · %s · connector %s · tracking %s" % (a.order, cid, doc_tracking(doc)))
    if not a.apply:
        print("  DRY-RUN — aș POST /api/actions/cancel-shipping-label:\n    %s" % json.dumps(body)); return
    s, d = xc.post("/api/actions/cancel-shipping-label", body)
    print("  %s" % ("✅ anulat" if (s == 200 and isinstance(d, dict) and d.get("accepted")) else "❌ %s: %s" % (s, d)))


def cmd_awb_regen(a):
    """Anulează AWB-ul curent și îl reface cu alte condiții (parcelCount/parcelType/connector)."""
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    doc = awb_doc(o)
    con, cons = pick_connector(xc, a)
    if not con:
        _ask_connector(cons); return
    print("═" * 60)
    print("  REGEN AWB · %s (%s)" % (a.order, sh["shopDomain"]))
    print("  pas 1: anulez AWB curent (%s)" % (doc_tracking(doc) or "—"))
    print("  pas 2: creez nou — curier %s [%s] · %d colete · %s" % (con.get("name"), con.get("id"), a.parcels, a.type))
    if not a.apply:
        print("  DRY-RUN — fără --apply nu execut."); return
    if not o.get("orderId"):
        print("  Comanda %s nu are orderId (Shopify) în xConnector." % a.order); return
    if doc:
        vbody = {"orderId": o.get("orderId")}
        if doc.get("connectorId"):
            vbody["connectorId"] = doc["connectorId"]
        sv, dv = xc.post("/api/actions/cancel-shipping-label", vbody)
        voided = (sv == 200 and isinstance(dv, dict) and dv.get("accepted"))
        print("  void: %s" % ("✅" if voided else "❌ %s: %s" % (sv, dv)))
        if not voided:
            print("  ⛔ void eșuat → NU recreez (risc 2 AWB-uri). Rezolvă manual."); return
        time.sleep(1.5)
    mbody = {"orderId": o.get("orderId"), "connectorId": con["id"], "parcelCount": a.parcels,
             "parcelType": a.type, "notifyCustomer": bool(a.notify)}
    _label_result(*xc.post("/api/actions/create-shipping-label", mbody))


def cmd_awb_label(a):
    """Arată tracking + URL-ul de descărcare a etichetei (PDF) pt comanda dată."""
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    doc = awb_doc(o)
    if not doc:
        print("  %s nu are AWB." % a.order); return
    cid, trk = doc.get("connectorId"), doc_tracking(doc)
    url = doc.get("url") or doc.get("awbPdfUrl") or (
        XBASE + "/api/document/shipping-label?connectorId=%s&trackingNumber=%s" % (cid, urllib.parse.quote(str(trk or ""))))
    print("  %s (%s) · AWB %s · connector %s" % (a.order, sh["shopDomain"], trk, cid))
    print("  etichetă: %s" % url)


# ── Anulare comandă (xConnector cancel AWB + Shopify cancel order), cu gardă „plecată" ──
# „Plecată" = preluată de curier (status AWBprint, sursa de adevăr) → NU se poate anula.
# Neplecată + are AWB → anulez AWB apoi comanda. Fără AWB → doar comanda.
PLECATA = {"in_transit", "delivered", "back_to_sender", "returning_to_sender", "customer_pickup",
           "unsuccessful_delivery", "refused", "deferred_delivery", "redirected", "lost", "lost_in_transit"}
ALREADY_CANCELLED = {"cancelled"}


def awbprint_status(order_name):
    """aggregated_status din AWBprint (sursa de adevăr curier) pt orderName. None dacă lipsește DB/order/pg8000."""
    try:
        import pg8000.native
        from urllib.parse import urlparse, unquote
    except Exception:
        return None
    url = os.environ.get("DATABASE_URL_AWBPRINT") or ""
    if not url:
        try:
            url = subprocess.run(["uv", "run", KB, "secret-get", "DATABASE_URL_AWBPRINT"],
                                 capture_output=True, text=True, timeout=40).stdout.strip()
        except Exception:
            url = ""
    if not url.startswith("postgres"):
        return None
    u = urlparse(url)
    con = None
    try:
        con = pg8000.native.Connection(user=unquote(u.username or ""), password=unquote(u.password or ""),
                                       host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=True)
        rows = con.run("select aggregated_status from orders where order_number = :n order by id desc limit 1", n=order_name)
        return (rows[0][0] if rows else None)
    except Exception:
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def shopify_order_cancel(shop, token, order_gid, refund=False, restock=True, notify=False):
    """orderCancel (Shopify Admin). refund OFF by default — NU returna bani fără decizie explicită
    (pt COD inutil; pt comenzi plătite, refund real). Întoarce listă de erori (gol = OK)."""
    m = ('mutation{ orderCancel(orderId:"%s", reason:CUSTOMER, refund:%s, restock:%s, '
         'notifyCustomer:%s, staffNote:"anulare CS via xconnector"){ job{ id } orderCancelUserErrors{ message } } }'
         ) % (order_gid, "true" if refund else "false", "true" if restock else "false", "true" if notify else "false")
    d = shopify_gql(shop, token, m)
    oc = (d.get("data") or {}).get("orderCancel")
    if oc is None:
        return d.get("errors") or [{"message": "orderCancel a întors null (răspuns Shopify neașteptat)"}]
    return oc.get("orderCancelUserErrors") or d.get("errors")


def cmd_order_cancel(a):
    """Anulează o comandă: dacă e PLECATĂ (preluată de curier) → refuz; dacă e neplecată și are AWB →
    anulez AWB (xConnector) APOI comanda (Shopify); fără AWB → doar comanda. Dry-run by default."""
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    status = awbprint_status(a.order)
    awb = has_awb(o)
    trk = doc_tracking(awb_doc(o))
    print("═" * 60)
    print("  ANULARE comandă · %s (%s)" % (a.order, sh["shopDomain"]))
    print("  status livrare (AWBprint): %s · AWB: %s" % (status or "necunoscut", trk or "—"))
    if status in ALREADY_CANCELLED:
        print("  • comanda e deja anulată."); return
    if status in PLECATA and not getattr(a, "force", False):
        print("  ⛔ comanda a PLECAT (%s) — NU se poate anula. (forțează cu --force ca să încerci oricum;"
              " xConnector va da eroare dacă AWB-ul chiar a plecat)." % status); return
    if status is None and awb:
        print("  ℹ status de curier necunoscut — încerc anularea AWB; dacă a plecat, xConnector dă eroare și mă opresc.")
    # tokenul Shopify din MAGAZINUL găsit (nu din prefix) + comanda — verificate ÎNAINTE de orice scriere,
    # ca să nu rămână comanda activă cu AWB anulat.
    by_dom = {t.get("shopDomain"): t for t in load_shopify_tokens()}
    st = by_dom.get(sh["shopDomain"])
    if not st:
        print("  ⚠ fără token Shopify pt %s în SHOPIFY_ADMIN_TOKENS → nu pot anula comanda. Nu ating nimic." % sh["shopDomain"]); return
    do_refund = bool(getattr(a, "refund", False))
    plan = (["anulez AWB %s (xConnector)" % (trk or "—")] if awb else []) + \
           ["anulez comanda în Shopify%s" % (" + REFUND" if do_refund else " (fără refund)")]
    print("  plan: %s" % "  →  ".join(plan))
    if not a.apply:
        print("  DRY-RUN — fără --apply nu execut."); return
    node = find_order(st["shopDomain"], st["adminToken"], a.order)
    if not node or not node.get("id"):
        print("  Comanda %s negăsită în Shopify (%s) → nu ating nimic." % (a.order, sh["shopDomain"])); return
    if awb:
        body = {"orderId": o.get("orderId")}
        cid = (awb_doc(o) or {}).get("connectorId")
        if cid:
            body["connectorId"] = cid
        sv, dv = xc.post("/api/actions/cancel-shipping-label", body)
        if not (sv == 200 and isinstance(dv, dict) and dv.get("accepted")):
            err = _err_text(sv, dv)
            print("  ⛔ AWB-ul NU s-a putut anula — cel mai probabil coletul A PLECAT deja la curier.")
            print("     → NU anulez comanda. ANUNȚĂ CS/clientul: comanda a plecat, nu se mai poate anula.")
            print("     (eroare xConnector: %s)" % err)
            return
        print("  ✅ AWB anulat")
    errs = shopify_order_cancel(st["shopDomain"], st["adminToken"], node["id"],
                                refund=do_refund, restock=not getattr(a, "no_restock", False), notify=bool(a.notify))
    print("  %s" % ("✅ comandă anulată în Shopify" if not errs else "❌ Shopify: %s" % errs))


# ── CRON safety-net: comenzi open+unfulfilled > N min (Shopify Flow a ratat AWB-ul) → validează + fă AWB ──
def parse_iso(ts):
    import datetime
    try:
        return datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except Exception:
        return None


def shopify_unfulfilled(shop, token, since_date, max_pages=12):
    """Comenzi open + unfulfilled din ultimele zile: [(name, createdAt, financialStatus)]. None la auth fail."""
    out, cursor = [], None
    for _ in range(max_pages):
        after = ', after:"%s"' % cursor if cursor else ""
        q = ('query{ orders(first:250%s, query:"fulfillment_status:unfulfilled AND status:open AND created_at:>=%s"){ '
             'edges{ cursor node{ name createdAt displayFinancialStatus } } pageInfo{ hasNextPage } } }') % (after, since_date)
        d = shopify_gql(shop, token, q)
        edges = (((d.get("data") or {}).get("orders") or {}).get("edges")) or []
        if not edges and not out and d.get("errors"):
            return None
        for e in edges:
            n = e["node"]
            out.append((n.get("name"), n.get("createdAt"), n.get("displayFinancialStatus")))
        pi = (((d.get("data") or {}).get("orders") or {}).get("pageInfo")) or {}
        if not pi.get("hasNextPage"):
            break
        cursor = edges[-1]["cursor"]
    return out


def cmd_fulfill(a):
    """Safety-net peste Shopify Flow: comenzi open+unfulfilled mai vechi de --max-age-min (Flow a ratat AWB-ul) →
    VALID → fă AWB (create-shipping-label, DPD default); WRONG/UNKNOWN → corecție conservatoare → dacă devine
    VALID, fă AWB; altfel → CS. Sare cele cu AWB (Flow le-a făcut) + tag duplicata. Dry-run by default (--apply scrie).
    Exclude magazinele externe (validator RO) + recomand --exclude Grandia (Dragon Star ≠ DPD)."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    max_age = getattr(a, "max_age_min", 15) or 15
    dto = now.date().isoformat()
    dfrom = (now - datetime.timedelta(days=a.days)).date().isoformat()
    toks_dom = {t["shopDomain"]: t for t in load_shopify_tokens()}
    toks_pref = {t["prefix"]: t for t in load_shopify_tokens()}
    for sh in load_shops():
        if skip_shop(sh, a):
            continue
        print("═" * 72)
        st = toks_dom.get(sh["shopDomain"])
        if not st:
            print("  %s — fără token Shopify → skip" % sh["shopDomain"]); continue
        xc = XC(sh["apiKey"])
        xmap = {o.get("orderName"): o for o in xc.orders(dfrom, dto)}
        unf = shopify_unfulfilled(st["shopDomain"], st["adminToken"], dfrom)
        if unf is None:
            print("  %s — Shopify auth FAIL (OAuth-rotation?) → skip" % sh["shopDomain"]); continue
        con, cons = pick_connector(xc, a)
        ready = fixable = hard = had_awb = noxc = dup = made = fixed = failed = 0
        dup_rows = []
        for name, created, fin in unf:
            c = parse_iso(created)
            if not c or (now - c).total_seconds() / 60.0 <= max_age:
                continue
            o = xmap.get(name)
            if not o:
                noxc += 1; continue
            if has_awb(o):
                had_awb += 1; continue
            # tag duplicata → NU se expediază (e duplicat) → de REZOLVAT/anulat separat, niciodată AWB
            if "duplicata" in shopify_order_tags(name, toks_pref):
                dup += 1; dup_rows.append(name); continue
            ast = o.get("addressStatus")
            do_awb = ast in ("VALID", "PERFECT")
            if do_awb:
                ready += 1
            else:
                stt, _, _ = correct_address(xc, o, sh["shopDomain"], apply=False)
                if stt != "would-correct":
                    hard += 1; continue
                fixable += 1
                if a.apply:
                    st2, _, _ = correct_address(xc, o, sh["shopDomain"], apply=True)
                    do_awb = (st2 == "corrected")
                    if do_awb:
                        fixed += 1
            if a.apply and do_awb:
                if not con:
                    failed += 1; continue
                body = {"orderId": o.get("orderId"), "connectorId": con["id"], "parcelCount": 1,
                        "parcelType": "PARCEL", "notifyCustomer": bool(a.notify)}
                s, d = xc.post("/api/actions/create-shipping-label", body)
                ok = (s == 200 and isinstance(d, dict) and d.get("accepted")
                      and any(L.get("success") for L in (d.get("shippingLabels") or [])))
                made += 1 if ok else 0
                failed += 0 if ok else 1
        print("  %s — unfulfilled >%dmin: %d gata(VALID) + %d corectabile + %d grele→CS  (aveau AWB: %d, fără xc: %d)"
              % (sh["shopDomain"], max_age, ready, fixable, hard, had_awb, noxc))
        if a.apply:
            print("  → APLICAT: AWB făcute %d (din care %d după corecție) · eșuate %d" % (made, fixed, failed))
        else:
            print("  → [DRY-RUN] aș face AWB la %d (gata) + până la %d (după corecție) · %d → CS" % (ready, fixable, hard))


# ── FACTURI prin API (/api/actions/*-invoice) — mirror AWB: make / cancel / storno(revert) / regen / doc ──
# Connector de facturare = tip SMART_BILL (din connectors). Dry-run by default; POST real DOAR cu --apply.
def billing_connectors(xc):
    return [c for c in xc.list_connectors() if c.get("active") and (c.get("type") or "").upper() in BILLING_TYPES]


def pick_billing(xc, a):
    """(connector_facturare|None, lista). None = ambiguu/absent → cere --connector."""
    bills = billing_connectors(xc)
    if getattr(a, "connector", None):
        try:
            cid = int(a.connector)
        except (TypeError, ValueError):
            print("  --connector trebuie să fie ID numeric (vezi `connectors`)."); return None, bills
        m = [c for c in xc.list_connectors() if c.get("id") == cid]
        if m and (m[0].get("type") or "").upper() not in BILLING_TYPES:
            print("  ⚠ connectorul %s (%s) NU e de facturare — pt facturi alege un connector SMART_BILL (vezi `connectors`)." % (cid, m[0].get("type")))
            return None, bills
        return (m[0] if m else {"id": cid, "name": "?", "type": "?"}), bills
    if len(bills) == 1:
        return bills[0], bills
    return None, bills


def inv_doc(o):
    for d in (o.get("documents") or []):
        if isinstance(d, dict) and d.get("documentType") == "INVOICE":
            return d
    return None


def _invoice_result(s, d):
    if s != 200 or not isinstance(d, dict):
        print("  ❌ eroare: %s" % _err_text(s, d)); return
    if not d.get("accepted"):
        print("  ❌ respins: %s" % _err_text(s, d)); return
    if not (d.get("invoices") or []):
        print("  ✅ acceptat (fără detaliu factură în răspuns)"); return
    for inv in (d.get("invoices") or []):
        if inv.get("success"):
            print("  ✅ %sfactură %s %s" % ("STORNO " if inv.get("storno") else "",
                                            inv.get("invoiceSerie") or "", inv.get("invoiceNumber") or ""))
        else:
            print("  ❌ factură: %s" % inv.get("errorMessage"))


def _inv_resolve(a):
    rid = getattr(a, "refund_id", None)
    if rid is not None:
        try:
            int(rid)
        except (TypeError, ValueError):
            print("  --refund-id trebuie să fie numeric (Shopify refund ID) — abort, ca să nu fac storno total din greșeală."); return None
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită%s." % (a.order, " în %s" % a.shop if a.shop else " (căutat în toate)")); return None
    if not o.get("orderId"):
        print("  Comanda %s nu are orderId (Shopify) în xConnector." % a.order); return None
    con, bills = pick_billing(xc, a)
    if not con:
        print("  Connector de facturare ambiguu/absent — alege --connector ID:")
        for c in bills:
            print("    %-7s %-14s %s" % (c.get("id"), c.get("type"), c.get("name")))
        return None
    return sh, xc, o, con


def _inv_body(o, con, a):
    body = {"orderId": o.get("orderId"), "connectorId": con["id"]}
    if getattr(a, "lang", None):
        body["languageCode"] = a.lang
    rid = getattr(a, "refund_id", None)
    if rid:
        try:
            body["refundId"] = int(rid)
        except (TypeError, ValueError):
            pass
    return body


def cmd_inv_make(a):
    r = _inv_resolve(a)
    if not r:
        return
    sh, xc, o, con = r
    if inv_doc(o):
        print("  ⚠ %s are deja factură — folosește inv-regen ca să o refaci (anulează + creează)." % a.order); return
    body = _inv_body(o, con, a)
    print("═" * 60)
    print("  FACTURĂ make · %s (%s) · %s [%s]" % (a.order, sh["shopDomain"], con.get("name"), con.get("id")))
    if not a.apply:
        print("  DRY-RUN — aș POST /api/actions/create-invoice:\n    %s" % json.dumps(body)); return
    _invoice_result(*xc.post("/api/actions/create-invoice", body))


def _inv_simple(a, endpoint, label):
    r = _inv_resolve(a)
    if not r:
        return
    sh, xc, o, con = r
    body = _inv_body(o, con, a)
    print("═" * 60)
    print("  %s · %s (%s) · %s [%s]" % (label, a.order, sh["shopDomain"], con.get("name"), con.get("id")))
    if not a.apply:
        print("  DRY-RUN — aș POST %s:\n    %s" % (endpoint, json.dumps(body))); return
    _invoice_result(*xc.post(endpoint, body))


def cmd_inv_cancel(a):
    _inv_simple(a, "/api/actions/cancel-invoice", "FACTURĂ cancel")


def cmd_inv_storno(a):
    _inv_simple(a, "/api/actions/revert-invoice", "FACTURĂ STORNO")


def cmd_inv_regen(a):
    """Anulează factura curentă și o reface (ca awb-regen). Create gardat pe succesul cancel-ului."""
    r = _inv_resolve(a)
    if not r:
        return
    sh, xc, o, con = r
    print("═" * 60)
    print("  REGEN FACTURĂ · %s (%s) · %s [%s]" % (a.order, sh["shopDomain"], con.get("name"), con.get("id")))
    print("  pas 1: anulez factura curentă · pas 2: creez una nouă")
    if not a.apply:
        print("  DRY-RUN — fără --apply nu execut."); return
    cv = xc.post("/api/actions/cancel-invoice", {"orderId": o.get("orderId"), "connectorId": con["id"]})
    ok = (cv[0] == 200 and isinstance(cv[1], dict) and cv[1].get("accepted"))
    print("  cancel: %s" % ("✅" if ok else "❌ %s: %s" % (cv[0], _err_text(*cv))))
    if not ok:
        print("  ⛔ anulare factură eșuată → NU recreez."); return
    time.sleep(1)
    _invoice_result(*xc.post("/api/actions/create-invoice", _inv_body(o, con, a)))


def cmd_inv_doc(a):
    sh, xc, o = resolve_order(a.order, a, a.days)
    if not o:
        print("Comanda %s negăsită." % a.order); return
    d = inv_doc(o)
    if not d:
        print("  %s nu are factură (document INVOICE)." % a.order); return
    print("  %s (%s) · factură %s" % (a.order, sh["shopDomain"], d.get("name") or ""))
    print("  PDF: %s" % (d.get("url") or "—"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["summary", "address-issues", "recheck", "correct", "connectors", "fulfill",
                                    "awb-make", "awb-void", "awb-regen", "awb-label", "order-cancel",
                                    "inv-make", "inv-cancel", "inv-storno", "inv-regen", "inv-doc",
                                    "awb-create", "awb-cancel", "awb-hold", "awb-auto"])
    ap.add_argument("--shop"); ap.add_argument("--order"); ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-age-hours", type=int, default=0, dest="min_age_hours",
                    help="correct: sare comenzile mai noi de N ore (validarea xConnector e async/batch — multe se auto-validează). 0 = oprit.")
    ap.add_argument("--exclude", default="",
                    help="domenii myshopify de SĂRIT (separate prin virgulă) — ex magazinele externe (Bonhaus CZ/PL/BG) pe care validatorul RO nu le acoperă.")
    ap.add_argument("--connector", help="awb-make/void/regen: connectorId curier (din `connectors`). Obligatoriu dacă sunt mai mulți curieri activi.")
    ap.add_argument("--parcels", type=int, default=1, help="awb-make/regen: număr de colete (parcelCount). Default 1.")
    ap.add_argument("--type", default="PARCEL", help="awb-make/regen: parcelType (PARCEL/ENVELOPE). Default PARCEL.")
    ap.add_argument("--notify", action="store_true", help="awb-make/regen/order-cancel: notifyCustomer.")
    ap.add_argument("--force", action="store_true", help="order-cancel: încearcă anularea chiar dacă statusul de curier zice PLECAT (xConnector dă eroare dacă chiar a plecat).")
    ap.add_argument("--refund", action="store_true", help="order-cancel: returnează banii la anulare (OFF by default — COD n-are nevoie; comenzi plătite = decizie explicită).")
    ap.add_argument("--no-restock", action="store_true", dest="no_restock", help="order-cancel: NU repune stocul la anulare (restock ON by default).")
    ap.add_argument("--max-age-min", type=int, default=15, dest="max_age_min", help="fulfill: vârsta minimă în minute a comenzii unfulfilled ca să-i facă AWB (default 15).")
    ap.add_argument("--lang", help="inv-make/regen: languageCode pt factură (ex ro/en).")
    ap.add_argument("--refund-id", dest="refund_id", help="inv-storno: Shopify refund ID (storno parțial pe un refund).")
    ap.add_argument("--correct", action="store_true", help="awb-auto: corectează conservator adresele proaste (xConnector ai-correct-address)")
    a = ap.parse_args()
    if a.cmd in ("awb-make", "awb-void", "awb-regen", "awb-label", "order-cancel",
                 "inv-make", "inv-cancel", "inv-storno", "inv-regen", "inv-doc"):
        if not a.order:
            print("Dă --order (ex: --order GT44004)."); sys.exit(1)
        {"awb-make": cmd_awb_make, "awb-void": cmd_awb_void, "awb-regen": cmd_awb_regen,
         "awb-label": cmd_awb_label, "order-cancel": cmd_order_cancel,
         "inv-make": cmd_inv_make, "inv-cancel": cmd_inv_cancel, "inv-storno": cmd_inv_storno,
         "inv-regen": cmd_inv_regen, "inv-doc": cmd_inv_doc}[a.cmd](a)
        return
    if a.cmd == "connectors":
        cmd_connectors(a); return
    if a.cmd in ("awb-create", "awb-cancel", "awb-hold"):
        if not a.order:
            print("Dă --order (ex: --order GT44004)."); sys.exit(1)
        cmd_awb(a); return
    if a.cmd == "awb-auto":
        cmd_awb_auto(a); return
    if a.cmd == "correct":
        cmd_correct(a); return
    if a.cmd == "fulfill":
        cmd_fulfill(a); return
    if a.cmd == "recheck":
        cmd_recheck(a); return
    import datetime
    a.dto = datetime.date.today().isoformat()
    a.dfrom = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    shops = load_shops()
    if not shops:
        print("Nicio configurație xConnector (KB XCONNECTOR_SHOPS sau ~/.aac/input.json)."); sys.exit(1)
    if a.cmd == "summary":
        cmd_summary(shops, a)
    else:
        cmd_issues(shops, a)


if __name__ == "__main__":
    main()
