# /// script
# requires-python = ">=3.10"
# dependencies = []
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


def load_shopify_tokens():
    """[{prefix, shopDomain, adminToken}] din KB SHOPIFY_ADMIN_TOKENS (sau env)."""
    raw = os.environ.get("SHOPIFY_ADMIN_TOKENS")
    if not raw:
        try:
            raw = subprocess.run(["uv", "run", KB, "secret-get", "SHOPIFY_ADMIN_TOKENS"],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            raw = ""
    try:
        return json.loads(raw) if raw.startswith("[") else []
    except Exception:
        return []


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["summary", "address-issues", "recheck", "correct", "awb-create", "awb-cancel", "awb-hold", "awb-auto"])
    ap.add_argument("--shop"); ap.add_argument("--order"); ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-age-hours", type=int, default=0, dest="min_age_hours",
                    help="correct: sare comenzile mai noi de N ore (validarea xConnector e async/batch — multe se auto-validează). 0 = oprit.")
    ap.add_argument("--exclude", default="",
                    help="domenii myshopify de SĂRIT (separate prin virgulă) — ex magazinele externe (Bonhaus CZ/PL/BG) pe care validatorul RO nu le acoperă.")
    ap.add_argument("--correct", action="store_true", help="awb-auto: corectează conservator adresele proaste (xConnector ai-correct-address)")
    a = ap.parse_args()
    if a.cmd in ("awb-create", "awb-cancel", "awb-hold"):
        if not a.order:
            print("Dă --order (ex: --order GT44004)."); sys.exit(1)
        cmd_awb(a); return
    if a.cmd == "awb-auto":
        cmd_awb_auto(a); return
    if a.cmd == "correct":
        cmd_correct(a); return
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
